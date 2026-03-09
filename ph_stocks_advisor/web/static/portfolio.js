/**
 * PH Stocks Advisor — Portfolio holdings modal & analysis.
 *
 * Provides the interactive modal for elevated users to enter their
 * stock position (shares + average cost), saves it via the API,
 * triggers a personalised portfolio analysis, and displays the
 * result inline in the report page.
 */

document.addEventListener("DOMContentLoaded", () => {
  const POLL_MS = 3000;

  const portfolioBtn = document.getElementById("portfolio-btn");
  const modal = document.getElementById("holdings-modal");
  const modalClose = document.getElementById("modal-close");
  const form = document.getElementById("holdings-form");
  const deleteBtn = document.getElementById("holding-delete-btn");
  const modalError = document.getElementById("modal-error");
  const modalProgress = document.getElementById("modal-progress");
  const portfolioSection = document.getElementById("portfolio-section");
  const portfolioBody = document.getElementById("portfolio-analysis-body");

  if (!portfolioBtn || !modal) return;

  // Extract symbol from the page heading.
  const symbolEl = document.querySelector(".report-header-title h1");
  const symbol = symbolEl
    ? symbolEl.textContent.replace("Stock Analysis", "").trim()
    : "";

  /* ================================================================ */
  /*  Modal open / close                                              */
  /* ================================================================ */

  portfolioBtn.addEventListener("click", () => {
    modal.style.display = "flex";
    modalError.style.display = "none";
    form.style.display = "";
    modalProgress.style.display = "none";
  });

  modalClose.addEventListener("click", () => {
    modal.style.display = "none";
  });

  // Close on overlay click.
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.style.display = "none";
  });

  // Close on Escape.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal.style.display !== "none") {
      modal.style.display = "none";
    }
  });

  /* ================================================================ */
  /*  Delete holding                                                  */
  /* ================================================================ */

  if (deleteBtn) {
    deleteBtn.addEventListener("click", async () => {
      deleteBtn.disabled = true;
      try {
        const resp = await fetch(`/api/holdings/${encodeURIComponent(symbol)}`, {
          method: "DELETE",
        });
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          showError(data.error || "Failed to delete holding.");
          return;
        }
        // Close modal and hide portfolio section.
        modal.style.display = "none";
        if (portfolioSection) portfolioSection.style.display = "none";
        // Remove the delete button (no holding left).
        deleteBtn.remove();
        // Clear form fields.
        document.getElementById("holding-shares").value = "";
        document.getElementById("holding-avg-cost").value = "";
      } finally {
        deleteBtn.disabled = false;
      }
    });
  }

  /* ================================================================ */
  /*  Save holding + trigger analysis                                 */
  /* ================================================================ */

  // Track which submit button was clicked.
  let submitAction = "analyse"; // default
  const saveBtn = document.getElementById("holding-save-btn");
  const analyseBtn = document.getElementById("holding-analyse-btn");

  if (saveBtn) {
    saveBtn.addEventListener("click", () => { submitAction = "save"; });
  }
  if (analyseBtn) {
    analyseBtn.addEventListener("click", () => { submitAction = "analyse"; });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    modalError.style.display = "none";

    const shares = parseFloat(document.getElementById("holding-shares").value);
    const avgCost = parseFloat(document.getElementById("holding-avg-cost").value);

    if (!shares || shares <= 0 || !avgCost || avgCost <= 0) {
      showError("Please enter valid positive numbers for shares and average cost.");
      return;
    }

    if (saveBtn) saveBtn.disabled = true;
    if (analyseBtn) analyseBtn.disabled = true;

    try {
      // 1. Save the holding.
      const saveResp = await fetch(`/api/holdings/${encodeURIComponent(symbol)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shares, avg_cost: avgCost }),
      });
      if (!saveResp.ok) {
        const data = await saveResp.json().catch(() => ({}));
        showError(data.error || "Failed to save holding.");
        return;
      }

      // If user only clicked "Save", close modal and stop here.
      if (submitAction === "save") {
        modal.style.display = "none";
        return;
      }

      // 2. Trigger portfolio analysis.
      const analyseResp = await fetch(
        `/api/portfolio-analyse/${encodeURIComponent(symbol)}`,
        { method: "POST" }
      );
      if (!analyseResp.ok) {
        const data = await analyseResp.json().catch(() => ({}));
        // On cooldown (429): save succeeded, close modal, show info inline.
        if (analyseResp.status === 429) {
          modal.style.display = "none";
          return;
        }
        showError(data.error || "Failed to start portfolio analysis.");
        return;
      }

      const analyseData = await analyseResp.json();
      const taskId = analyseData.task_id;

      // 3. Close the modal and show inline progress on the page.
      modal.style.display = "none";
      showInlineProgress();

      // 4. Poll for completion.
      pollTask(taskId, shares, avgCost);
    } catch (err) {
      showError("Network error. Please try again.");
    } finally {
      if (saveBtn) saveBtn.disabled = false;
      if (analyseBtn) analyseBtn.disabled = false;
      submitAction = "analyse"; // reset default
    }
  });

  /* ================================================================ */
  /*  Poll the Celery task until done                                 */
  /* ================================================================ */

  function pollTask(taskId, shares, avgCost) {
    const iv = setInterval(async () => {
      try {
        const resp = await fetch(`/status/${taskId}`);
        const data = await resp.json();

        if (data.done || data.state === "SUCCESS" || data.state === "FAILURE") {
          clearInterval(iv);

          if (data.state === "FAILURE" || data.error) {
            hideInlineProgress();
            showInlineError(data.error || "Analysis failed. Please try again.");
            return;
          }

          // Fetch the portfolio report.
          const reportResp = await fetch(
            `/api/portfolio-report/${encodeURIComponent(symbol)}`
          );
          const reportData = await reportResp.json();

          hideInlineProgress();

          if (reportData.report && reportData.report.analysis) {
            displayPortfolioReport(reportData.report, shares, avgCost);
          }
        }
      } catch {
        // Ignore transient fetch errors; keep polling.
      }
    }, POLL_MS);
  }

  /* ================================================================ */
  /*  Display the portfolio report inline                             */
  /* ================================================================ */

  function displayPortfolioReport(report, shares, avgCost) {
    if (!portfolioSection || !portfolioBody) return;

    // Use the server-rendered HTML (same converter as the main report).
    const html = report.analysis_html || report.analysis || "";
    const meta = `<p class="portfolio-meta">
      Position: ${Number(shares).toLocaleString()} shares @ ₱${Number(avgCost).toFixed(4)}
      · Generated: ${report.created_at ? new Date(report.created_at).toLocaleString() : "just now"}
    </p>`;
    portfolioBody.innerHTML = html + meta;
    portfolioSection.style.display = "";

    // Smooth scroll to the new section.
    portfolioSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ================================================================ */
  /*  Helpers                                                         */
  /* ================================================================ */

  function showError(msg) {
    modalError.textContent = msg;
    modalError.style.display = "block";
  }

  /** Show progress indicator inline on the report page. */
  function showInlineProgress() {
    if (!portfolioSection || !portfolioBody) return;
    portfolioBody.innerHTML = `
      <div class="portfolio-inline-progress">
        <div class="spinner"></div>
        <p>Running personalised analysis…</p>
      </div>`;
    portfolioSection.style.display = "";
    portfolioSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** Remove inline progress indicator. */
  function hideInlineProgress() {
    const el = portfolioBody ? portfolioBody.querySelector(".portfolio-inline-progress") : null;
    if (el) el.remove();
  }

  /** Show an error message inline in the portfolio section. */
  function showInlineError(msg) {
    if (!portfolioSection || !portfolioBody) return;
    portfolioBody.innerHTML = `<p class="portfolio-inline-error">${msg}</p>`;
    portfolioSection.style.display = "";
  }
});
