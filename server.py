#!/usr/bin/env python3
"""Erol - screenshot issue logger. Stdlib-only local server.

Watches a screenshots folder for new PNGs, serves a single-page app where the
user adds a description to each, and exports a handoff bundle (images + markdown)
for pasting/uploading into Codex, Claude, or Hermes.

Endpoints:
  GET  /                      index.html SPA
  GET  /api/images            list of screenshot files currently in the folder
  GET  /img/<name>            serve one screenshot image
  GET  /api/desc              current saved descriptions {filename: text}
  POST /api/desc              save/update a description {filename, text}
  GET  /api/status            server + folder config (for the UI header)
  POST /api/actions           archive the current described screenshots (set aside)
  POST /api/export            build the handoff bundle
"""
import json
import os
import shutil
import time
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, unquote_plus

ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(ROOT, "index.html")

# --- config ------------------------------------------------------------------
# Default watched folder. Cross-platform default: a `screenshots/` folder next to
# the project, created on demand. Point `config.json` at your own screenshot
# autosave folder to watch that instead (see config.example.json).
SCREENSHOTS_DIR = os.path.join(ROOT, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
# Override with a local config.json if the folder lives elsewhere / differs.
CONFIG_PATH = os.path.join(ROOT, "config.json")
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        SCREENSHOTS_DIR = cfg.get("screenshots_dir", SCREENSHOTS_DIR)
    except Exception as e:
        print("[config] could not read config.json:", e)

CAPTURE_DIR = os.path.join(ROOT, "captures")   # pasted images the user drops in
ARCHIVE_DIR = os.path.join(ROOT, "archive")     # set-aside processed screenshots
EXPORT_DIR = os.path.join(ROOT, "export")       # handoff bundles
STATE_PATH = os.path.join(ROOT, "descriptions.json")
for d in (CAPTURE_DIR, ARCHIVE_DIR, EXPORT_DIR):
    os.makedirs(d, exist_ok=True)

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

# --- state -------------------------------------------------------------------
_lock = threading.Lock()

_raw_state = {}
if os.path.exists(STATE_PATH):
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            _raw_state = json.load(f)
    except Exception:
        _raw_state = {}

def _load_state():
    """Return {"descriptions": {name: text}, "archived": [names], "deleted": [names]}."""
    # Tolerate old/broken shapes that stored the wrapper as the inner map.
    d = _raw_state.get("descriptions", {})
    if not isinstance(d, dict) or "descriptions" in d:  # wrapped-before bug
        d = {}
        for k, v in _raw_state.items():
            if k in ("descriptions", "archived", "deleted"):
                continue
            if isinstance(v, str):
                d[k] = v
    return {
        "descriptions": d,
        "archived": _raw_state.get("archived", []),
        "deleted": _raw_state.get("deleted", []),
    }

def _save_state():
    payload = {
        "descriptions": DESCRIPTIONS,
        "archived": sorted(ARCHIVED),
        "deleted": sorted(DELETED),
    }
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

_st = _load_state()
DESCRIPTIONS = _st["descriptions"]   # {filename: text}
ARCHIVED = set(_st["archived"])      # filenames set aside (recoverable)
DELETED = set(_st["deleted"])        # filenames removed from Erol for good


def list_images():
    """Return all non-archived image files in the watched + capture folders."""
    out = {}
    for base, label in ((SCREENSHOTS_DIR, "screenshot"), (CAPTURE_DIR, "paste")):
        if not os.path.isdir(base):
            continue
        with os.scandir(base) as it:
            for e in it:
                if not e.is_file():
                    continue
                name = e.name
                if not e.name.lower().endswith(IMAGE_EXT):
                    continue
                if name in ARCHIVED or name in DELETED:
                    continue
                try:
                    mtime = e.stat().st_mtime
                except OSError:
                    mtime = 0
                out[name] = {
                    "name": name,
                    "src": label + ":" + name,
                    "url": "/img/" + label + "/" + name.replace("#", "%23"),
                    "mtime": mtime,
                    "size": e.stat().st_size,
                }
    return out


def resolve_path(src):
    """src is 'screenshot:<name>' or 'paste:<name>'. Return absolute path or None."""
    try:
        label, name = src.split(":", 1)
    except ValueError:
        return None
    if "\x00" in name or ".." in name or "/" in name or "\\" in name:
        return None
    if label == "screenshot":
        return os.path.join(SCREENSHOTS_DIR, name) if os.path.isfile(os.path.join(SCREENSHOTS_DIR, name)) else None
    if label == "paste":
        return os.path.join(CAPTURE_DIR, name) if os.path.isfile(os.path.join(CAPTURE_DIR, name)) else None
    return None


# --- handler -----------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Erol/0.1"

    def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # --- GET -----------------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path in ("/", "/index.html"):
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif path == "/api/images":
                with _lock:
                    imgs = list_images()
                items = sorted(imgs.values(), key=lambda i: -i["mtime"])
                for it in items:  # attach current description
                    it["desc"] = DESCRIPTIONS.get(it["name"], "")
                self._send(200, items)
            elif path == "/api/desc":
                with _lock:
                    self._send(200, {"descriptions": DESCRIPTIONS,
                                     "archived": sorted(ARCHIVED)})
            elif path == "/api/status":
                self._send(200, {
                    "screenshots_dir": SCREENSHOTS_DIR,
                    "capture_dir": CAPTURE_DIR,
                    "export_dir": EXPORT_DIR,
                    "watched_exists": os.path.isdir(SCREENSHOTS_DIR),
                })
            elif path in ("/favicon.png", "/logo.png"):
                # Serve the Erol brand image (wordmark) for the page + favicon.
                img = os.path.join(ROOT, path[1:])
                if not os.path.isfile(img):
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                try:
                    with open(img, "rb") as f:
                        self._send(200, f.read(), "image/png")
                except OSError as e:
                    self._send(500, {"ok": False, "error": str(e)})
            elif path in ("/manifest.webmanifest", "/sw.js"):
                # PWA manifest + service worker (must be served with no-store so
                # updates land immediately, and correct content-type).
                ctype = "application/manifest+json" if path.endswith(".webmanifest") else "application/javascript"
                fp = os.path.join(ROOT, path[1:])
                if not os.path.isfile(fp):
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                try:
                    with open(fp, "rb") as f:
                        self._send(200, f.read(), ctype)
                except OSError as e:
                    self._send(500, {"ok": False, "error": str(e)})
            elif path.startswith("/assets/"):
                # Static PWA assets (icons) — allowlist by basename, no traversal.
                name = path[len("/assets/"):]
                if "/" in name or "\\" in name or ".." in name or name not in (
                    "icon-192.png", "icon-512.png", "favicon.png", "logo.png"):
                    self._send(403, {"ok": False, "error": "forbidden"})
                    return
                fp = os.path.join(ROOT, name)
                if not os.path.isfile(fp):
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                try:
                    with open(fp, "rb") as f:
                        self._send(200, f.read(), "image/png")
                except OSError as e:
                    self._send(500, {"ok": False, "error": str(e)})
            elif path == "/api/captures":
                # images already pasted into captures/ (via /save)
                with _lock:
                    if os.path.isdir(CAPTURE_DIR):
                        names = sorted(
                            n for n in os.listdir(CAPTURE_DIR)
                            if n.lower().endswith(IMAGE_EXT) and n not in ARCHIVED
                        )
                    else:
                        names = []
                self._send(200, names)
            elif path == "/api/open":
                # Open a folder (the export bundle dir) in the OS file explorer,
                # so the user can drag-and-drop it into Codex/Claude/Hermes.
                qs = urlparse(self.path).query
                params = {}
                if qs:
                    for kv in qs.split("&"):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            params[k] = unquote_plus(v)  # + -> space too
                target = params.get("path") or EXPORT_DIR
                target = os.path.abspath(target)
                # Only allow opening paths inside the export dir (safety).
                if not target.startswith(os.path.abspath(EXPORT_DIR)):
                    self._send(403, {"ok": False, "error": "not allowed"})
                    return
                if not os.path.isdir(target):
                    self._send(404, {"ok": False, "error": "folder not found"})
                    return
                try:
                    if os.name == "nt":
                        os.startfile(target)  # Windows: opens Explorer
                    else:
                        import subprocess
                        subprocess.Popen(["xdg-open", target])
                    self._send(200, {"ok": True, "opened": target})
                except Exception as e:
                    self._send(500, {"ok": False, "error": str(e)})
            elif path.startswith("/img/"):
                self._serve_img(path[5:])
            elif path.startswith("/capture/") or path.startswith("/paste/"):
                # served images live in the watched dir / captures dir
                self._serve_img(path[1:])
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def _serve_img(self, sub):
        # sub like "screenshot/<name>" or "paste/<name>" (slashes from the URL).
        # Convert to canonical "label:name" for resolve_path.
        if "/" in sub:
            label, _, name = sub.partition("/")
            src = label + ":" + name
        else:
            src = sub
        p = resolve_path(src)
        if not p or not os.path.isfile(p):
            self._send(404, {"ok": False, "error": "image not found"})
            return
        try:
            with open(p, "rb") as f:
                self._send(200, f.read(), self._guess_ctype(p))
        except OSError as e:
            self._send(500, {"ok": False, "error": str(e)})

    @staticmethod
    def _guess_ctype(p):
        ext = os.path.splitext(p)[1].lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }.get(ext, "application/octet-stream")

    # --- POST ----------------------------------------------------------------
    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/desc":
                data = self._read_json()
                fn = data.get("filename", "")
                text = data.get("text", "")
                with _lock:
                    if text.strip():
                        DESCRIPTIONS[fn] = text
                    else:
                        DESCRIPTIONS.pop(fn, None)
                    _save_state()
                self._send(200, {"ok": True})

            elif path == "/api/export":
                data = self._read_json()
                items = data.get("items", [])  # [{src, desc}] in current order
                publish = bool(data.get("publish", True))  # default: authorize deploy
                self._send(200, self._do_export(items, publish=publish))

            elif path == "/api/actions":
                data = self._read_json()
                action = data.get("action")
                if action == "archive":
                    for src in data.get("srcs", []):
                        ARCHIVED.add(src.split(":", 1)[-1])
                        DELETED.discard(src.split(":", 1)[-1])
                elif action == "clear_archive":
                    for src in data.get("srcs", []):
                        ARCHIVED.discard(src.split(":", 1)[-1])
                elif action == "delete":
                    # Permanent: removes the card from Erol for good (the actual
                    # file in the screenshots/captures folder is NOT touched).
                    for src in data.get("srcs", []):
                        name = src.split(":", 1)[-1]
                        DELETED.add(name)
                        ARCHIVED.discard(name)
                        DESCRIPTIONS.pop(name, None)
                elif action == "clear_all":
                    # Wipe every currently-shown card so the page starts fresh.
                    # Only the items visible right now are cleared; files on disk
                    # are untouched (same semantics as per-item delete).
                    for it in list_images().values():
                        name = it["name"]
                        DELETED.add(name)
                        ARCHIVED.discard(name)
                        DESCRIPTIONS.pop(name, None)
                else:
                    self._send(400, {"ok": False, "error": "unknown action"})
                    return
                _save_state()
                self._send(200, {"ok": True})

            elif path == "/save":
                # Paste fallback: accept a base64 data URL and store it in captures/
                data = self._read_json()
                data_url = data.get("image", "")
                if not data_url.startswith("data:image/"):
                    self._send(400, {"ok": False, "error": "bad payload"})
                    return
                import base64
                meta, _, b64 = data_url.partition(",")
                mime = meta.split(";")[0].split("/")[-1] or "png"
                ext = {"jpeg": "jpg"}.get(mime, mime)
                img_bytes = base64.b64decode(b64)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                fname = os.path.join(CAPTURE_DIR, f"paste_{stamp}.{ext}")
                with open(fname, "wb") as f:
                    f.write(img_bytes)
                self._send(200, {"ok": True, "file": os.path.basename(fname)})

            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def _do_export(self, items, publish=True):
        """Build a handoff bundle in export/handoff <date> <time>/.
        Returns (dict) with files list + markdown path.
        publish controls whether we add explicit authorization to deploy."""
        if not items:
            return {"ok": False, "error": "nothing to export"}
        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d %H-%M-%S")
        bundle_dir = os.path.join(EXPORT_DIR, f"handoff {stamp}")
        os.makedirs(bundle_dir, exist_ok=True)
        images_dir = os.path.join(bundle_dir, "images")
        os.makedirs(images_dir, exist_ok=True)

        md_lines = []
        md_lines.append("# Erol — Screenshot issue handoff (Codex / Claude / Hermes)\n")
        md_lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
        md_lines.append(f"Total items: {len(items)}\n")
        if publish:
            md_lines.append(
                "\n## ⚠️ Publishing authorization\n"
                "The requester explicitly authorizes you to apply the fixes described "
                "below AND to publish / deploy / upload the resulting changes to the "
                "relevant live environment (e.g. Canvasroom / production site). "
                "Proceed to publish once the build passes validation — do NOT pause to "
                "ask for permission to upload.\n"
            )
        md_lines.append("\n---\n")

        files_out = []
        for i, item in enumerate(items, 1):
            if not isinstance(item, dict):
                md_lines.append(f"\n## {i}. (INVALID ITEM) — skipped\n")
                continue
            src = item.get("src", "")
            desc = (item.get("desc") or "").strip()
            p = resolve_path(src)
            if not p or not os.path.isfile(p):
                md_lines.append(f"\n## {i}. (MISSING FILE) {src}\n")
                md_lines.append(f"- Source: `{src}` — **not found on disk**\n")
                continue
            ext = os.path.splitext(p)[1].lower() or ".png"
            img_name = f"{i:02d}{ext}"
            img_dst = os.path.join(images_dir, img_name)
            shutil.copy2(p, img_dst)
            files_out.append(img_name)
            md_lines.append(f"\n## {i}. {img_name}\n")
            md_lines.append(f"- Source: `{src}`\n")
            if desc:
                md_lines.append(f"- Description: {desc}\n")
            else:
                md_lines.append(f"- Description: *(no description provided)*\n")

        md_lines.append("\n---\n")
        md_lines.append(
            "### Instructions for the AI\n"
            "Review each screenshot and its description in order. Fix or improve "
            "each described issue. Reply with a summary of changes by item number.\n"
        )
        md_path = os.path.join(bundle_dir, "HANDOFF.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))

        return {
            "ok": True,
            "bundle_dir": bundle_dir,
            "bundle_name": os.path.basename(bundle_dir),
            "markdown": "HANDOFF.md",
            "images": files_out,
            "count": len(items),
        }

    def log_message(self, fmt, *args):
        print("[erol]", fmt % args, flush=True)


# --- watcher -----------------------------------------------------------------
def watch_loop(stop):
    """Poll the watched folder; when new images appear, print a note (the UI
    polls /api/images, so no push is strictly required)."""
    seen = set()
    while not stop.is_set():
        if os.path.isdir(SCREENSHOTS_DIR):
            with os.scandir(SCREENSHOTS_DIR) as it:
                try:
                    for e in it:
                        if e.is_file() and e.name.lower().endswith(IMAGE_EXT):
                            seen.add(e.name)
                except OSError:
                    pass
        time.sleep(1.5)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Erol screenshot issue logger")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (use 0.0.0.0 for LAN access)")
    ap.add_argument("--port", type=int, default=8000,
                    help="listen port (default 8000)")
    args = ap.parse_args()
    port = args.port
    srv = ThreadingHTTPServer((args.host, port), Handler)
    stop = threading.Event()
    t = threading.Thread(target=watch_loop, args=(stop,), daemon=True)
    t.start()
    print("=" * 60)
    print("Erol — screenshot issue logger")
    print("  Running at  http://%s:%d" % (args.host, port))
    print("  Watching   %s" % SCREENSHOTS_DIR)
    print("  Exports to %s" % EXPORT_DIR)
    print("  Ctrl+C to stop.")
    print("=" * 60, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.shutdown()


if __name__ == "__main__":
    main()
