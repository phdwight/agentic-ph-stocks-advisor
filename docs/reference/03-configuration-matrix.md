# 03 · Configuration matrix

Everything is environment-sourced into one `Settings` object in `infra/config.py`. Two variables change the shape of the system: `DB_BACKEND` picks the storage column, and `WEBAUTHN_RP_ID` turns the whole auth stack on. Empty-but-forwarded Compose vars are treated as unset.

## LLM · provider-agnostic, per-agent

| Variable | Default | What it governs |
|---|---|---|
| `LLM_PROVIDER` | `openai` | Default provider; each agent may override via its spec |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | unset | Fail-fast when the chosen provider's key is missing |
| `*_MODEL_LARGE / MEDIUM / SMALL` | gpt-4o · gpt-4o-mini / opus · sonnet · haiku | Three tiers per provider |
| `LLM_<AGENT>` | consolidator+portfolio large, specialists small | Per-agent `[provider:]tier` spec |

## Data plane · protect the sources

| Variable | Default | What it governs |
|---|---|---|
| `MCP_SERVER_URL` | **required** | All fetching dispatches here — a missing value is a hard error, not a fallback |
| `TAVILY_API_KEY` | unset | Web search for sentiment / controversy context |
| `DRAGONFI / PSE_EDGE / TRADINGVIEW _BASE_URL` | public endpoints | Upstream roots (DragonFi outage ≠ not-listed — Edge fallbacks) |

## Storage · the one switch

| Variable | Default | What it governs |
|---|---|---|
| `DB_BACKEND` | `sqlite` | sqlite → single file; postgres → pooled `POSTGRES_DSN` (always postgres in Docker) |
| `REDIS_URL` | `redis://localhost:6379/0` | Queue, sessions, SSE, rate limit, dedup claims — one pool, bounded |

## Auth · off unless the RP ID is set

| Variable | Default | What it governs |
|---|---|---|
| `WEBAUTHN_RP_ID` / `WEBAUTHN_ORIGIN` | unset / `localhost:5180` | Passkeys bind to this domain; origin verified server-side, never inferred |
| `GOOGLE_*` / `ENTRA_*` | unset | OAuth recovery sign-ins |
| `FLASK_SECRET_KEY` | change-me | Sessions, CSRF, verification-code HMAC, decoy credential HMAC |

## Email · console unless the key is set

| Variable | Default | What it governs |
|---|---|---|
| `ZEPTOMAIL_API_KEY` | unset | Unset → log-only console sender; set → ZeptoMail. Sign-up codes need it in prod |
| `EMAIL_FROM_ADDRESS` | customer.success@sakayandgo.com | Must belong to a ZeptoMail-verified **exact** domain |
| `APP_BASE_URL` | → `WEBAUTHN_ORIGIN` | Absolute report links inside result emails |

## Verdict & limits

| Variable | Default | What it governs |
|---|---|---|
| `SCORE_WEIGHT_*` | valuation .25 · others .15 | Sub-score weights, normalised by their sum |
| `BUY_SCORE_THRESHOLD` | `60` | Where the legacy binary verdict flips to BUY |
| `DAILY_ANALYSIS_LIMIT` | `5` | Fresh analyses per user per trading day; cached and joined runs are free |
| `TIMEZONE` | `Asia/Manila` | The ★ rule's clock — trading days, 15:00 cutoff |

The full variable list (thresholds, ports, tuning) is in the repository [README](https://github.com/phdwight/agentic-ph-stocks-advisor#07--configuration).
