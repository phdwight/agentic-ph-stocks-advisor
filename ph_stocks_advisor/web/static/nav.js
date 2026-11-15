/**
 * PH Stocks Advisor — Optimistic navigation.
 *
 * Clicking an in-app link swaps #main-content for the destination view's
 * skeleton layout immediately, then confirms the navigation with the server:
 *
 *   success → commit:   a real browser navigation to the URL. When the
 *                       service worker controls the page, it hands the
 *                       already-fetched HTML straight to that navigation
 *                       (see the "nav hand-off" in sw.js), so the server
 *                       renders the page once.
 *   failure → rollback: the previous view is restored exactly as it was
 *                       (the original DOM nodes, so listeners, timers and
 *                       live SSE updates survive), along with the title,
 *                       body class, scroll position and focus, and an error
 *                       toast explains what went wrong.
 *
 * Public API (used by app.js / pwa.js for links that need a server
 * decision before they know where they lead):
 *
 *   OptimisticNav.navigate(url)  → Promise<boolean>  skeleton + confirm
 *   OptimisticNav.begin(url)     → Transition | null skeleton only; the caller
 *                                  later calls t.commit(url?) or t.rollback(msg?)
 */

(function () {
  "use strict";

  const NAV_HEADER = "X-Optimistic-Nav";
  const CONFIRM_TIMEOUT_MS = 30000;
  const TOAST_MS = 8000;

  /** Server-rendered views that have a skeleton layout in _skeletons.html. */
  const ROUTES = [
    { view: "report", re: /^\/report\/([^/]+)\/?$/, symbol: true },
    { view: "report", re: /^\/report-by-id\/\d+\/?$/ },
    { view: "history", re: /^\/history\/([^/]+)\/?$/, symbol: true },
    { view: "index", re: /^\/$/ },
    { view: "page", re: /^\/(?:disclaimer|version)\/?$/ },
  ];

  const main = document.getElementById("main-content");
  const supported =
    !!main &&
    typeof window.fetch === "function" &&
    typeof window.AbortController === "function" &&
    "content" in document.createElement("template") &&
    typeof Element.prototype.replaceChildren === "function";

  /** The in-flight transition (null when the page is at rest). */
  let current = null;
  /** The committed page captured before the first skeleton was shown. */
  let snapshot = null;

  /* ================================================================== */
  /*  Routing                                                           */
  /* ================================================================== */

  function resolveRoute(href) {
    let url;
    try {
      url = new URL(href, window.location.href);
    } catch {
      return null;
    }
    if (url.origin !== window.location.origin) return null;
    for (const r of ROUTES) {
      const m = url.pathname.match(r.re);
      if (!m) continue;
      let symbol = "";
      if (r.symbol) {
        try {
          symbol = decodeURIComponent(m[1]);
        } catch {
          symbol = m[1];
        }
        symbol = symbol.trim().toUpperCase().replace(/\.PS$/, "");
      }
      url.hash = "";
      return { view: r.view, symbol, url: url.href };
    }
    return null;
  }

  function describe(route) {
    switch (route.view) {
      case "report":
        return route.symbol ? `the ${route.symbol} report` : "that report";
      case "history":
        return `the ${route.symbol} report history`;
      case "index":
        return "the home page";
      default:
        return "that page";
    }
  }

  function errorForStatus(status, route) {
    if (status === 404) {
      if (route.view === "report") {
        return route.symbol
          ? `There is no saved report for ${route.symbol} yet. Search for it to run a new analysis.`
          : "That report no longer exists.";
      }
      return "That page could not be found.";
    }
    if (status === 401 || status === 403) return `You don't have access to ${describe(route)}.`;
    if (status === 429) return "Too many requests. Wait a moment, then try again.";
    if (status >= 500) return `The server couldn't load ${describe(route)} (error ${status}). Try again.`;
    return `Couldn't open ${describe(route)} (error ${status}).`;
  }

  /* ================================================================== */
  /*  Snapshot / skeleton / restore                                     */
  /* ================================================================== */

  function tabbarItems() {
    return Array.from(document.querySelectorAll(".mobile-tabbar a.tabbar-item[href]"));
  }

  /** Detach (not clone) the current view so it can be restored intact. */
  function captureSnapshot() {
    const fragment = document.createDocumentFragment();
    while (main.firstChild) fragment.appendChild(main.firstChild);
    const viewClasses = allViewClasses();
    return {
      fragment,
      title: document.title,
      viewClasses: Array.from(document.body.classList).filter((c) => viewClasses.has(c)),
      scrollX: window.scrollX,
      scrollY: window.scrollY,
      focus: document.activeElement,
      activeTabs: tabbarItems().filter((a) => a.classList.contains("is-active")),
    };
  }

  function findTemplate(view) {
    return document.querySelector(`template[data-skeleton-view="${view}"]`);
  }

  function splitClasses(value) {
    return (value || "").split(/\s+/).filter(Boolean);
  }

  /**
   * Body classes that belong to a *view* (e.g. "page-report"). Only these are
   * swapped, so transient UI classes such as pwa.js's "drawer-open" survive.
   */
  function allViewClasses() {
    const set = new Set();
    document.querySelectorAll("template[data-skeleton-body-class]").forEach((tpl) => {
      splitClasses(tpl.dataset.skeletonBodyClass).forEach((c) => set.add(c));
    });
    return set;
  }

  function setViewClasses(classes) {
    const body = document.body;
    allViewClasses().forEach((c) => body.classList.remove(c));
    classes.forEach((c) => body.classList.add(c));
  }

  function renderSkeleton(route) {
    const tpl = findTemplate(route.view);
    const content = tpl.content.cloneNode(true);
    content.querySelectorAll("[data-skeleton-symbol]").forEach((el) => {
      if (route.symbol) {
        el.textContent = route.symbol;
      } else {
        el.classList.add("sk", "sk-sym");
      }
    });

    const hadFocusInside = main.contains(document.activeElement) || document.activeElement === document.body;
    main.replaceChildren(content);
    main.setAttribute("aria-busy", "true");
    setViewClasses(splitClasses(tpl.dataset.skeletonBodyClass));
    document.body.classList.add("is-navigating");
    document.title = route.symbol ? `${route.symbol} — Loading… — PH Stocks Advisor` : "Loading… — PH Stocks Advisor";

    const targetPath = new URL(route.url).pathname;
    tabbarItems().forEach((a) => {
      a.classList.toggle("is-active", new URL(a.href).pathname === targetPath);
    });

    announce(`Loading ${describe(route)}…`);
    window.scrollTo(0, 0);
    // The element that had focus may have been detached with the old view.
    if (hadFocusInside || !document.activeElement || !document.activeElement.isConnected) {
      main.focus({ preventScroll: true });
    }
  }

  function restoreSnapshot() {
    document.body.classList.remove("is-navigating");
    if (!snapshot) return;
    const s = snapshot;
    snapshot = null;
    main.replaceChildren(s.fragment);
    main.removeAttribute("aria-busy");
    setViewClasses(s.viewClasses);
    document.title = s.title;
    tabbarItems().forEach((a) => a.classList.toggle("is-active", s.activeTabs.includes(a)));
    window.scrollTo(s.scrollX, s.scrollY);
    if (s.focus && s.focus.isConnected && typeof s.focus.focus === "function") {
      s.focus.focus({ preventScroll: true });
    }
    announce("");
  }

  function announce(msg) {
    const live = document.getElementById("nav-status");
    if (live) live.textContent = msg;
  }

  /* ================================================================== */
  /*  Error toast                                                       */
  /* ================================================================== */

  let toastTimer = null;

  function showToast(message, retryUrl) {
    const toast = document.getElementById("nav-toast");
    if (!toast) return;
    toast.querySelector("[data-nav-toast-msg]").textContent = message;
    const retry = toast.querySelector("[data-nav-toast-retry]");
    retry.hidden = !retryUrl;
    retry.dataset.url = retryUrl || "";
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, TOAST_MS);
  }

  function hideToast() {
    const toast = document.getElementById("nav-toast");
    clearTimeout(toastTimer);
    if (toast) toast.hidden = true;
  }

  /* ================================================================== */
  /*  Transitions                                                       */
  /* ================================================================== */

  /**
   * One optimistic navigation. Only the newest transition is "live";
   * a superseded transition's commit/rollback calls become no-ops.
   */
  function createTransition(route) {
    const t = {
      route,
      state: "pending", // pending → confirming → leaving | rolled-back | superseded
      controller: new AbortController(),
      timedOut: false,

      /** Confirm with the server and navigate, or roll back on failure. */
      commit(url) {
        if (current !== t || t.state === "leaving") return Promise.resolve(false);
        if (url) {
          const next = resolveRoute(url);
          if (!next) {
            // Not a skeleton-backed view (e.g. /auth/login): go there natively.
            leave(t, url);
            return Promise.resolve(true);
          }
          if (next.view !== t.route.view || next.symbol !== t.route.symbol) renderSkeleton(next);
          t.route = next;
        }
        return confirm(t);
      },

      /** Restore the previous view; show `message` in the error toast if given. */
      rollback(message, retryUrl) {
        if (current !== t) return;
        t.state = "rolled-back";
        t.controller.abort();
        current = null;
        restoreSnapshot();
        if (message) showToast(message, retryUrl);
      },
    };
    return t;
  }

  /** Show the destination skeleton now; the caller decides commit/rollback. */
  function begin(href) {
    if (!supported) return null;
    const route = resolveRoute(href);
    if (!route || !findTemplate(route.view)) return null;

    if (current) {
      // A newer click wins. Keep the original snapshot: the page currently
      // shows the previous skeleton, which must never become a restore point.
      current.state = "superseded";
      current.controller.abort();
    } else {
      snapshot = captureSnapshot();
    }
    hideToast();
    renderSkeleton(route);
    current = createTransition(route);
    return current;
  }

  function navigate(href) {
    const t = begin(href);
    if (!t) {
      window.location.assign(href);
      return Promise.resolve(false);
    }
    return t.commit();
  }

  function leave(t, url) {
    t.state = "leaving";
    window.location.assign(url);
  }

  async function confirm(t) {
    t.state = "confirming";
    const { route, controller } = t;
    const timer = setTimeout(() => {
      t.timedOut = true;
      controller.abort();
    }, CONFIRM_TIMEOUT_MS);

    let resp;
    try {
      resp = await fetch(route.url, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { [NAV_HEADER]: "1", Accept: "text/html" },
        signal: controller.signal,
      });
    } catch {
      clearTimeout(timer);
      if (current !== t || t.route !== route) return false; // superseded or cancelled
      let msg;
      if (t.timedOut) msg = `The server took too long to load ${describe(route)}. Try again.`;
      else if (navigator.onLine === false) msg = "You're offline. Reconnect, then try again.";
      else msg = `Couldn't reach the server to load ${describe(route)}. Try again.`;
      t.rollback(msg, route.url);
      return false;
    }
    clearTimeout(timer);
    if (resp.body) resp.body.cancel().catch(() => {});
    if (current !== t || t.route !== route) return false;

    if (resp.ok || resp.redirected) {
      // Redirects (e.g. guest → /auth/login) are replayed natively so the
      // server can set its session state on the real navigation.
      leave(t, route.url);
      return true;
    }
    t.rollback(errorForStatus(resp.status, route), resp.status >= 500 ? route.url : "");
    return false;
  }

  /* ================================================================== */
  /*  Wiring                                                            */
  /* ================================================================== */

  function shouldIntercept(link) {
    if (link.target && link.target !== "_self") return false;
    if (link.hasAttribute("download") || link.closest("[data-native-nav]")) return false;
    const url = new URL(link.href, window.location.href);
    const here = new URL(window.location.href);
    if (url.origin !== here.origin) return false;
    // Same document (hash jump or reload of the current page): leave native.
    if (url.pathname === here.pathname && url.search === here.search) return false;
    return resolveRoute(url.href) !== null;
  }

  window.OptimisticNav = {
    begin,
    navigate,
    get enabled() {
      return supported;
    },
  };

  if (!supported) return;

  // Listen on window (bubble phase) so page-specific handlers on document —
  // app.js / pwa.js stale-report checks — run first and can claim the click
  // with preventDefault(); they then drive OptimisticNav themselves.
  window.addEventListener("click", (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const link = e.target instanceof Element ? e.target.closest("a[href]") : null;
    if (!(link instanceof HTMLAnchorElement) || !shouldIntercept(link)) return;
    e.preventDefault();
    navigate(link.href);
  });

  // Escape abandons a pending navigation (before the browser starts leaving).
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && current && current.state !== "leaving") current.rollback();
  });

  // Back/forward cache: if the user returns to this page after a committed
  // navigation, it would still show the skeleton — restore the real view.
  window.addEventListener("pageshow", (e) => {
    if (e.persisted && current) {
      current.state = "rolled-back";
      current = null;
      restoreSnapshot();
    }
  });

  document.addEventListener("click", (e) => {
    if (!(e.target instanceof Element)) return;
    if (e.target.closest("[data-nav-toast-close]")) hideToast();
    const retry = e.target.closest("[data-nav-toast-retry]");
    if (retry && retry.dataset.url) {
      hideToast();
      navigate(retry.dataset.url);
    }
  });
})();
