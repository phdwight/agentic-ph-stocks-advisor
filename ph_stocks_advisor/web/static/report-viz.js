/**
 * PH Stocks Advisor — Report Data Visualization Enhancements.
 *
 * Scans rendered report sections for numeric patterns (52-week range,
 * percentage changes, ratios) and injects visual components like
 * progress bars, trend badges, and sparkline-style indicators.
 */

document.addEventListener("DOMContentLoaded", () => {
  enhanceReportSections();
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
    color: #9ca0b0;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  `;
  label.innerHTML = `
    <span>52-week range</span>
    ${current ? `<span style="color:#e4e5eb;">Current: ₱${current.toLocaleString()}</span>` : ""}
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
    background: linear-gradient(90deg, #f87171, #fbbf24, #34d399);
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
      background: #e4e5eb;
      border: 2px solid #0f1117;
      border-radius: 50%;
      box-shadow: 0 0 8px rgba(108,99,255,0.4);
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
    color: #6b6f82;
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

    // Match patterns like "+15.2%", "-7.8%", "15.2%" with context
    const enhanced = html.replace(
      /([+-]?\d+\.?\d*)\s*%/g,
      (match, num) => {
        const value = parseFloat(num);
        if (isNaN(value)) return match;

        const isPositive = value > 0;
        const isNegative = value < 0;
        const color = isPositive
          ? "#34d399"
          : isNegative
          ? "#f87171"
          : "#9ca0b0";
        const bgColor = isPositive
          ? "rgba(52,211,153,0.1)"
          : isNegative
          ? "rgba(248,113,113,0.1)"
          : "rgba(156,160,176,0.1)";
        const arrow = isPositive ? "↑" : isNegative ? "↓" : "";

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
          background:rgba(99,179,237,0.1);
          color:#63b3ed;
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
          color:#e4e5eb;
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
