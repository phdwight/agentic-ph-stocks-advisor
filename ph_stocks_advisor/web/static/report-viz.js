/**
 * PH Stocks Advisor — Report Data Visualization Enhancements.
 *
 * Scans rendered report sections for numeric patterns (52-week range,
 * percentage changes, ratios) and injects visual components like
 * progress bars, trend badges, and sparkline-style indicators.
 */

document.addEventListener("DOMContentLoaded", () => {
  enhanceReportSections();
  injectMovementTrendline();
});

function enhanceReportSections() {
  const sections = document.querySelectorAll(".section-body");
  sections.forEach((section) => {
    enhance52WeekRange(section);
    enhancePercentages(section);
    enhanceRatioValues(section);
    enhancePriceValues(section);
  });
}

/* ====================================================================== */
/*  52-Week Range Progress Bar                                             */
/* ====================================================================== */

function enhance52WeekRange(section) {
  const paragraphs = section.querySelectorAll("p");
  paragraphs.forEach((p) => {
    const text = p.textContent;

    // Match patterns like "52-week low: ₱12.50" and "52-week high: ₱25.00"
    // or "52-Week Range: ₱12.50 – ₱25.00"    or "52-week low/high"
    const rangeMatch = text.match(
      /52[- ]?week\s+(?:range|low|high)/i
    );
    if (!rangeMatch) return;

    // Try to extract low, high, and current price from surrounding context
    const allText = section.textContent;

    const lowMatch = allText.match(
      /52[- ]?week\s+low[:\s]*(?:PHP|₱|PhP)?\s*([\d,.]+)/i
    );
    const highMatch = allText.match(
      /52[- ]?week\s+high[:\s]*(?:PHP|₱|PhP)?\s*([\d,.]+)/i
    );
    const currentMatch = allText.match(
      /current\s+price[:\s]*(?:PHP|₱|PhP)?\s*([\d,.]+)/i
    );

    if (lowMatch && highMatch) {
      const low = parseFloat(lowMatch[1].replace(/,/g, ""));
      const high = parseFloat(highMatch[1].replace(/,/g, ""));
      const current = currentMatch
        ? parseFloat(currentMatch[1].replace(/,/g, ""))
        : null;

      if (high > low) {
        const bar = create52WeekBar(low, high, current);
        // Insert after the paragraph
        p.parentNode.insertBefore(bar, p.nextSibling);
      }
    }
  });
}

function create52WeekBar(low, high, current) {
  const container = document.createElement("div");
  container.style.cssText = `
    margin: 1rem 0 1.2rem;
    padding: 1rem 1.2rem;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 10px;
  `;

  const label = document.createElement("div");
  label.style.cssText = `
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
    font-size: 0.72rem;
    font-weight: 600;
    color: #a0a6be;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  `;
  label.innerHTML = `
    <span>52-Week Range</span>
    ${current ? `<span style="color:#2E4A56;">Current: ₱${current.toLocaleString()}</span>` : ""}
  `;

  const track = document.createElement("div");
  track.style.cssText = `
    position: relative;
    height: 6px;
    background: rgba(255,255,255,0.06);
    border-radius: 3px;
    overflow: visible;
  `;

  // Gradient fill
  const fill = document.createElement("div");
  const range = high - low;
  const fillPct = current ? Math.min(100, Math.max(0, ((current - low) / range) * 100)) : 50;
  fill.style.cssText = `
    height: 100%;
    width: ${fillPct}%;
    background: linear-gradient(90deg, #D85E52, #C4A050, #3D8A7E);
    border-radius: 3px;
    transition: width 1s ease-out;
  `;

  // Current position marker
  if (current) {
    const marker = document.createElement("div");
    marker.style.cssText = `
      position: absolute;
      top: -5px;
      left: ${fillPct}%;
      transform: translateX(-50%);
      width: 16px;
      height: 16px;
      background: #2E4A56;
      border: 2px solid #F5F1ED;
      border-radius: 50%;
      box-shadow: 0 0 8px rgba(61,138,126,0.3);
      z-index: 2;
    `;
    track.appendChild(marker);
  }

  track.insertBefore(fill, track.firstChild);

  const ends = document.createElement("div");
  ends.style.cssText = `
    display: flex;
    justify-content: space-between;
    margin-top: 0.4rem;
    font-size: 0.72rem;
    color: #6b7088;
    font-variant-numeric: tabular-nums;
  `;
  ends.innerHTML = `
    <span>₱${low.toLocaleString()}</span>
    <span>₱${high.toLocaleString()}</span>
  `;

  container.appendChild(label);
  container.appendChild(track);
  container.appendChild(ends);

  return container;
}

