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
  }

  function removeTask(symbol) {
    const tasks = loadTasks();
    const task = tasks[symbol];
    // If the task completed successfully, add it to the "Previously Analysed" chips
    if (task && task.status === "done" && task.verdict) {
      addChipToRecent(symbol, task.verdict);
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

  function addChipToRecent(symbol, verdict) {
    const chipsContainer = document.getElementById("stock-chips");
    const recentSection = document.getElementById("recent-stocks");
    if (!chipsContainer || !recentSection) return;

    // Don't add a duplicate
    if (chipsContainer.querySelector(`[data-symbol="${symbol}"]`)) return;

    // Show the section if it was hidden (no previous stocks)
    recentSection.style.display = "";

    const isBuy = verdict.toUpperCase() === "BUY";
    const now = new Date();
    const monthNames = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const dateStr = `${monthNames[now.getMonth()]} ${String(now.getDate()).padStart(2, "0")}`;

    const chip = document.createElement("a");
    chip.href = `/report/${symbol}`;
    chip.className = "stock-chip stock-chip-new";
    chip.dataset.symbol = symbol;
    chip.innerHTML = `
      <span class="chip-symbol">${symbol}</span>
      <span class="chip-verdict badge-sm ${isBuy ? "buy" : "not-buy"}">${isBuy ? "BUY" : "NOT BUY"}</span>
      <span class="chip-date">${dateStr}</span>`;

    // Prepend chip then let calibrateCarousel redistribute
    chipsContainer.prepend(chip);
    calibrateCarousel();
  }

  /* ================================================================== */
  /*  Render the full tracker from stored state                         */
  /* ================================================================== */

  const cardRefs = {}; // symbol → DOM element

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
        const stepLabels = ["Queued", "Fetching data", "Running agents", "Consolidating", "Saving report"];
        const stepLabel = stepLabels[Math.min(step, stepLabels.length - 1)];
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
        row.className = "tracker-row tracker-row-error";
        row.innerHTML = `
          <td><span class="chip-symbol">${sym}</span></td>
          <td><span class="tracker-status-pill error-pill">✕ Failed</span></td>
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
        try {
          await fetch(`/cancel/${taskId}`, { method: "POST" });
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
    const tasks = loadTasks();
    let dirty = false;

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

    // Advance step indicators based on elapsed time
    for (const sym of Object.keys(tasks)) {
      const t = tasks[sym];
      if (t.status !== "pending") continue;
      const elapsed = (Date.now() - t.ts) / 1000;
      let newStep = 0;
      if (elapsed > 45) newStep = 4;
      else if (elapsed > 30) newStep = 3;
      else if (elapsed > 15) newStep = 2;
      else if (elapsed > 5)  newStep = 1;
      if ((t.step || 0) !== newStep) {
        t.step = newStep;
        dirty = true;
      }
    }

    if (dirty) {
      saveTasks(tasks);
      renderTracker();
    }
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
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
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
          msg = msg.replace(
            /Your quota resets at .+$/,
            `Your quota resets at ${localTime}.`
          );
        }
        flashError(msg);
        return;
      }

      if (data.status === "cached") {
        // Fresh report exists — navigate straight to it
        window.location.href = `/report/${data.symbol}`;
        return;
      }

      if (data.task_id) {
        // Analysis dispatched — now add the card
        addTask(symbol, data.task_id);
        renderTracker();
        pollStatus(data.task_id, symbol);
      } else {
        flashError(data.error || "Something went wrong.");
      }
    } catch {
      flashError("Failed to connect to the server.");
    }
  });

  /* ================================================================== */
  /*  Polling                                                           */
  /* ================================================================== */

  function pollStatus(taskId, symbol) {
    const interval = setInterval(async () => {
      try {
        const resp = await fetch(`/status/${taskId}`);
        const data = await resp.json();

        if (data.done) {
          clearInterval(interval);
          if (data.error) {
            updateTask(symbol, "error", { msg: data.error });
          } else {
            updateTask(symbol, "done", { verdict: data.verdict || "" });
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

  function flashError(msg) {
    errorArea.style.display = "block";
    errorText.textContent = msg;
    setTimeout(hideError, 4000);
  }

  function hideError() {
    errorArea.style.display = "none";
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

  // Resume polling for any tasks still pending
  for (const sym of Object.keys(boot)) {
    if (boot[sym].status === "pending" && boot[sym].taskId) {
      pollStatus(boot[sym].taskId, sym);
    }
  }

  // Calibrate carousel speed on initial load
  calibrateCarousel();
});
