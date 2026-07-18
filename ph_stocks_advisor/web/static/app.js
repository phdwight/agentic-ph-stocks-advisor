/**
 * PH Stocks Advisor — client-side analysis submission & polling.
 *
 * Supports multiple concurrent analyses.  Each submission creates a
 * task card in the tracker panel.  Task state is persisted in
 * localStorage so in-progress analyses survive page navigation
 * (e.g. viewing a report and pressing Back).
 */

document.addEventListener("DOMContentLoaded", () => {
  const STORAGE_KEY = "ph_advisor_tasks";
  const POLL_MS = 3000;
  const STALE_MS = 10 * 60 * 1000; // auto-expire tasks older than 10 min

  const form = document.getElementById("analyse-form");
  const input = document.getElementById("symbol-input");
  const trackerList = document.getElementById("tracker-list");
  const trackerPanel = document.getElementById("tracker-panel");
  const trackerCount = document.getElementById("tracker-count");
  const errorArea = document.getElementById("error-area");
  const errorText = document.getElementById("error-text");

  if (!form) return;

  /* ================================================================== */
  /*  localStorage helpers                                              */
  /* ================================================================== */

  function loadTasks() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
    } catch { return {}; }
  }

  function saveTasks(tasks) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
  }

  function addTask(symbol, taskId) {
    const tasks = loadTasks();
    tasks[symbol] = { taskId, status: "pending", ts: Date.now() };
    saveTasks(tasks);
  }

  function updateTask(symbol, status, extra) {
    const tasks = loadTasks();
    if (tasks[symbol]) {
      tasks[symbol].status = status;
      if (extra) Object.assign(tasks[symbol], extra);
    }
    saveTasks(tasks);
    if (status === "error") {
      scheduleErrorAutoFade(symbol);
    }
  }

  // Tracker error rows should not stick around forever — fade them out
  // and remove them from the persisted task list after a short read window.
  // Tracked per-symbol so duplicate updates don't stack timers.
  const ERROR_AUTO_FADE_MS = 8000;   // visible time before fade starts
  const ERROR_FADE_DURATION = 600;   // must match CSS transition duration
  const errorFadeTimers = {};

  function scheduleErrorAutoFade(symbol) {
    if (errorFadeTimers[symbol]) {
      clearTimeout(errorFadeTimers[symbol]);
    }
    errorFadeTimers[symbol] = setTimeout(() => {
      const row = cardRefs[symbol];
      const finish = () => {
        delete errorFadeTimers[symbol];
        // Only remove if it's still in error state (user may have retried).
        const tasks = loadTasks();
        if (tasks[symbol] && tasks[symbol].status === "error") {
          removeTask(symbol);
          renderTracker();
        }
      };
      if (row && row.classList.contains("tracker-row-error")) {
        row.classList.add("is-fading");
        setTimeout(finish, ERROR_FADE_DURATION);
      } else {
        finish();
      }
    }, ERROR_AUTO_FADE_MS);
  }

  function removeTask(symbol) {
    const tasks = loadTasks();
    const task = tasks[symbol];
    // If the task completed successfully, surface it in the recent chips
    // and the left "Recent" sidebar so the user doesn't have to refresh
    // the page to see their newly-completed analysis.
    if (task && task.status === "done" && task.verdict) {
      addChipToRecent(symbol, task.verdict, task.score);
      addToSidebar(symbol, task.verdict, task.score);
    }
    delete tasks[symbol];
    saveTasks(tasks);
  }

  /* ================================================================== */
  /*  Carousel: build row tracks for seamless marquee                   */
  /* ================================================================== */

  function calibrateCarousel() {
    const el = document.getElementById("stock-chips");
    const wrapper = document.querySelector(".stock-chips-wrapper");
    if (!el || !wrapper) return;

    // 1. Flatten — move chips out of any existing row tracks
    el.querySelectorAll(".stock-chips-row").forEach(row => {
      while (row.firstChild) el.appendChild(row.firstChild);
      row.remove();
    });

    // 2. Remove all duplicates (we'll re-create them per-row)
    el.querySelectorAll('[aria-hidden="true"]').forEach(d => d.remove());

    // 3. Gather originals
    const originals = Array.from(el.querySelectorAll(".stock-chip"));
    const count = originals.length;
    if (count === 0) return;

    // ≤5 chips → static centered row, no animation
    if (count <= 5) {
      wrapper.setAttribute("data-static", "");
      el.classList.add("calibrated");
      return;
    }

    // >5 chips → marquee mode with independent row tracks
    wrapper.removeAttribute("data-static");
    const numRows = count <= 10 ? 2 : 3;

    // 4. Create row track elements
    const rowEls = [];
    for (let i = 0; i < numRows; i++) {
      const row = document.createElement("div");
      row.className = "stock-chips-row";
      rowEls.push(row);
      el.appendChild(row);
    }

    // 5. Distribute chips round-robin across rows
    originals.forEach((chip, i) => {
      rowEls[i % numRows].appendChild(chip);
    });

    // 6. Duplicate each row's chips for seamless infinite scroll
    rowEls.forEach(row => {
      Array.from(row.children).forEach(chip => {
        const dupe = chip.cloneNode(true);
        dupe.setAttribute("aria-hidden", "true");
        dupe.removeAttribute("data-symbol");
        dupe.tabIndex = -1;
        row.appendChild(dupe);
      });
    });

    // 7. Set animation speed per row (~50 px/s)
    const PX_PER_SEC = 50;
    rowEls.forEach((row, i) => {
      const halfWidth = row.scrollWidth / 2;
      if (halfWidth <= 0) return;
      const base = Math.max(6, halfWidth / PX_PER_SEC);
      // Slight speed offset between rows for visual depth
      const duration = base * (1 + i * 0.12);
      row.style.setProperty("--row-duration", `${duration.toFixed(1)}s`);
      // Stagger start so rows aren't synchronised
      row.style.animationDelay = `${(-i * 1.5).toFixed(1)}s`;
    });

    // Reveal once layout is ready
    el.classList.add("calibrated");
  }

  /* ================================================================== */
  /*  Dynamically add a chip to "Previously Analysed Stocks"            */
  /* ================================================================== */

  function addChipToRecent(symbol, verdict, score) {
    const chipsContainer = document.getElementById("stock-chips");
    const recentSection = document.getElementById("recent-stocks");
    if (!chipsContainer || !recentSection) return;

    // Don't add a duplicate
    if (chipsContainer.querySelector(`[data-symbol="${symbol}"]`)) return;

    // Show the section if it was hidden (no previous stocks)
    recentSection.style.display = "";

    const chipInfo = bandChip(verdict, score);
    const now = new Date();
    const monthNames = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const dateStr = `${monthNames[now.getMonth()]} ${String(now.getDate()).padStart(2, "0")}`;

    const chip = document.createElement("a");
    chip.href = `/report/${symbol}`;
    chip.className = "stock-chip stock-chip-new";
    chip.dataset.symbol = symbol;
    chip.innerHTML = `
      <span class="chip-symbol">${symbol}</span>
      <span class="chip-verdict badge-sm ${chipInfo.cls}">${chipInfo.label}</span>
      <span class="chip-date">${dateStr}</span>`;

    // Prepend chip then let calibrateCarousel redistribute
    chipsContainer.prepend(chip);
    calibrateCarousel();
  }

  /* ================================================================== */
  /*  Dynamically prepend a ticker to the left "Recent" sidebar         */
  /* ================================================================== */

  function addToSidebar(symbol, verdict, score) {
    const layout = document.querySelector(".layout");
    if (!layout) return;

    // Build today's label so it matches the server-side format
    // produced by ``group.date.strftime('%b %d, %Y')`` in base.html.
    const now = new Date();
    const monthNames = [
      "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
    ];
    const todayLabel =
      `${monthNames[now.getMonth()]} ${String(now.getDate()).padStart(2, "0")}, ${now.getFullYear()}`;

    // Locate (or build) the sidebar — it is omitted server-side when the
    // user has zero history yet, in which case we create it on the fly.
    let sidebar = layout.querySelector(".sidebar");
    let history;
    if (!sidebar) {
      sidebar = document.createElement("aside");
      sidebar.className = "sidebar";
      sidebar.setAttribute("aria-label", "Recently analysed tickers");
      sidebar.innerHTML = `
        <h2 class="sidebar-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          Recent
        </h2>
        <ul class="sidebar-history"></ul>`;
      layout.prepend(sidebar);
      history = sidebar.querySelector(".sidebar-history");
    } else {
      history = sidebar.querySelector(".sidebar-history");
      if (!history) {
        history = document.createElement("ul");
        history.className = "sidebar-history";
        sidebar.appendChild(history);
      }
    }

    // Find or create today's day group at the top of the list.
    let todayGroup = history.querySelector(".sidebar-day");
    let tickers;
    const firstLabel = todayGroup
      ? todayGroup.querySelector(".sidebar-day-label")?.textContent.trim()
      : null;
    if (!todayGroup || firstLabel !== todayLabel) {
      todayGroup = document.createElement("li");
      todayGroup.className = "sidebar-day";
      todayGroup.innerHTML = `
        <div class="sidebar-day-label">${todayLabel}</div>
        <ul class="sidebar-tickers"></ul>`;
      history.prepend(todayGroup);
    }
    tickers = todayGroup.querySelector(".sidebar-tickers");

    // Dedup: a re-analysis of the same symbol should move to the top,
    // not stack a duplicate row underneath the previous result.
    history.querySelectorAll(".sidebar-ticker").forEach(a => {
      const sym = a.querySelector(".sidebar-ticker-symbol")?.textContent.trim();
      if (sym === symbol) {
        const li = a.closest("li");
        if (li) li.remove();
      }
    });

    // Drop now-empty day groups created above by the dedup pass.
    history.querySelectorAll(".sidebar-day").forEach(group => {
      const list = group.querySelector(".sidebar-tickers");
      if (list && list.children.length === 0 && group !== todayGroup) {
        group.remove();
      }
    });

    const chipInfo = bandChip(verdict, score);
    const li = document.createElement("li");
    li.innerHTML = `
      <a href="/report/${encodeURIComponent(symbol)}" class="sidebar-ticker">
        <span class="sidebar-ticker-symbol">${symbol}</span>
        <span class="sidebar-ticker-verdict badge-sm ${chipInfo.cls}">${chipInfo.label}</span>
      </a>`;
    tickers.prepend(li);
  }

  /* ================================================================== */
  /*  Render the full tracker from stored state                         */
  /* ================================================================== */

  const cardRefs = {}; // symbol → DOM element

  /**
   * Chip label + class for a verdict, preferring the score band so chips
   * agree with the report's scale (mirrors the server's score_band).
   */
  function bandChip(verdict, score) {
    const s = Number(score);
    if (score === null || score === undefined || Number.isNaN(s)) {
      const isBuy = (verdict || "").toUpperCase() === "BUY";
      return { label: isBuy ? "BUY" : "DON'T BUY", cls: isBuy ? "buy" : "not-buy" };
    }
    if (s >= 80) return { label: "STRONG BUY", cls: "buy" };
    if (s >= 60) return { label: "BUY", cls: "buy" };
    if (s >= 40) return { label: "WAIT", cls: "wait" };
    if (s >= 20) return { label: "DON'T BUY", cls: "avoid" };
    return { label: "AVOID", cls: "avoid" };
  }

  function renderTracker() {
    const tasks = loadTasks();
    const symbols = Object.keys(tasks);

    // Purge stale tasks (>10 min old and still pending)
    const now = Date.now();
    for (const sym of symbols) {
      if (tasks[sym].status === "pending" && now - tasks[sym].ts > STALE_MS) {
        delete tasks[sym];
      }
    }
    saveTasks(tasks);

    const remaining = Object.keys(tasks);
    if (remaining.length === 0) {
      trackerPanel.style.display = "none";
      return;
    }

    trackerPanel.style.display = "block";

    const pendingCount = remaining.filter(s => tasks[s].status === "pending").length;
    const doneCount = remaining.filter(s => tasks[s].status === "done").length;
    if (pendingCount > 0) {
      trackerCount.textContent = `${pendingCount} running`;
      trackerCount.className = "tracker-count tracker-count-active";
    } else if (doneCount > 0) {
      trackerCount.textContent = `${doneCount} ready`;
      trackerCount.className = "tracker-count tracker-count-done";
    } else {
      trackerCount.textContent = "";
      trackerCount.className = "tracker-count";
    }

    trackerList.innerHTML = "";

    // Show newest first
    remaining.sort((a, b) => (tasks[b].ts || 0) - (tasks[a].ts || 0));

    for (const sym of remaining) {
      const t = tasks[sym];
      const row = document.createElement("tr");

      if (t.status === "pending") {
        const step = t.step || 0;
        const stepLabels = ["Queued", "Validating symbol", "Fetching data", "Running agents", "Consolidating", "Saving report"];
        const stepLabel = t.stepLabel || stepLabels[Math.min(step, stepLabels.length - 1)];
        row.className = "tracker-row tracker-row-pending";
        row.innerHTML = `
          <td><span class="chip-symbol">${sym}</span></td>
          <td>
            <span class="tracker-status-pill pending-pill"><span class="tracker-dot"></span>${stepLabel}…</span>
          </td>
          <td><span class="tracker-elapsed" data-ts="${t.ts}"></span></td>
          <td class="tracker-actions">
            <button class="tracker-cancel" data-symbol="${sym}" data-task="${t.taskId}" title="Cancel">✕</button>
          </td>`;
      } else if (t.status === "done") {
        row.className = "tracker-row tracker-row-done";
        row.innerHTML = `
          <td><span class="chip-symbol">${sym}</span></td>
          <td><span class="tracker-status-pill done-pill">✓ Complete</span></td>
          <td></td>
          <td class="tracker-actions">
            <a href="/report/${sym}" class="btn-small">View</a>
            <button class="tracker-dismiss" data-symbol="${sym}">✕</button>
          </td>`;
      } else if (t.status === "error") {
        const fullMsg = t.msg || "Unknown error";
        // Show a short label in the pill; full detail in tooltip.
        const shortMsg = fullMsg.length > 48 ? fullMsg.slice(0, 45) + "…" : fullMsg;
        row.className = "tracker-row tracker-row-error";
        row.innerHTML = `
          <td><span class="chip-symbol">${sym}</span></td>
          <td><span class="tracker-status-pill error-pill" title="${fullMsg.replace(/"/g, '&quot;')}">✕ ${shortMsg}</span></td>
          <td></td>
          <td class="tracker-actions">
            <button class="tracker-retry" data-symbol="${sym}">↻ Retry</button>
            <button class="tracker-dismiss" data-symbol="${sym}">✕</button>
          </td>`;
      }

      trackerList.appendChild(row);
      cardRefs[sym] = row;
    }

    // Attach dismiss & retry handlers
    trackerList.querySelectorAll(".tracker-dismiss").forEach(btn => {
      btn.addEventListener("click", () => {
        removeTask(btn.dataset.symbol);
        renderTracker();
      });
    });

    trackerList.querySelectorAll(".tracker-retry").forEach(btn => {
      btn.addEventListener("click", () => {
        removeTask(btn.dataset.symbol);
        renderTracker();
        input.value = btn.dataset.symbol;
        form.dispatchEvent(new Event("submit", { cancelable: true }));
      });
    });

    trackerList.querySelectorAll(".tracker-cancel").forEach(btn => {
      btn.addEventListener("click", async () => {
        const sym = btn.dataset.symbol;
        const taskId = btn.dataset.task;
        btn.disabled = true;
        btn.textContent = "…";
        // Close any open SSE connection for this symbol.
        if (activeSources[sym]) {
          activeSources[sym].close();
          delete activeSources[sym];
        }
        try {
          await fetch(`/cancel/${taskId}`, {
            method: "POST",
            headers: { "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "" },
          });
        } catch { /* best-effort */ }
        removeTask(sym);
        renderTracker();
      });
    });
  }

  /* ================================================================== */
  /*  Elapsed-time ticker for pending cards                             */
  /* ================================================================== */

  setInterval(() => {
    document.querySelectorAll(".tracker-elapsed[data-ts]").forEach(el => {
      const secs = Math.floor((Date.now() - parseInt(el.dataset.ts)) / 1000);
      if (secs < 60) {
        el.textContent = `${secs}s`;
      } else {
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        el.textContent = `${m}m ${s < 10 ? "0" : ""}${s}s`;
      }
    });
  }, 1000);

  /* ================================================================== */
  /*  Form submission                                                   */
  /* ================================================================== */

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const symbol = input.value.trim().toUpperCase();
    if (!symbol) return;

    const tasks = loadTasks();
    if (tasks[symbol] && tasks[symbol].status === "pending") {
      flashError(`${symbol} is already being analysed.`);
      return;
    }

    input.value = "";
    input.focus();
    hideError();

    try {
      const resp = await fetch("/analyse", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "",
        },
        body: `symbol=${encodeURIComponent(symbol)}`,
      });

      const data = await resp.json();

      if (!resp.ok) {
        let msg = data.error || "Something went wrong.";
        if (data.reset_at) {
          const resetDate = new Date(data.reset_at);
          const localTime = resetDate.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            hour12: true,
            timeZoneName: "short",
          });
          // Localize any "3:00 PM PHT" reset tail.
          // The server may say "after 3:00 PM PHT" or
          // "tomorrow after 3:00 PM PHT".
          msg = msg
            .replace(
              /Your quota resets .+$/,
              `Your quota resets at ${localTime}.`
            )
            .replace(
              /(tomorrow )?after 3:00\u202fPM PHT\.?$/,
              (_, tom) => `${tom || ""}after ${localTime}.`
            );
        }
        flashError(msg, data.symbol);
        return;
      }

      if (data.status === "cached") {
        // Fresh report exists — navigate straight to it
        window.location.href = `/report/${data.symbol}`;
        return;
      }

      if (data.task_id) {
        // Analysis dispatched — open SSE stream for real-time updates
        addTask(symbol, data.task_id);
        renderTracker();
        streamStatus(data.task_id, symbol);
      } else {
        flashError(data.error || "Something went wrong.");
      }
    } catch {
      flashError("Failed to connect to the server.");
    }
  });

  /* ================================================================== */
  /*  SSE streaming (primary) + polling fallback                        */
  /* ================================================================== */

  /** Active EventSource handles keyed by symbol so we can close them. */
  const activeSources = {};

  /**
   * Open an SSE connection to /stream/<taskId> for real-time progress.
   * Falls back to polling if EventSource is unsupported or the
   * connection fails.
   */
  function streamStatus(taskId, symbol) {
    if (typeof EventSource === "undefined") {
      // Browser doesn't support SSE — fall back to polling
      pollStatus(taskId, symbol);
      return;
    }

    const source = new EventSource(`/stream/${taskId}`);
    activeSources[symbol] = source;

    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Update the step indicator with the real server label.
        if (!data.done) {
          updateTask(symbol, "pending", {
            step: data.step || 0,
            stepLabel: data.label || undefined,
          });
          renderTracker();
        }

        if (data.done) {
          source.close();
          delete activeSources[symbol];

          if (data.error) {
            const failLabel = data.label || "Analysis";
            updateTask(symbol, "error", { msg: `Failed ${failLabel.toLowerCase()}: ${data.error}` });
          } else {
            updateTask(symbol, "done", {
              verdict: data.verdict || "",
              score: data.score,
              report_id: data.report_id,
            });
            setTimeout(() => { removeTask(symbol); renderTracker(); }, 8000);
          }
          renderTracker();
        }
      } catch { /* malformed event — ignore */ }
    };

    source.onerror = () => {
      // SSE connection failed — close and fall back to polling.
      source.close();
      delete activeSources[symbol];
      pollStatus(taskId, symbol);
    };
  }

  /**
   * Polling fallback — used when SSE is unavailable or drops.
   */
  function pollStatus(taskId, symbol) {
    const interval = setInterval(async () => {
      try {
        const resp = await fetch(`/status/${taskId}`);
        const data = await resp.json();

        if (data.done) {
          clearInterval(interval);
          if (data.error) {
            updateTask(symbol, "error", { msg: `Failed: ${data.error}` });
          } else {
            updateTask(symbol, "done", { verdict: data.verdict || "", score: data.score });
            // Auto-dismiss completed tasks after 8 seconds
            setTimeout(() => { removeTask(symbol); renderTracker(); }, 8000);
          }
          renderTracker();
        }
      } catch {
        // Network blip — keep polling
      }
    }, POLL_MS);
  }

  /* ================================================================== */
  /*  Error toast                                                       */
  /* ================================================================== */

  function flashError(msg, symbol) {
    errorArea.classList.remove("is-fading");
    errorArea.style.display = "block";
    if (symbol) {
      errorText.innerHTML =
        msg +
        ` <a href="/report/${encodeURIComponent(symbol)}" class="error-report-link">View last report &rarr;</a>`;
    } else {
      errorText.textContent = msg;
    }
    if (flashError._fadeTimer) clearTimeout(flashError._fadeTimer);
    if (flashError._hideTimer) clearTimeout(flashError._hideTimer);
    // Start the CSS fade after the read window, then hide once it ends.
    flashError._fadeTimer = setTimeout(() => {
      errorArea.classList.add("is-fading");
      flashError._hideTimer = setTimeout(hideError, 600);
    }, 8000);
  }

  function hideError() {
    errorArea.style.display = "none";
    errorArea.classList.remove("is-fading");
  }

  /* ================================================================== */
  /*  Boot: restore persisted tasks & resume polling                    */
  /* ================================================================== */

  // Boot: purge done/error tasks from previous sessions, restore pending ones
  const boot = loadTasks();
  for (const sym of Object.keys(boot)) {
    if (boot[sym].status === "done" || boot[sym].status === "error") {
      delete boot[sym];
    }
  }
  saveTasks(boot);
  renderTracker();

  // Resume SSE streams for any tasks still pending
  for (const sym of Object.keys(boot)) {
    if (boot[sym].status === "pending" && boot[sym].taskId) {
      streamStatus(boot[sym].taskId, sym);
    }
  }

  // Calibrate carousel speed on initial load
  calibrateCarousel();

  /* ================================================================== */
  /*  Quick-pick ticker chips → fill the input and submit               */
  /* ================================================================== */

  document.querySelectorAll(".quick-pick").forEach((btn) => {
    btn.addEventListener("click", () => {
      input.value = btn.dataset.symbol;
      form.dispatchEvent(new Event("submit", { cancelable: true }));
    });
  });
});
