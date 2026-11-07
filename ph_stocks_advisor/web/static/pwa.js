/**
 * PH Stocks Advisor — PWA shell, theme sync, install flow, and mobile navigation.
 */

(function () {
  const THEME_KEY = "ph_advisor_theme";
  const INSTALL_DISMISSED_KEY = "ph_advisor_install_dismissed";
  const LIGHT_THEME_COLOR = "#f8fafc";
  const DARK_THEME_COLOR = "#0b1120";

  const root = document.documentElement;
  const darkMedia = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function getSavedTheme() {
    try {
      const val = localStorage.getItem(THEME_KEY);
      return val === "light" || val === "dark" ? val : null;
    } catch {
      return null;
    }
  }

  function effectiveTheme() {
    const saved = getSavedTheme();
    if (saved) return saved;
    return darkMedia && darkMedia.matches ? "dark" : "light";
  }

  function syncThemeColorMeta() {
    const color = effectiveTheme() === "dark" ? DARK_THEME_COLOR : LIGHT_THEME_COLOR;
    document.querySelectorAll('meta[name="theme-color"][data-dynamic-theme]').forEach((meta) => {
      meta.setAttribute("content", color);
    });
  }

  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") {
      root.setAttribute("data-theme", theme);
    } else {
      root.removeAttribute("data-theme");
    }
    syncThemeColorMeta();
    const isDark = effectiveTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(isDark));
      btn.setAttribute("title", isDark ? "Switch to light mode" : "Switch to dark mode");
    });
  }

  // Apply saved theme immediately before paint settles
  applyTheme(getSavedTheme());

  if (darkMedia && typeof darkMedia.addEventListener === "function") {
    darkMedia.addEventListener("change", () => {
      if (!getSavedTheme()) applyTheme(null);
    });
  }

  let deferredInstallPrompt = null;

  function isStandalone() {
    return (
      (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
      window.navigator.standalone === true
    );
  }

  function isIosSafari() {
    const ua = window.navigator.userAgent || "";
    const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    const isWebkit = /WebKit/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua);
    return isIOS && isWebkit && !isStandalone();
  }

  function revealInstallAffordances() {
    if (isStandalone()) return;
    document.querySelectorAll("[data-pwa-install]").forEach((el) => {
      el.hidden = false;
    });
    const banner = document.getElementById("pwa-install-banner");
    let dismissed = false;
    try {
      dismissed = sessionStorage.getItem(INSTALL_DISMISSED_KEY) === "1";
    } catch { /* ignore */ }
    if (banner && !dismissed) {
      banner.hidden = false;
    }
  }

  function hideInstallAffordances() {
    document.querySelectorAll("[data-pwa-install]").forEach((el) => {
      el.hidden = true;
    });
    const banner = document.getElementById("pwa-install-banner");
    if (banner) banner.hidden = true;
  }

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    revealInstallAffordances();
  });

  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    hideInstallAffordances();
  });

  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(getSavedTheme());

    // 1. Register root-scoped Service Worker
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        // Non-fatal in test/unsupported environments
      });
    }

    // 2. Theme toggle buttons
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = effectiveTheme() === "dark" ? "light" : "dark";
        try {
          localStorage.setItem(THEME_KEY, next);
        } catch { /* ignore */ }
        applyTheme(next);
      });
    });

    // 3. PWA install buttons & iOS install guide sheet
    if (isIosSafari()) {
      revealInstallAffordances();
    }

    const iosModal = document.getElementById("ios-install-sheet");
    const closeIosSheet = () => {
      if (iosModal) iosModal.hidden = true;
    };

    document.querySelectorAll("[data-pwa-install]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (deferredInstallPrompt) {
          deferredInstallPrompt.prompt();
          try {
            const choice = await deferredInstallPrompt.userChoice;
            if (choice && choice.outcome === "accepted") {
              hideInstallAffordances();
            }
          } catch { /* user cancelled */ }
          deferredInstallPrompt = null;
        } else if (iosModal) {
          iosModal.hidden = false;
        }
      });
    });

    document.getElementById("ios-sheet-close")?.addEventListener("click", closeIosSheet);
    iosModal?.addEventListener("click", (e) => {
      if (e.target === iosModal) closeIosSheet();
    });

    document.getElementById("pwa-banner-dismiss")?.addEventListener("click", () => {
      const banner = document.getElementById("pwa-install-banner");
      if (banner) banner.hidden = true;
      try {
        sessionStorage.setItem(INSTALL_DISMISSED_KEY, "1");
      } catch { /* ignore */ }
    });

    // 4. Mobile Recent slide-over drawer & bottom tab bar actions
    const backdrop = document.getElementById("sidebar-backdrop");
    const mobileDrawerMedia = window.matchMedia ? window.matchMedia("(max-width: 900px)") : null;
    const closeDrawer = () => {
      document.body.classList.remove("drawer-open");
      if (backdrop) backdrop.hidden = true;
      document.querySelectorAll("[data-open-recent]").forEach((b) => b.setAttribute("aria-expanded", "false"));
    };

    const openDrawer = () => {
      const sidebar = document.querySelector(".sidebar");
      if (sidebar && window.getComputedStyle(sidebar).display !== "none") {
        document.body.classList.add("drawer-open");
        if (backdrop) backdrop.hidden = false;
        document.querySelectorAll("[data-open-recent]").forEach((b) => b.setAttribute("aria-expanded", "true"));
        return;
      }
      // Fallback when no personal sidebar exists yet: scroll to recent stocks or search
      const recentSection = document.getElementById("recent-stocks");
      if (recentSection && recentSection.style.display !== "none") {
        recentSection.scrollIntoView({ behavior: "smooth", block: "start" });
      } else {
        const input = document.getElementById("symbol-input");
        if (input) {
          input.scrollIntoView({ behavior: "smooth", block: "center" });
          input.focus();
        } else {
          window.location.href = "/?focus=search";
        }
      }
    };

    document.querySelectorAll("[data-open-recent]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (document.body.classList.contains("drawer-open")) {
          closeDrawer();
        } else {
          openDrawer();
        }
      });
    });

    backdrop?.addEventListener("click", closeDrawer);
    if (mobileDrawerMedia && typeof mobileDrawerMedia.addEventListener === "function") {
      mobileDrawerMedia.addEventListener("change", (e) => {
        if (!e.matches) closeDrawer();
      });
    }
    window.addEventListener("pageshow", (e) => {
      if (e.persisted) closeDrawer();
    });
    document.addEventListener("click", (e) => {
      if (e.target.closest("[data-close-drawer]")) closeDrawer();
      if (document.body.classList.contains("drawer-open") && e.target.closest(".sidebar-ticker")) {
        closeDrawer();
      }
    });

    // On non-index pages (where app.js is absent), intercept logged-in clicks
    // on sidebar report links so stale reports trigger a fresh analysis on "/"
    // when conditions permit instead of displaying the outdated report.
    if (!document.getElementById("analyse-form")) {
      const TASKS_KEY = "ph_advisor_tasks";
      const STALE_NOTICE_KEY = "ph_advisor_stale_notice";
      const TASK_STALE_MS = 10 * 60 * 1000;
      const inflightSidebarClicks = new Set();

      document.addEventListener("click", async (e) => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
          return;
        }
        const link = e.target.closest(".sidebar-ticker");
        if (!link) return;
        if (document.body.dataset.authenticated !== "true") return;

        let symbol = (link.dataset.symbol || "").trim().toUpperCase();
        if (!symbol) {
          const href = link.getAttribute("href") || "";
          const match = href.match(/^\/report\/([^/?#]+)/);
          symbol = match ? decodeURIComponent(match[1]).trim().toUpperCase() : "";
        }
        if (!symbol) return;

        e.preventDefault();

        try {
          const existingTasks = JSON.parse(localStorage.getItem(TASKS_KEY)) || {};
          const existing = existingTasks[symbol];
          if (existing && existing.status === "pending" && Date.now() - existing.ts <= TASK_STALE_MS) {
            sessionStorage.setItem(STALE_NOTICE_KEY, JSON.stringify({ symbol, status: "joined" }));
            window.location.href = "/";
            return;
          }
        } catch { /* ignore */ }

        if (inflightSidebarClicks.has(symbol)) return;
        inflightSidebarClicks.add(symbol);
        link.classList.add("is-checking");

        try {
          const resp = await fetch("/analyse", {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded",
              "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
            },
            body: `symbol=${encodeURIComponent(symbol)}&refresh_stale=1`,
          });

          if (resp.redirected && resp.url.includes("/auth/login")) {
            window.location.href = resp.url;
            return;
          }

          const data = await resp.json();

          if (resp.status === 401 || data.status === "login_required" || data.login_url) {
            window.location.href = data.login_url || "/auth/login";
            return;
          }

          if (data.status === "cached") {
            window.location.href = `/report/${encodeURIComponent(data.symbol || symbol)}`;
            return;
          }

          if (data.task_id) {
            try {
              const tasks = JSON.parse(localStorage.getItem(TASKS_KEY)) || {};
              tasks[symbol] = { taskId: data.task_id, status: "pending", ts: Date.now() };
              localStorage.setItem(TASKS_KEY, JSON.stringify(tasks));
            } catch { /* ignore */ }
            try {
              sessionStorage.setItem(
                STALE_NOTICE_KEY,
                JSON.stringify({ symbol: data.symbol || symbol, status: data.status })
              );
            } catch { /* ignore */ }
            window.location.href = "/";
            return;
          }

          window.location.href = link.href;
        } catch {
          window.location.href = link.href;
        } finally {
          inflightSidebarClicks.delete(symbol);
          link.classList.remove("is-checking");
        }
      });
    }

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeDrawer();
        closeIosSheet();
      }
      // "/" shortcut focuses ticker search when not typing in an input
      if (
        e.key === "/" &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.altKey &&
        !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "") &&
        !document.activeElement?.isContentEditable
      ) {
        const input = document.getElementById("symbol-input");
        if (input) {
          e.preventDefault();
          input.focus();
          input.select();
        }
      }
    });

    // Bottom bar "Search" button focuses input if already on "/"
    document.querySelectorAll("[data-nav-search]").forEach((el) => {
      el.addEventListener("click", (e) => {
        const input = document.getElementById("symbol-input");
        if (input) {
          e.preventDefault();
          closeDrawer();
          window.scrollTo({ top: 0, behavior: "smooth" });
          input.focus({ preventScroll: true });
        }
      });
    });

    // Honour /?focus=search (PWA app shortcut & cross-page search jump)
    const params = new URLSearchParams(window.location.search);
    if (params.get("focus") === "search") {
      const input = document.getElementById("symbol-input");
      if (input) {
        setTimeout(() => input.focus(), 60);
      }
    }

    // 5. Live connectivity banner & /offline retry button
    const netBanner = document.getElementById("network-status-banner");
    const syncNetworkBanner = () => {
      if (!netBanner) return;
      netBanner.hidden = navigator.onLine !== false;
    };
    window.addEventListener("offline", syncNetworkBanner);
    window.addEventListener("online", syncNetworkBanner);
    syncNetworkBanner();

    document.getElementById("offline-retry-btn")?.addEventListener("click", () => {
      window.location.reload();
    });
  });
})();
