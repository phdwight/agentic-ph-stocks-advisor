# static

## Overview
Client-side JavaScript modules and PWA assets for service worker registration, theme sync, install prompts, and mobile navigation (`pwa.js` + `sw.js` + `icons/*`), submitting and tracking stock analyses with `localStorage` persistence and SSE/polling updates (`app.js`), managing user portfolio holdings and personalised analysis reports (`portfolio.js`), email-first WebAuthn passkey and 6-digit OTP authentication/registration workflows (`passkey.js`), and post-render DOM data visualization enhancements for stock analysis reports (`report-viz.js`).

## Interface
- `[pwa.js:(IIFE)]` - Registers `/sw.js`, manages light/dark theme persistence (`ph_advisor_theme`) and `<meta name="theme-color">` sync, handles `beforeinstallprompt` and iOS install guide sheet, wires mobile bottom tab bar and slide-over `.sidebar` drawer, intercepts authenticated `.sidebar-ticker` clicks on non-index pages (`POST /analyse` with `refresh_stale=1`), and toggles offline status banners.
- `[sw.js]` - Root-scoped PWA service worker pre-caching `/offline` and static shell assets under a content-hashed `CACHE_NAME`, serving HTML navigations network-first with `/offline` fallback and `/static/*` assets cache-first.
- `[app.js:STORAGE_KEY]`, `[app.js:STALE_NOTICE_KEY]`, `[app.js:POLL_MS]`, `[app.js:STALE_MS]`, `[app.js:NOTICE_AUTO_FADE_MS]`, `[app.js:ERROR_AUTO_FADE_MS]`, `[app.js:ERROR_FADE_DURATION]` - Task/notice storage keys (`'ph_advisor_tasks'`, `'ph_advisor_stale_notice'`) and timing constants (`3000ms` poll, `600,000ms` stale cutoff, `10000ms` notice fade delay, `8000ms` error fade delay, `600ms` fade duration).
- `[app.js:loadTasks]`, `[app.js:saveTasks]`, `[app.js:addTask]`, `[app.js:updateTask]`, `[app.js:removeTask]` - `localStorage` task state CRUD helpers.
- `[app.js:staleRefreshMessage]`, `[app.js:flashNotice]`, `[app.js:hideNotice]`, `[app.js:scheduleErrorAutoFade]`, `[app.js:flashError]`, `[app.js:hideError]` - Notice banner, error banner, and tracker row auto-fade UI helpers.
- `[app.js:calibrateCarousel]`, `[app.js:addChipToRecent]`, `[app.js:addToSidebar]`, `[app.js:bandChip]`, `[app.js:renderTracker]` - DOM rendering helpers for the stock-chip carousel, sidebar history list, score band badges, and active task tracker.
- `[app.js:streamStatus]`, `[app.js:pollStatus]` - Opens an `EventSource` SSE stream to `/stream/<taskId>` for real-time task progress, falling back to polling `/status/<taskId>` every `POLL_MS`.
- `[portfolio.js:POLL_MS]`, `[portfolio.js:_csrfToken]`, `[portfolio.js:window.__portfolioInflightTaskId]` - Polling interval (`3000ms`), CSRF meta tag reader, and optional boot-time in-flight task ID global.
- `[portfolio.js:pollTask]`, `[portfolio.js:displayPortfolioReport]`, `[portfolio.js:showError]`, `[portfolio.js:showInlineProgress]`, `[portfolio.js:hideInlineProgress]`, `[portfolio.js:showInlineError]` - Portfolio task polling and inline/modal DOM rendering helpers.
- `[passkey.js:(IIFE)]` - Self-executing module encapsulating passkey/OTP authentication and registration form logic.
- `[report-viz.js:enhanceReportSections]`, `[report-viz.js:enhance52WeekRange]`, `[report-viz.js:create52WeekBar]`, `[report-viz.js:enhancePercentages]`, `[report-viz.js:enhanceRatioValues]`, `[report-viz.js:enhancePriceValues]`, `[report-viz.js:injectMovementTrendline]`, `[report-viz.js:buildMonthTicks]`, `[report-viz.js:createTrendline]` - Global DOM enhancement functions that inject 52-week range bars, percentage/ratio/price badges, and 1-year SVG trendlines into rendered reports.