/* ====================================================================== */
/*  Percentage Change Badges                                               */
/* ====================================================================== */

function enhancePercentages(section) {
  const paragraphs = section.querySelectorAll("p, li");
  paragraphs.forEach((el) => {
    // Only process text nodes, not already enhanced items
    if (el.querySelector(".pct-badge")) return;

    const html = el.innerHTML;
    // Use plain textContent for context detection — innerHTML may contain
    // <strong> / <em> tags that inflate character positions and cause the
    // surrounding-text window to miss nearby directional words.
    const plainText = el.textContent.toLowerCase();

    // Match patterns like "+15.2%", "-7.8%", "15.2%" with context
    const enhanced = html.replace(
      /([+-]?\d+\.?\d*)\s*%/g,
      (match, num) => {
        const value = parseFloat(num);
        if (isNaN(value)) return match;

        // Find this percentage in the plain text and grab context around it.
        const pctLiteral = match.toLowerCase();
        const plainIdx = plainText.indexOf(pctLiteral);
        const ctxStart = Math.max(0, (plainIdx >= 0 ? plainIdx : 0) - 80);
        const ctxEnd = Math.min(plainText.length, (plainIdx >= 0 ? plainIdx : 0) + match.length + 80);
        const surroundingText = plainText.substring(ctxStart, ctxEnd);

        // Decline vocabulary near a positive number means the sentiment is
        // actually negative (e.g. "16% below its 52-week high", "drawdown of
        // 30%", "8.8% slide", "12.8% rout"). Stems use \w* so inflections
        // (declines, dropped, falling) and financial terms are all caught.
        // Conversely, gain vocabulary near a negative number flips to positive.
        //
        // EXCEPTION: an explicit "+" prefix (e.g. "+28.9%") is an
        // unambiguous positive signal from the LLM — honour it directly
        // and skip context-word heuristics.
        const negativeContext =
          /\b(below|down|drawdown|declin\w*|drop\w*|dip\w*|loss\w*|lost|f[ae]ll\w*|decreas\w*|lower|slid\w*|slump\w*|plung\w*|rout|sell-?off|selloff|selling|crash\w*|tumbl\w*|sank|sink\w*|retreat\w*|pullback|correction|shed|wiped|slipp\w*|slip)\b/;
        const positiveContext =
          /\b(above|up|gain\w*|rise\w*|rose|grew|grow\w*|increas\w*|higher|surge\w*|rall\w*|jump\w*|climb\w*|soar\w*)\b/;
        const hasExplicitSign = /^[+-]/.test(num.trim());

        let sentiment;  // true = positive, false = negative, null = neutral
        if (hasExplicitSign) {
          // Explicit +/- prefix is authoritative — no context override.
          sentiment = value > 0 ? true : value < 0 ? false : null;
        } else if (value > 0) {
          sentiment = negativeContext.test(surroundingText) ? false : true;
        } else if (value < 0) {
          sentiment = positiveContext.test(surroundingText) ? true : false;
        } else {
          sentiment = null;
        }

        const color = sentiment === true
          ? "#3D8A7E"
          : sentiment === false
          ? "#D85E52"
          : "#7A8E98";
        const bgColor = sentiment === true
          ? "rgba(61,138,126,0.10)"
          : sentiment === false
          ? "rgba(216,94,82,0.10)"
          : "rgba(122,142,152,0.08)";
        const arrow = sentiment === true ? "↑" : sentiment === false ? "↓" : "";

        return `<span class="pct-badge" style="
          display:inline-flex;align-items:center;gap:0.2rem;
          padding:0.1rem 0.45rem;border-radius:4px;
          background:${bgColor};color:${color};
          font-size:0.8rem;font-weight:600;
          font-variant-numeric:tabular-nums;
          white-space:nowrap;
        ">${arrow}${match}</span>`;
      }
    );

    if (enhanced !== html) {
      el.innerHTML = enhanced;
    }
  });
}

/* ====================================================================== */
/*  Financial Ratio Highlights                                             */
/* ====================================================================== */

