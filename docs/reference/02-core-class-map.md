# 02 · Core class map

Seven LLM-backed agents cover the analysis; three protocols cover persistence, mail, and models. Concrete classes are wired in `infra/config.py` — nothing else knows which column of the swap table is live.

## Agents

### Six specialists — `agents/specialists.py`

| Agent | Returns |
|---|---|
| `PriceAgent` | `PriceAnalysis` |
| `DividendAgent` | `DividendAnalysis` |
| `MovementAgent` | `MovementAnalysis` |
| `ValuationAgent` | `ValuationAnalysis` |
| `ControversyAgent` | `ControversyAnalysis` |
| `SentimentAgent` | `SentimentAnalysis` |

> Small tier by default · tools via MCP · prompts may only key on fields the payload carries — state the negative case.

### `ConsolidatorAgent` — `agents/consolidator.py`

- `run(state)` → `FinalReport`
- six sub-scores → weighted 0–100 score
- `score_band()` → five bands

> Large tier · structured output first, regex fallback · binary BUY/NOT BUY derived at threshold **60** for compat.

### `PortfolioAgent` — `agents/portfolio.py`

- `run(symbol, shares, avg_cost, …)` → analysis

> Elevated users only · chains after the base report (**My Position**) · assumes an existing holding, not a new position.

## Protocols & boundaries

### `AbstractReportRepository` — `infra/repository.py`

- `save` / `get_latest` / `get_by_id` → `ReportRecord`
- `save_user` / `get_user_by_email` → `UserRecord`
- `add/list_webauthn_credentials` → credentials
- `save_portfolio_report` → id

> SQLite (dev default) **or** Postgres (Docker) · schema created at startup, concurrency-safe · Adminer (`:5181`, LAN) for direct access.

### `EmailSender` — `infra/email.py`

- `send(to, subject, html)` → `None`
- `build_report_email(…)` → Tala-styled report mail
- `build_verification_email(…)` → 6-digit code mail

> Console (no key) **or** ZeptoMail · **no email failure of any kind may interrupt the flow** — logged, never raised past the seam.

### Web plumbing boundary — `rate_limit` · `dedup` · `progress`

- `rl_reserve` / `rl_release` → atomic, 5/day
- `SET NX` inflight claim → one run per ticker
- `publish_progress` → SSE → snapshot + stream

> All three live in Redis · a failed analysis releases its rate-limit slot so the user can retry.

## Swap table

| Protocol | Implementations |
|---|---|
| Repository | `SQLiteReportRepository` **or** `PostgresReportRepository` |
| EmailSender | `ConsoleEmailSender` **or** `ZeptoMailSender` |
| BaseChatModel | `ChatOpenAI` **or** `ChatAnthropic` — × large / medium / small |
| Data source | MCP server (required — deliberately no fallback) |

`DB_BACKEND` picks the repository; `ZEPTOMAIL_API_KEY` picks the sender; every agent picks its model from a `[provider:]tier` spec, so one agent can run on Anthropic while the rest run on OpenAI.

## Sign-in — passkey-first, verified email

1. **Login page** — email-first; decoy credentials make unknown emails indistinguishable.
2. **`register/send-code`** — consent + emailed 6-digit code: HMAC-hashed in session, 10 min TTL, 5 attempts, 60 s resend, newest 2 codes stay valid.
3. **`register/begin` · `complete`** — WebAuthn ceremony; the code is checked before the exists-check, so begin answers uniformly.
4. **Session user** — `user_id` *is* the email: reports, limits, and result mail all key on it.

Google / Microsoft OAuth stays as account recovery. Passkeys bind to `WEBAUTHN_RP_ID` — changing the domain invalidates every credential.

## Verdict scale — 0–100, five bands

| Range | Band | Note |
|---|---|---|
| 80–100 | **STRONG BUY** | meter deep in the green zone |
| 60–79 | **BUY** | ≥ 60 also sets the legacy binary BUY |
| 40–59 | **WAIT** | mixed signals — the band the binary verdict can't say |
| 20–39 | **DON'T BUY** | |
| 0–19 | **AVOID** | |

The score is the weighted mean of six per-dimension sub-scores (weights env-tunable, valuation heaviest at 0.25). The report page and result email both show the band, never the raw binary.

## Ship pipeline — tag-driven, path-gated

Merge to `main` → tests → **Bump Version & Tag** (app version == git tag == image tag) → multi-arch image to GHCR. The image rebuilds **only when its inputs change**; deps are locked via pip-compiled `requirements.txt` (CI resolves fresh from `pyproject` — the reason the `mcp<2` and `redis<6.5` ceilings exist).

Prod pulls `docker-compose.prod.yml` verbatim: the same app image serves web / worker / MCP, selected by entrypoint; the cloudflared tunnel is opt-in via the `tunnel` profile with its token in `.env`.
