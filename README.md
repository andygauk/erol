# Erol

**Erol** is a free, open-source visual bug-tracker for people who screenshot issues
instead of typing them. Take screenshots of whatever's broken or could be improved,
drop a one-line note under each, and Erol packages them into a single **handoff
folder** (`HANDOFF.md` + images) you can drop straight into an AI coding agent
(Codex, Claude, Hermes, …) — or keep for yourself.

It runs **entirely on your own machine** (no cloud, no account, no data leaves your
computer). A hosted variant is available for teams who want a shared web drop.

![Erol](logo.png)

---

## How it works

1. **Take a screenshot.** Erol watches your screenshots folder (or you can paste with
   `Ctrl+V`), and each new image appears as a card.
2. **Describe the issue.** Type a short note under each card. Works for as many
   screenshots as you like, all on one screen.
3. **Hand off.** Click **→ Hand off to AI**. Erol bundles everything — the screenshots
   and a `HANDOFF.md` that lists each image with its description — into a folder named
   after the export time, and opens it in Explorer. Drag the folder into your AI agent
   (or a colleague, or a ticket) and you're done. The handoff even includes an option to
   explicitly authorize the agent to **publish** the resulting changes, so it won't get
   blocked waiting for permission at the deploy step.

Per-card actions: **Set aside** (hide without deleting the file), **Delete** (hide
permanently, file on disk untouched), **Clear all** (start fresh). It's a PWA too, so
you can install it as a standalone window.

---

## Local quick start (the main path)

Requires **Python 3.8+**. No dependencies.

```bash
git clone https://github.com/andygauk/erol.git
cd erol
python server.py
```

Open **http://localhost:8000/** (or the LAN IP your machine shows). Erol watches a
`screenshots/` folder next to the project by default.

### Point it at your real screenshot folder

Copy the example config and edit the path:

```bash
cp config.example.json config.json
```

```json
{ "screenshots_dir": "C:\\Users\\you\\Pictures\\Screenshots" }
```

Erol will watch that folder and auto-signal every new screenshot it finds.

### LAN access (another device / your phone)

```bash
python server.py --host 0.0.0.0 --port 8005
```

Then open `http://<your-PC-IP>:8005/` from any device on the same Wi-Fi. (The default
`127.0.0.1` binding is local-only on purpose; use `0.0.0.0` to expose it on the network.)

---

## Hosted mode (optional, for teams)

A Cloudflare Worker backend (`worker/index.js`, using D1 for metadata and R2 for images)
lets Erol run as a shareable web app where people paste/upload screenshots. To deploy
your own instance you need your own Cloudflare D1 + R2 bindings:

- Run `build.ps1` (produces `dist/`) or wire `worker/index.js` directly.
- Bind the Worker to a D1 database (`DB`) and an R2 bucket (`IMAGES`). `worker/index.js`
  is the whole backend — it's self-contained and uses only those two bindings.
- The browser UI is the same single `index.html`, so paste/upload works but (being web)
  it cannot read anyone's local screenshot folder — that's the local mode's job.

> The hosted mode is entirely optional. The local mode is the point of Erol.

---

## Where things are

| Path | Purpose |
|---|---|
| `server.py` | Local server (Python stdlib, no deps). Watches the folder, serves the UI, builds handoff folders in `export/`. |
| `index.html` | The whole UI (single file). Works in both local and hosted mode. |
| `worker/index.js` | Optional Cloudflare Worker backend for the hosted mode. |
| `manifest.webmanifest`, `sw.js`, `icon-*.png`, `favicon.png`, `logo.png` | PWA manifest, service worker, and icons — makes Erol installable as an app. |
| `config.json` | **Your** screenshot-folder override (gitignored — never commit it). |

Generated material (archived/cleared cards, export bundles, pasted images, saved
descriptions) lives in `archive/`, `export/`, `captures/`, and `descriptions.json` —
all gitignored so they never pollute a shared repo.

---

## Development

```bash
python server.py            # run locally (127.0.0.1:8000)
python server.py --host 0.0.0.0 --port 8005   # LAN-reachable
```

Active dev port is `8005` when bound to the LAN. PRs welcome.

---

## License

[MIT](LICENSE). Free to use, modify, and share.
