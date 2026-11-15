/**
 * PH Stocks Advisor — Root-scoped PWA Service Worker.
 *
 * Caching strategy:
 * - Navigation requests (HTML): Network-first so CSRF tokens, session state,
 *   and live stock reports are never stale; falls back to /offline when
 *   disconnected.
 * - Optimistic-navigation hand-off: nav.js confirms a navigation with a
 *   fetch() tagged `X-Optimistic-Nav: 1` before committing to it. A 200 HTML
 *   answer is parked in a single-use cache for a few seconds, and the real
 *   navigation that immediately follows is answered from it. The server
 *   renders the page once, and the commit paints instantly.
 * - Versioned static assets (/static/*): Cache-first for instant app-shell
 *   startup, keyed by the content-derived ASSET_REV injected by /sw.js.
 * - Auth, API, SSE streams, and health checks: Network-only (bypassed).
 */

/* global ASSET_REV */
const CACHE_PREFIX = "ph-stocks-shell-";
const CACHE_NAME = `${CACHE_PREFIX}${typeof ASSET_REV !== "undefined" ? ASSET_REV : "v1"}`;

const NAV_HANDOFF_CACHE = "ph-stocks-nav-handoff";
const NAV_HANDOFF_HEADER = "X-Optimistic-Nav";
const NAV_HANDOFF_AT_HEADER = "X-Nav-Handoff-At";
const NAV_HANDOFF_TTL_MS = 15000;

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
            .filter(
              (key) =>
                (key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME) ||
                // Hand-offs are single-use and short-lived; never carry them across versions.
                key === NAV_HANDOFF_CACHE
            )
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

/**
 * Forward an optimistic-navigation confirmation to the network and park a
 * successful HTML answer for the navigation that follows it. The hand-off
 * is an optimisation only — any failure here still returns the response.
 */
async function confirmAndParkNavigation(request) {
  const response = await fetch(request);
  const type = response.headers.get("Content-Type") || "";
  if (response.ok && !response.redirected && response.type === "basic" && type.includes("text/html")) {
    try {
      const body = await response.clone().arrayBuffer();
      const headers = new Headers(response.headers);
      headers.set(NAV_HANDOFF_AT_HEADER, String(Date.now()));
      const cache = await caches.open(NAV_HANDOFF_CACHE);
      // Park before answering, so the page can't navigate ahead of the put.
      await cache.put(
        request.url,
        new Response(body, { status: response.status, statusText: response.statusText, headers })
      );
    } catch {
      /* hand-off unavailable — the navigation will simply hit the network */
    }
  }
  return response;
}

/**
 * Take (and delete) a fresh parked response for this navigation, if any.
 * Expired entries are purged opportunistically. Reloads (request.cache is
 * not "default") always go to the network.
 */
async function takeParkedNavigation(request) {
  if (request.cache !== "default") return null;
  try {
    const cache = await caches.open(NAV_HANDOFF_CACHE);
    const now = Date.now();
    let hit = null;
    for (const key of await cache.keys()) {
      const parked = await cache.match(key, { ignoreVary: true });
      const at = Number((parked && parked.headers.get(NAV_HANDOFF_AT_HEADER)) || 0);
      const fresh = at > 0 && now - at <= NAV_HANDOFF_TTL_MS;
      if (key.url === request.url) {
        if (fresh) hit = parked;
        await cache.delete(key);
      } else if (!fresh) {
        await cache.delete(key);
      }
    }
    return hit;
  } catch {
    return null;
  }
}

async function networkFirstNavigation(request) {
  const parked = await takeParkedNavigation(request);
  if (parked) return parked;
  try {
    return await fetch(request);
  } catch {
    const offlineResp = await caches.match("/offline");
    return (
      offlineResp ||
      new Response("Offline — check your connection and try again.", {
        status: 503,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      })
    );
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === "/sw.js") return;
  if (BYPASS_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) return;

  if (request.headers.get(NAV_HANDOFF_HEADER) === "1") {
    event.respondWith(confirmAndParkNavigation(request));
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(request));
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
