// Minimal service worker: network-first so listeners always get fresh shows,
// with a cached copy as an offline fallback. Bump CACHE_VERSION to force refresh.
const CACHE_VERSION = "bps-v1";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;

  // Only handle same-origin GETs. Never touch audio streams or cross-origin media.
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) {
    return;
  }
  if (req.destination === "audio") {
    return;
  }

  event.respondWith(
    fetch(req)
      .then((resp) => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy));
        }
        return resp;
      })
      .catch(() => caches.match(req))
  );
});
