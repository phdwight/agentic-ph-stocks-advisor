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
  container.className = "range-bar-card";

  const label = document.createElement("div");
  label.className = "range-bar-label";
  const labelTitle = document.createElement("span");
  labelTitle.textContent = "52-Week Range";
  label.appendChild(labelTitle);
  if (current) {
    const curSpan = document.createElement("span");
    curSpan.className = "range-bar-current";
    curSpan.textContent = `Current: ₱${current.toLocaleString()}`;
    label.appendChild(curSpan);
  }

  const track = document.createElement("div");
  track.className = "range-bar-track";

  // Gradient fill
  const fill = document.createElement("div");
  fill.className = "range-bar-fill";
  const range = high - low;
  const fillPct = current ? Math.min(100, Math.max(0, ((current - low) / range) * 100)) : 50;
  fill.style.width = `${fillPct}%`;

  // Current position marker
  if (current) {
    const marker = document.createElement("div");
    marker.className = "range-bar-marker";
    marker.style.left = `${fillPct}%`;
    track.appendChild(marker);
  }

  track.insertBefore(fill, track.firstChild);

  const ends = document.createElement("div");
  ends.className = "range-bar-ends";
  const lowSpan = document.createElement("span");
  lowSpan.textContent = `₱${low.toLocaleString()}`;
  const highSpan = document.createElement("span");
  highSpan.textContent = `₱${high.toLocaleString()}`;
  ends.replaceChildren(lowSpan, highSpan);

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

        const cls = sentiment === true
          ? "pct-badge-pos"
          : sentiment === false
          ? "pct-badge-neg"
          : "pct-badge-neutral";
        const arrow = sentiment === true ? "↑" : sentiment === false ? "↓" : "";

        return `<span class="pct-badge ${cls}">${arrow}${match}</span>`;
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
        return `<strong>${label}</strong>: <span class="ratio-value">${value}</span>`;
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
        return `${prefix}<span class="price-highlight">${price}</span>`;
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
  const rootStyles = getComputedStyle(document.documentElement);
  const stroke = up
    ? (rootStyles.getPropertyValue("--buy").trim() || "#059669")
    : (rootStyles.getPropertyValue("--sell").trim() || "#dc2626");
  const fillTop = up
    ? (rootStyles.getPropertyValue("--buy-tint").trim() || "rgba(5,150,105,0.16)")
    : (rootStyles.getPropertyValue("--sell-tint").trim() || "rgba(220,38,38,0.16)");
  const sellColor = rootStyles.getPropertyValue("--sell").trim() || "#dc2626";
  const cardBg = rootStyles.getPropertyValue("--card").trim() || "#ffffff";
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
              rx="3" fill="rgba(220,38,38,0.08)"/>
        <rect x="${startX.toFixed(1)}" y="2" width="${rectW.toFixed(1)}" height="${h - 4}"
              rx="3" fill="url(#suspended-hatch)" stroke="rgba(220,38,38,0.28)" stroke-width="1"/>
        <line x1="${startX.toFixed(1)}" y1="2" x2="${startX.toFixed(1)}" y2="${h - 2}"
              stroke="${sellColor}" stroke-width="1.5" stroke-dasharray="3 2"/>
        <line x1="${startX.toFixed(1)}" y1="${frozenY.toFixed(1)}" x2="${endX.toFixed(1)}" y2="${frozenY.toFixed(1)}"
              stroke="${sellColor}" stroke-width="1.75" stroke-dasharray="4 3"/>
        <circle cx="${endX.toFixed(1)}" cy="${frozenY.toFixed(1)}" r="3"
                fill="${cardBg}" stroke="${sellColor}" stroke-width="1.75"/>
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
          <div class="trendline-suspended-overlay" style="left:${centerPct.toFixed(1)}%;">
            <div>⏸ SUSPENDED · NO TRADING</div>
            <div class="trendline-suspended-sub">${rangeSubtext}</div>
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
      <div class="trendline-legend">
        <span class="trendline-legend-item">
          <span class="trendline-legend-line" style="background:${stroke};"></span>
          <span>Active trading (${lastTradeText})</span>
        </span>
        <span class="trendline-legend-suspended">
          <span class="trendline-legend-swatch"></span>
          <span>Suspended — no trading (${suspWindowText} · ${totalSuspendedDays} trading days)</span>
        </span>
      </div>
    `;
  }

  const container = document.createElement("div");
  container.className = totalSuspendedDays > 0 ? "trendline-card trendline-card-suspended" : "trendline-card";

  const sign = changePct >= 0 ? "+" : "";
  const ticks = buildMonthTicks(prices.length, reportDate);
  const xAxisHtml = ticks
    .map((t) => {
      const shift = t.pct <= 0 ? "0" : t.pct >= 100 ? "-100%" : "-50%";
      return `<span class="trendline-xtick" style="left:${t.pct}%;transform:translateX(${shift});">${t.text}</span>`;
    })
    .join("");

  const headerBadgeHtml =
    totalSuspendedDays > 0
      ? `<span class="trendline-suspended-pill">⏸ Trading Suspended (${totalSuspendedDays}d no trades)</span>`
      : "";

  container.innerHTML = `
    <div class="trendline-header">
      <span class="trendline-title-group">
        <span>1-Year Trend</span>
        ${headerBadgeHtml}
      </span>
      <span class="trendline-change ${up ? "is-up" : "is-down"}">${up ? "↑" : "↓"} ${sign}${changePct.toFixed(1)}%</span>
    </div>
    <div class="trendline-body">
      <div class="trendline-yaxis">
        <span>${money(max)}</span>
        <span>${money(min)}</span>
      </div>
      <div class="trendline-plot-col">
        <div class="trendline-plot">
          <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height="72"
               role="img" aria-label="1-year price trend, ${sign}${changePct.toFixed(1)} percent${totalSuspendedDays > 0 ? `, ${totalSuspendedDays} suspended trading days with no trading` : ""}">
            <defs>
              <pattern id="suspended-hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="8" stroke="rgba(220,38,38,0.22)" stroke-width="2.5"/>
              </pattern>
            </defs>
            ${tradedSvg}
            ${suspendedSvg}
            <circle cx="${lastValidX.toFixed(1)}" cy="${lastValidY.toFixed(1)}" r="3.2" fill="${stroke}" stroke="${cardBg}" stroke-width="1"/>
          </svg>
          ${suspendedOverlaysHtml}
        </div>
        <div class="trendline-xaxis">${xAxisHtml}</div>
      </div>
    </div>
    ${legendHtml}
  `;

  return container;
}

