const CACHE_NAME = "docnexus-pwa-v6";
const APP_SHELL = ["/", "/offline.html", "/manifest.webmanifest", "/static/pwa/icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  // Trang quản trị và đăng nhập phụ thuộc phiên đăng nhập: luôn để trình duyệt tải trực tiếp, không cache.
  if (request.method !== "GET" || url.origin !== self.location.origin
      || url.pathname.startsWith("/api/") || url.pathname.startsWith("/admin") || url.pathname.startsWith("/login")) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).then((response) => {
      if (url.pathname === "/") {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
      }
      return response;
    }).catch(() => caches.match(request).then((cached) => cached || caches.match("/offline.html"))));
    return;
  }

  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
    // Stale-while-revalidate: serve cached assets immediately but refresh them in the background.
    event.respondWith(caches.open(CACHE_NAME).then((cache) => cache.match(request).then((cached) => {
      const network = fetch(request).then((response) => {
        if (response.ok) cache.put(request, response.clone());
        return response;
      }).catch(() => cached);
      return cached || network;
    })));
  }
});