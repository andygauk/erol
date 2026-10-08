// Erol service worker — enables PWA install + offline app-launch.
// Strategy: cache the app shell (/) for offline launch. NEVER cache /api/*,
// images from the screenshots folder, or /logo|/favicon (those must be live).
const SHELL = ["/", "/manifest.webmanifest"];
const CACHE = "erol-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Always go to the network for API + live image data.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/img/")) {
    return; // let the browser handle it normally
  }

  // For navigation/shell requests: try network, fall back to cached shell.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/"))
    );
    return;
  }

  // App shell assets: cache-first (they rarely change).
  if (SHELL.includes(url.pathname)) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((r) => {
        const copy = r.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return r;
      }))
    );
    return;
  }

  // Everything else: normal network.
});