function enhanceRatioValues(section) {
  const listItems = section.querySelectorAll("li");
  listItems.forEach((li) => {
    if (li.querySelector(".ratio-value")) return;

    const html = li.innerHTML;

    // Match patterns like "PE Ratio: 12.5" or "PB Ratio: 1.2"
    const enhanced = html.replace(
      /\b(P\/?E|P\/?B|PEG|Forward\s+P\/?E)\s*(?:Ratio)?[:\s]+(\d+\.?\d*)/gi,
      (match, label, value) => {
        return `<strong>${label}</strong>: <span class="ratio-value" style="
          display:inline-block;
          padding:0.1rem 0.4rem;
          background:rgba(74,128,160,0.10);
          color:#4A80A0;
          border-radius:4px;
          font-weight:600;
          font-size:0.82rem;
          font-variant-numeric:tabular-nums;
        ">${value}</span>`;
      }
    );

    if (enhanced !== html) {
      li.innerHTML = enhanced;
    }
  });
}

/* ====================================================================== */
/*  Price Value Formatting                                                 */
/* ====================================================================== */

function enhancePriceValues(section) {
  const paragraphs = section.querySelectorAll("p");
  paragraphs.forEach((p) => {
    if (p.querySelector(".price-highlight")) return;

    const html = p.innerHTML;

    // Match "₱123.45" or "PHP 123.45" price patterns in key contexts
    const enhanced = html.replace(
      /((?:current\s+price|fair\s+value|estimated\s+fair\s+value|graham\s+number)[:\s]*)((?:PHP|₱|PhP)\s*[\d,]+\.?\d*)/gi,
      (match, prefix, price) => {
        return `${prefix}<span class="price-highlight" style="
          font-weight:700;
          color:#2E4A56;
          font-size:1.05em;
          font-variant-numeric:tabular-nums;
        ">${price}</span>`;
      }
    );

    if (enhanced !== html) {
      p.innerHTML = enhanced;
    }
  });
}

/* ====================================================================== */
/*  1-Year Trend Line (Movement card)                                      */
/* ====================================================================== */

function injectMovementTrendline() {
  // The 1-year series is a snapshot embedded at render time on the agent grid
  // (a data attribute, not an inline script, to satisfy the page CSP). Blank
  // for legacy reports saved before the snapshot was captured.
  const grid = document.querySelector(".agent-grid[data-movement-prices]");
  let prices = [];
  try {
    prices = JSON.parse(grid?.dataset.movementPrices || "[]");
  } catch {
    prices = [];
  }
  if (!Array.isArray(prices) || prices.length < 2) return;

  // Find the "Price Movement Analysis" card by its agent name.
  const cards = document.querySelectorAll(".agent-card");
  let body = null;
  cards.forEach((card) => {
    if (body) return;
    const name = card.querySelector(".agent-name")?.textContent.toLowerCase() || "";
    if (name.includes("movement") || name.includes("trend")) {
      body = card.querySelector(".section-body");
    }
  });
  if (!body) return;

  const chart = createTrendline(prices, grid?.dataset.reportDate || "");
  if (!chart) return;
  body.insertBefore(chart, body.firstChild);
}

// Month labels for the x-axis. The series is a run of daily points spanning
// roughly one year ending at the report date, so horizontal position maps
// linearly onto that year. Returns evenly-spaced month ticks with their
// horizontal position (%) along the line.
function buildMonthTicks(n, endDateStr) {
  const end = endDateStr ? new Date(endDateStr) : null;
  if (!end || isNaN(end.getTime())) return [];
  const start = new Date(end.getFullYear() - 1, end.getMonth(), end.getDate());
  const span = end.getTime() - start.getTime();
  const dateAt = (frac) => new Date(start.getTime() + frac * span);
  const fmt = (m, withYear) =>
    m.toLocaleString("en-US", { month: "short" }) + (withYear ? " '" + String(m.getFullYear()).slice(2) : "");
  const count = 7;
  const ticks = [];
  for (let k = 0; k < count; k++) {
    const frac = k / (count - 1);
    const m = dateAt(frac);
    ticks.push({
      pct: frac * 100,
      text: fmt(m, k === 0 || k === count - 1 || m.getMonth() === 0),
    });
  }
  return ticks;
}

