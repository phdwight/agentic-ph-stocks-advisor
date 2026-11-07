/**
 * PH Stocks Advisor — Root-scoped PWA Service Worker.
 *
 * Caching strategy:
 * - Navigation requests (HTML): Network-first so CSRF tokens, session state,
 *   and live stock reports are never stale; falls back to /offline when
 *   disconnected.
 * - Versioned static assets (/static/*): Cache-first for instant app-shell
 *   startup, keyed by the content-derived ASSET_REV injected by /sw.js.
 * - Auth, API, SSE streams, and health checks: Network-only (bypassed).
 */

/* global ASSET_REV */
const CACHE_PREFIX = "ph-stocks-shell-";
const CACHE_NAME = `${CACHE_PREFIX}${typeof ASSET_REV !== "undefined" ? ASSET_REV : "v1"}`;

const PRECACHE_URLS = [
  "/offline",
  "/manifest.webmanifest",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
];

const BYPASS_PREFIXES = [
  "/auth/",
  "/api/",
  "/analyse",
  "/stream/",
  "/status/",
  "/cancel/",
  "/healthz",
  "/version",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => Promise.allSettled(PRECACHE_URLS.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === "/sw.js") return;
  if (BYPASS_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(async () => {
        const offlineResp = await caches.match("/offline");
        return (
          offlineResp ||
          new Response("Offline — check your connection and try again.", {
            status: 503,
            headers: { "Content-Type": "text/plain; charset=utf-8" },
          })
        );
      })
    );
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          if (response && response.status === 200 && response.type === "basic") {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        });
      })
    );
  }
});