## Invariants
- `[app.js:DOMContentLoaded]` - Exits early if `#analyse-form` is not present in the DOM; purges tasks with status `'done'` or `'error'` persisted from previous sessions on initial page load.
- `[app.js:STALE_MS]` - Pending tasks older than 10 minutes (`600,000ms`) are purged during `renderTracker()`.
- `[app.js:form]` - Submitting a symbol that already has status `'pending'` in `localStorage` is rejected with `flashError`.
- `[app.js:calibrateCarousel]` - Stock chip counts `<= 5` render statically (`data-static`), `6–10` split across 2 marquee rows, and `> 10` split across 3 marquee rows.
- `[app.js:bandChip]` - Score bands map as: `>=80` `'STRONG BUY'` (`buy`), `>=60` `'BUY'` (`buy`), `>=40` `'WAIT'` (`wait`), `>=20` `'DON\'T BUY'` (`avoid`), `<20` `'AVOID'` (`avoid`); `null`/`NaN` score falls back to `verdict === 'BUY'`.
- `[portfolio.js:DOMContentLoaded]` - Exits early if `#portfolio-btn`, `#holdings-modal`, or a resolvable stock symbol (from `data-symbol`, `.rail-sym`, or `/report/<symbol>` URL path) is missing.
- `[portfolio.js:form]` - Holding submission requires both `shares` and `avgCost` to be valid positive numbers (`> 0`).
- `[portfolio.js:pollTask]` - When `portfolioTaskId` is present (chained base + portfolio tasks), the base task must finish before polling switches to `portfolioTaskId` and fetches the report.
- `[passkey.js:EMAIL_RE]` & `[passkey.js:handleSubmit]` - Email input must match `/^[^@\s]+@[^@\s]+\.[^@\s]+$/` and have length `<= 254`; verification code must match `/^\d{6}$/` during code verification stages.
- `[passkey.js:webauthnOK]` - Passkey registration (`regStage === 'form'`) and WebAuthn login (`doLogin`) require `!!window.PublicKeyCredential`.
- `[passkey.js:syncConsentState]` - `#pk-signin` button is disabled whenever creating a new account and `#pk-accept` is unchecked.
- `[report-viz.js:enhance52WeekRange]` - Only inserts a 52-week progress bar when both low and high prices are matched and `high > low` (`fillPct` is clamped to `[0, 100]`, or `50` if current price is absent).
- `[report-viz.js:injectMovementTrendline]` - Requires `data-movement-prices` on `.agent-grid` to parse as a JSON array with `prices.length >= 2` and a matching `.agent-card` whose `.agent-name` contains `'movement'` or `'trend'`.

## Side Effects
- `[app.js:localStorage]` - Reads and mutates `localStorage` key `'ph_advisor_tasks'` to persist task tracking state across page loads.
- `[app.js:form]`, `[app.js:renderTracker]`, `[app.js:streamStatus]`, `[app.js:pollStatus]` - Sends `POST /analyse`, `POST /cancel/<taskId>`, `EventSource GET /stream/<taskId>`, and `GET /status/<taskId>` requests, mutating the DOM and redirecting `window.location.href` when a cached report is returned.
- `[portfolio.js:deleteBtn]`, `[portfolio.js:form]`, `[portfolio.js:pollTask]` - Sends `DELETE /api/holdings/<symbol>`, `POST /api/holdings/<symbol>`, `POST /api/portfolio-analyse/<symbol>`, `GET /status/<taskId>`, and `GET /api/portfolio-report/<symbol>` requests with `X-CSRFToken` headers.
- `[passkey.js:postJSON]`, `[passkey.js:postCredential]`, `[passkey.js:doLogin]`, `[passkey.js:doRegister]` - Invokes browser WebAuthn APIs (`navigator.credentials.get` / `navigator.credentials.create`), posts to `/auth/passkey/*` endpoints with `X-CSRFToken` headers, and navigates via `window.location.href`.
- `[report-viz.js:DOMContentLoaded]` - Mutates `.section-body` DOM elements and `.agent-card` contents in-place after page load.

## Verification
- Run: `pytest`