// Step backward `bdaysBack` Monday–Friday business days from the latest
// weekday on or before `endDateStr`. Matches `pd.bdate_range` in movement.py.
function bdateBeforeEnd(endDateStr, bdaysBack) {
  const end = endDateStr ? new Date(endDateStr) : null;
  if (!end || isNaN(end.getTime())) return null;
  const d = new Date(end.getFullYear(), end.getMonth(), end.getDate());
  while (d.getDay() === 0 || d.getDay() === 6) {
    d.setDate(d.getDate() - 1);
  }
  let remaining = Math.max(0, bdaysBack);
  while (remaining > 0) {
    d.setDate(d.getDate() - 1);
    if (d.getDay() !== 0 && d.getDay() !== 6) {
      remaining -= 1;
    }
  }
  return d;
}

function fmtShortDate(d) {
  if (!d || isNaN(d.getTime())) return "";
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function fmtCompactDate(d) {
  if (!d || isNaN(d.getTime())) return "";
  return (
    d.toLocaleString("en-US", { month: "short", day: "numeric" }) +
    " '" +
    String(d.getFullYear()).slice(2)
  );
}

function createTrendline(prices, reportDate) {
  const w = 520;
  const h = 96;
  const padX = 6;
  const padY = 12;

  const validEntries = [];
  prices.forEach((p, i) => {
    if (typeof p === "number" && Number.isFinite(p)) {
      validEntries.push({ index: i, price: p });
    }
  });
  if (validEntries.length === 0) return null;

  const validPrices = validEntries.map((e) => e.price);
  const min = Math.min(...validPrices);
  const max = Math.max(...validPrices);
  const range = max - min || 1;
  const stepX = (w - padX * 2) / Math.max(1, prices.length - 1);
  const xAt = (i) => padX + i * stepX;
  const yAt = (p) => padY + (1 - (p - min) / range) * (h - padY * 2);

  const firstPrice = validPrices[0];
  const lastEntry = validEntries[validEntries.length - 1];
  const lastPrice = lastEntry.price;
  const lastValidX = xAt(lastEntry.index);
  const lastValidY = yAt(lastPrice);

  const up = lastPrice >= firstPrice;
  const stroke = up ? "#3D8A7E" : "#D85E52";
  const fillTop = up ? "rgba(61,138,126,0.16)" : "rgba(216,94,82,0.16)";
  const changePct = firstPrice ? ((lastPrice - firstPrice) / firstPrice) * 100 : 0;
  const money = (v) => "₱" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Partition into contiguous traded segments and suspended (`null`) runs.
  const tradedSegments = [];
  let curSeg = [];
  const suspendedRuns = [];
  let curSusp = null;

  prices.forEach((p, i) => {
    if (typeof p === "number" && Number.isFinite(p)) {
      curSeg.push([xAt(i), yAt(p), i, p]);
      if (curSusp) {
        suspendedRuns.push(curSusp);
        curSusp = null;
      }
    } else {
      if (curSeg.length > 0) {
        tradedSegments.push(curSeg);
        curSeg = [];
      }
      if (!curSusp) {
        curSusp = { startIdx: i, endIdx: i, count: 1 };
      } else {
        curSusp.endIdx = i;
        curSusp.count += 1;
      }
    }
  });
  if (curSeg.length > 0) tradedSegments.push(curSeg);
  if (curSusp) suspendedRuns.push(curSusp);

  const totalSuspendedDays = suspendedRuns.reduce((sum, r) => sum + r.count, 0);

  const tradedSvg = tradedSegments
    .map((seg) => {
      if (seg.length < 2) return "";
      const linePath = seg
        .map(([x, y], idx) => `${idx === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`)
        .join(" ");
      const areaPath = `${linePath} L${seg[seg.length - 1][0].toFixed(1)} ${h - padY} L${seg[0][0].toFixed(1)} ${h - padY} Z`;
      return `
        <path d="${areaPath}" fill="${fillTop}" stroke="none"/>
        <path d="${linePath}" fill="none" stroke="${stroke}" stroke-width="2"
              stroke-linejoin="round" stroke-linecap="round"/>
      `;
    })
    .join("");

  let suspendedSvg = "";
  let suspendedOverlaysHtml = "";
  let legendHtml = "";

  if (suspendedRuns.length > 0) {
    const svgParts = [];
    const overlayParts = [];

    suspendedRuns.forEach((run) => {
      const anchorIdx = run.startIdx > 0 ? run.startIdx - 1 : run.startIdx;
      const startX = xAt(anchorIdx);
      const endX = xAt(run.endIdx);
      const anchorPrice =
        typeof prices[anchorIdx] === "number" && Number.isFinite(prices[anchorIdx])
          ? prices[anchorIdx]
          : lastPrice;
      const frozenY = yAt(anchorPrice);
      const rectW = Math.max(2, endX - startX);

      svgParts.push(`
        <rect x="${startX.toFixed(1)}" y="2" width="${rectW.toFixed(1)}" height="${h - 4}"
              rx="3" fill="rgba(216,94,82,0.08)"/>
        <rect x="${startX.toFixed(1)}" y="2" width="${rectW.toFixed(1)}" height="${h - 4}"
              rx="3" fill="url(#suspended-hatch)" stroke="rgba(216,94,82,0.28)" stroke-width="1"/>
        <line x1="${startX.toFixed(1)}" y1="2" x2="${startX.toFixed(1)}" y2="${h - 2}"
              stroke="#D85E52" stroke-width="1.5" stroke-dasharray="3 2"/>
        <line x1="${startX.toFixed(1)}" y1="${frozenY.toFixed(1)}" x2="${endX.toFixed(1)}" y2="${frozenY.toFixed(1)}"
              stroke="#D85E52" stroke-width="1.75" stroke-dasharray="4 3"/>
        <circle cx="${endX.toFixed(1)}" cy="${frozenY.toFixed(1)}" r="3"
                fill="#fff" stroke="#D85E52" stroke-width="1.75"/>
      `);

      const startPct = (anchorIdx / Math.max(1, prices.length - 1)) * 100;
      const endPct = (run.endIdx / Math.max(1, prices.length - 1)) * 100;
      const widthPct = endPct - startPct;
      const centerPct = (startPct + endPct) / 2;

      const bdaysFromEndToStart = (prices.length - 1 - run.startIdx);
      const bdaysFromEndToEnd = (prices.length - 1 - run.endIdx);
      const suspStartDate = bdateBeforeEnd(reportDate, bdaysFromEndToStart);
      const suspEndDate =
        run.endIdx === prices.length - 1 && reportDate
          ? new Date(reportDate)
          : bdateBeforeEnd(reportDate, bdaysFromEndToEnd);
      const rangeSubtext =
        suspStartDate && suspEndDate
          ? `${fmtCompactDate(suspStartDate)} – ${fmtCompactDate(suspEndDate)} · ${run.count}d`
          : `${run.count} trading days`;

      if (widthPct >= 14) {
        overlayParts.push(`
          <div style="position:absolute;top:50%;left:${centerPct.toFixed(1)}%;
            transform:translate(-50%,-50%);background:rgba(255,250,249,0.94);
            border:1px solid rgba(216,94,82,0.45);border-radius:6px;
            padding:0.16rem 0.45rem;font-size:0.6rem;font-weight:700;color:#B83B2E;
            letter-spacing:0.03em;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,0.06);
            pointer-events:none;text-align:center;line-height:1.25;">
            <div>⏸ SUSPENDED · NO TRADING</div>
            <div style="font-size:0.55rem;font-weight:600;color:#8c3b32;letter-spacing:0.01em;">${rangeSubtext}</div>
          </div>
        `);
      }
    });

    suspendedSvg = svgParts.join("");
    suspendedOverlaysHtml = overlayParts.join("");

    const lastRun = suspendedRuns[suspendedRuns.length - 1];
    const suspStart = bdateBeforeEnd(reportDate, prices.length - 1 - lastRun.startIdx);
    const lastTradedDate = bdateBeforeEnd(reportDate, prices.length - 1 - lastEntry.index);
    const suspEnd = reportDate ? new Date(reportDate) : null;
    const suspWindowText =
      suspStart && suspEnd
        ? `${fmtShortDate(suspStart)} – ${fmtShortDate(suspEnd)}`
        : `${totalSuspendedDays} trading days`;
    const lastTradeText = lastTradedDate
      ? `Last traded ${fmtShortDate(lastTradedDate)} at ${money(lastPrice)}`
      : `Last traded at ${money(lastPrice)}`;

    legendHtml = `
      <div style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:center;
        margin-top:0.55rem;padding-top:0.45rem;border-top:1px dashed rgba(46,74,86,0.12);
        font-size:0.65rem;color:#5a6075;">
        <span style="display:inline-flex;align-items:center;gap:0.35rem;">
          <span style="display:inline-block;width:14px;height:2.5px;background:${stroke};border-radius:2px;"></span>
          <span>Active trading (${lastTradeText})</span>
        </span>
        <span style="display:inline-flex;align-items:center;gap:0.35rem;color:#B83B2E;font-weight:600;">
          <span style="display:inline-block;width:14px;height:8px;background:repeating-linear-gradient(45deg,rgba(216,94,82,0.35),rgba(216,94,82,0.35) 2px,rgba(216,94,82,0.1) 2px,rgba(216,94,82,0.1) 4px);border:1px solid rgba(216,94,82,0.55);border-radius:2px;"></span>
          <span>Suspended — no trading (${suspWindowText} · ${totalSuspendedDays} trading days)</span>
        </span>
      </div>
    `;
  }

  const container = document.createElement("div");
  container.className = "trendline-card";
  container.style.cssText = `
    margin: 0 0 1.2rem;
    padding: 0.85rem 1rem;
    background: rgba(46,74,86,0.03);
    border: 1px solid ${totalSuspendedDays > 0 ? "rgba(216,94,82,0.28)" : "rgba(46,74,86,0.08)"};
    border-radius: 10px;
  `;

  const sign = changePct >= 0 ? "+" : "";
  const ticks = buildMonthTicks(prices.length, reportDate);
  const xAxisHtml = ticks
    .map((t) => {
      const shift = t.pct <= 0 ? "0" : t.pct >= 100 ? "-100%" : "-50%";
      return `<span style="position:absolute;left:${t.pct}%;transform:translateX(${shift});white-space:nowrap;">${t.text}</span>`;
    })
    .join("");

  const headerBadgeHtml =
    totalSuspendedDays > 0
      ? `<span style="background:rgba(216,94,82,0.12);color:#B83B2E;border:1px solid rgba(216,94,82,0.35);
           border-radius:999px;padding:0.1rem 0.48rem;font-size:0.64rem;font-weight:700;
           letter-spacing:0.02em;text-transform:none;">⏸ Trading Suspended (${totalSuspendedDays}d no trades)</span>`
      : "";

  container.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.4rem;
      margin-bottom:0.5rem;font-size:0.72rem;font-weight:600;color:#6b7088;
      text-transform:uppercase;letter-spacing:0.04em;">
      <span style="display:inline-flex;align-items:center;gap:0.45rem;flex-wrap:wrap;">
        <span>1-Year Trend</span>
        ${headerBadgeHtml}
      </span>
      <span style="color:${stroke};font-variant-numeric:tabular-nums;">${up ? "↑" : "↓"} ${sign}${changePct.toFixed(1)}%</span>
    </div>
    <div style="display:flex;align-items:stretch;gap:0.4rem;">
      <div style="display:flex;flex-direction:column;justify-content:space-between;
        height:72px;font-size:0.66rem;color:#8a8f9c;text-align:right;
        font-variant-numeric:tabular-nums;min-width:3.4em;">
        <span>${money(max)}</span>
        <span>${money(min)}</span>
      </div>
      <div style="flex:1;min-width:0;">
        <div style="position:relative;height:72px;">
          <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height="72"
               role="img" aria-label="1-year price trend, ${sign}${changePct.toFixed(1)} percent${totalSuspendedDays > 0 ? `, ${totalSuspendedDays} suspended trading days with no trading` : ""}">
            <defs>
              <pattern id="suspended-hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(216,94,82,0.22)" stroke-width="2.5"/>
              </pattern>
            </defs>
            ${tradedSvg}
            ${suspendedSvg}
            <circle cx="${lastValidX.toFixed(1)}" cy="${lastValidY.toFixed(1)}" r="3.2" fill="${stroke}" stroke="#fff" stroke-width="1"/>
          </svg>
          ${suspendedOverlaysHtml}
        </div>
        <div style="position:relative;height:1em;margin-top:0.35rem;font-size:0.66rem;
          color:#8a8f9c;font-variant-numeric:tabular-nums;">${xAxisHtml}</div>
      </div>
    </div>
    ${legendHtml}
  `;

  return container;
}

