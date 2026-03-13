# Philippine Stock Market AI Advisor

An agentic AI application that analyses Philippine Stock Exchange (PSE) listed stocks and provides a **BUY** or **NOT BUY** verdict in plain English.

Built with **LangGraph** + **LangChain** using a multi-agent architecture. Requires Python ≥ 3.11.

## Architecture

A **validation node** checks the stock symbol, then six specialist agents run **in parallel**, each responsible for a single analysis dimension. A **consolidator agent** synthesises their outputs into a final investor-friendly report.

```
                        ┌── Price Agent ────────────┐
                        ├── Dividend Agent ─────────┤
                        ├── Movement Agent ─────────┤
START → Validate ──────►├── Valuation Agent ────────┼──► Consolidator ──► END
                        ├── Controversy Agent ──────┤
                        └── Sentiment Agent ────────┘
```

| Agent | Responsibility |
|-------|---------------|
| **Price Agent** | Current price vs 52-week range, price catalysts |
| **Dividend Agent** | Yield, payout ratio, sustainability, REIT rules (RA 9856), income/revenue/FCF trends, structured dividend announcements (ex-date, rate, payment date) |
| **Movement Agent** | 1-year trend, max drawdown, candlestick patterns, TradingView multi-period performance; LLM-driven web search via tool calling |
| **Valuation Agent** | PE/PB/PEG ratios, Graham Number fair value estimate |
| **Controversy Agent** | Price spike detection, risk factors; LLM-driven web search for news & controversies via tool calling |
| **Sentiment Agent** | Global events impact (wars, pandemics, economic shifts, climate); LLM-driven web search for geopolitical & macro risks via tool calling |
| **Consolidator** | Merges all analyses → prose summary with BUY / NOT BUY verdict (via structured output; regex fallback) |
| **Portfolio Agent** | Personalised hold / accumulate / trim advisory for elevated users based on their stock holdings (on-demand, not part of the main graph) |

### Data Sources

The data layer cascades through multiple sources for resilience:

| Source | API Key | Usage |
|--------|---------|-------|
| **DragonFi** (`api.dragonfi.ph`) | Not required | Primary — price, dividends, valuation, financials, news, symbol validation |
| **PSE EDGE** (`edge.pse.com.ph`) | Not required | Primary for daily OHLCV history, spike detection, declared dividend disclosures (SEC Form 6-1), and company dividend announcements page (ex-date, rate, payment date) |
| **TradingView Scanner** | Not required | Multi-period performance & volatility |
| **Tavily** | Optional | Web search for dividend news, general news, and controversies — invoked by the LLM via tool calling (not called automatically) |

## SOLID Principles Applied

- **S**ingle Responsibility – each domain service (`price_service`, `dividend_service`, etc.) handles one data concern; `tools.py` is a thin re-export façade
- **O**pen/Closed – new agents are added via `AGENT_REGISTRY` in `workflow.py`; new export formats are added by subclassing `OutputFormatter` and registering in `FORMATTER_REGISTRY`; existing code needs no changes
- **L**iskov Substitution – `get_llm()` returns `BaseChatModel`; any LangChain-compatible LLM provider works. `PdfFormatter` and `HtmlFormatter` are drop-in replacements for `OutputFormatter`
- **I**nterface Segregation – tool functions return narrow, typed Pydantic models; `OutputFormatter` exposes only `render()`, `write()`, and metadata properties
- **D**ependency Inversion – LLM is injected into `build_graph(llm=...)` and closed over in nodes; repository layer uses an ABC with SQLite/Postgres implementations; export uses `OutputFormatter` ABC

## Setup

### Local (without Docker)

```bash
# 1. Create virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Configure environment
cp .env.example .env
# Edit .env — at minimum set OPENAI_API_KEY
```

For PostgreSQL support:

```bash
pip install -e ".[postgres]"
```

### Docker (recommended for deployment)

The project ships with a multi-stage `Dockerfile` and a Compose v2 file with four services:

| Container | Role |
|-----------|------|
| **db** | PostgreSQL 16 — persistent report storage |
| **redis** | Redis 7 — Celery message broker & result backend |
| **web** | Flask web UI via Gunicorn + gevent (port 5000) |
| **worker** | Celery worker — runs stock analyses in the background |
| **advisor** | One-shot CLI analysis (optional) |

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env — set at least OPENAI_API_KEY

# 2. Start the web UI + worker + database + redis
docker compose up --build -d web worker

# 3. Open the web UI
open http://localhost:5000

# 4. Or run a one-shot CLI analysis
docker compose run --rm advisor TEL
docker compose run --rm advisor SM BDO TEL --pdf

# 5. Stop everything
docker compose down            # keeps data in the pgdata volume
docker compose down -v         # removes volumes too
```

Exported files (PDF / HTML) are written to the `./output` directory on the host via a bind mount.

> **Tip:** Override any env var in `.env` — the Compose file reads them automatically.

## Usage

### Analyse a stock

```bash
ph-advisor TEL          # PLDT
ph-advisor SM           # SM Investments
ph-advisor BDO          # BDO Unibank
ph-advisor ALI          # Ayala Land
ph-advisor JFC          # Jollibee
```

### Analyse multiple stocks at once

```bash
ph-advisor SM BDO TEL               # analyse three stocks sequentially
ph-advisor SM BDO --pdf              # each stock gets its own PDF
ph-advisor SM BDO TEL --html --pdf   # PDF + HTML for every stock
```

Or via module:

```bash
python -m ph_stocks_advisor.main TEL
```

### Generate a PDF report

```bash
ph-advisor SM --pdf                   # PDF saved alongside terminal output
ph-advisor SM --pdf -o report.pdf     # custom output path
```

### Generate an HTML report

```bash
ph-advisor SM --html                  # HTML saved alongside terminal output
ph-advisor SM --html -o report.html   # custom output path
```

### Export a saved report to PDF

```bash
ph-advisor-pdf MREIT                  # latest report for the symbol
ph-advisor-pdf MREIT --id 3           # specific report by ID
ph-advisor-pdf MREIT -o mreit.pdf     # custom output path
```

### Export a saved report to HTML

```bash
ph-advisor-html MREIT                 # latest report for the symbol
ph-advisor-html MREIT --id 3          # specific report by ID
ph-advisor-html MREIT -o mreit.html   # custom output path
```

### Web UI

```bash
ph-advisor-web                        # start with Gunicorn on http://127.0.0.1:5000
ph-advisor-web --port 8080            # custom port
ph-advisor-web --host 0.0.0.0         # expose to network
ph-advisor-web --debug                # use Flask dev server with auto-reload
```

The web interface lets you enter a stock symbol, kicks off the analysis in the background, and streams real-time progress to the browser via **Server-Sent Events (SSE)**. Each workflow step (validation, data fetching, agent execution, consolidation, saving) publishes events through Redis Pub/Sub; the frontend receives them instantly via `/stream/<task_id>`. A polling fallback (`/status/<task_id>`) is available for browsers without SSE support. Once complete, the report is displayed in the browser.

### Health Check

A `/healthz` heartbeat endpoint verifies that both Redis and the database are reachable:

```bash
curl http://localhost:5000/healthz
# {"checks":{"database":"ok","redis":"ok"},"status":"healthy"}
```

Returns **200** when all dependencies are healthy, **503** otherwise. This endpoint does not require authentication and is used by Docker healthchecks and Azure Container Apps liveness/readiness probes to detect and restart unhealthy replicas.

Reports are automatically persisted to a local SQLite database (`reports.db` by default) after each analysis. Reports are **shared** — they are not tied to any user — but each authenticated user only sees the stocks they have personally requested to analyse. A `user_symbols` table tracks which symbols each user has analysed; anonymous users (auth disabled) see all reports. Authenticated user profiles (name, email, provider, login timestamps) are saved to a `users` table on every sign-in (upserted by `oid`).

### User Types

Every user is assigned a **user type** that controls access privileges:

| Type | Value | Rate Limit | Cache Bypass | Per-Stock Cooldown |
|------|-------|------------|--------------|-------------------|
| **Normal** | `0` | 5 analyses/day (configurable via `DAILY_ANALYSIS_LIMIT`) | No — fresh cached reports are served | N/A (cache rules apply) |
| **Elevated** | `1` | Unlimited | Yes — can re-analyse stocks | 1 per UTC day per stock — re-analysis available after 00:00 UTC |

All new users start as **Normal**. An administrator can promote a user to **Elevated** via the SQLAdmin panel (Admin → Users → edit `user_type`). The user type is stored in the `users` table and read from the database on each login — it cannot be changed by the user themselves. Login upserts intentionally do **not** overwrite the `user_type` column.

### Portfolio Holdings (Elevated Users Only)

Elevated users can record their stock positions and receive **personalised portfolio advice**.

1. **My Position** button appears on any report page for elevated users
2. A modal asks for **number of shares** and **average cost per share**
3. Holdings are saved per user per symbol and persist across sessions
4. Clicking **Save & Analyse** triggers a dedicated **Portfolio Agent** that considers:
   - The user's cost basis and unrealised P/L
   - The latest stock analysis report
   - Current market price
5. The agent produces a **Hold / Accumulate / Trim** recommendation with key price levels and risk considerations
6. The portfolio advisory is **private** — only visible to the elevated user who created it

| API Endpoint | Method | Description |
|-------------|--------|-------------|
| `/api/holdings/<symbol>` | `GET` | Retrieve user's holding for a symbol |
| `/api/holdings/<symbol>` | `POST` | Save/update shares & average cost |
| `/api/holdings/<symbol>` | `DELETE` | Remove a holding |
| `/api/portfolio-analyse/<symbol>` | `POST` | Trigger portfolio analysis (async Celery task) |
| `/api/portfolio-report/<symbol>` | `GET` | Retrieve latest portfolio advisory |

### Authentication (Microsoft Entra ID + Google OAuth2)

The web UI supports **Microsoft Entra ID** and **Google** login with **passkey (FIDO2)** support. When at least one provider is configured, users must sign in before accessing any page. Both providers can be enabled simultaneously.

#### Microsoft Entra ID

1. **Register an app** in the [Azure portal](https://portal.azure.com) → Microsoft Entra ID → App registrations:
   - **Redirect URI** → `http://localhost:5000/auth/callback` (Web platform)
   - Note the **Application (client) ID** and create a **Client secret**
2. **Enable FIDO2 passkeys** in your Entra ID tenant (Security → Authentication methods → FIDO2 security key)
3. Set the environment variables:

```bash
ENTRA_CLIENT_ID=<your-client-id>
ENTRA_CLIENT_SECRET=<your-client-secret>
ENTRA_TENANT_ID=<your-tenant-id>   # or "common" for multi-tenant
FLASK_SECRET_KEY=<random-secret>
```

#### Google OAuth2

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (Web application type):
   - **Authorized redirect URI** → `http://localhost:5000/auth/google/callback`
   - For Azure deployment also add: `https://<your-app>.azurecontainerapps.io/auth/google/callback`
3. Set the environment variables:

```bash
GOOGLE_CLIENT_ID=<your-google-client-id>
GOOGLE_CLIENT_SECRET=<your-google-client-secret>
```

When **neither** `ENTRA_CLIENT_ID` nor `GOOGLE_CLIENT_ID` is set, authentication is disabled and all routes are publicly accessible (useful for local development).

### Azure (Cloud Deployment)

Deploy to **Azure Container Apps** with managed PostgreSQL and Redis. All infrastructure is defined as code via Bicep.

| Azure Service | Role |
|---------------|------|
| **Container Registry** | Stores the Docker image |
| **Container Apps — web** | Flask web UI (HTTPS, auto-scaling) |
| **Container Apps — worker** | Celery worker (scales with queue depth) |
| **Container Apps — admin** | SQLAdmin database panel (HTTPS) |
| **Database for PostgreSQL** | Flexible Server (Burstable B1ms, 32 GB) |
| **Cache for Redis** | Basic C0 (TLS only) |
| **Log Analytics** | Container logs & monitoring |

#### Prerequisites

- [Azure CLI](https://aka.ms/install-az-cli) installed and logged in (`az login`)
- Docker running locally
- `OPENAI_API_KEY` in `.env` (or exported)

#### First-time deploy

```bash
# Set a password for the managed PostgreSQL server
export AZURE_PG_PASSWORD='<strong-password>'

# Deploy everything (infra + build + push + update apps)
./infra/azure/deploy.sh
```

The script will:
1. Create a resource group (`ph-stocks-advisor-rg` in `southeastasia`)
2. Provision all Azure resources via Bicep
3. Build the web and admin Docker images and push them to ACR
4. Update all Container Apps (web, worker, admin) with the new images
5. Print the public HTTPS URLs for the web UI and admin panel

#### Update after code changes

```bash
./infra/azure/deploy.sh --update   # rebuild image & redeploy (skips infra)
```

#### Provision infrastructure only (no image push)

```bash
./infra/azure/deploy.sh --infra-only
```

#### Tear down

```bash
./infra/azure/teardown.sh          # interactive confirmation
./infra/azure/teardown.sh --yes    # skip confirmation
```

#### Customisation

Override defaults via environment variables before running `deploy.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `AZURE_RESOURCE_GROUP` | `ph-stocks-advisor-rg` | Resource group name |
| `AZURE_LOCATION` | `southeastasia` | Azure region |
| `AZURE_APP_NAME` | `phstocks` | Name prefix for all resources |
| `AZURE_PG_ADMIN_USER` | `phadmin` | PostgreSQL admin login |
| `AZURE_PG_PASSWORD` | _(required)_ | PostgreSQL admin password |
| `IMAGE_TAG` | `latest` | Docker image tag |

## Testing

```bash
# Run all unit tests (fast, fully mocked, no API keys needed)
pytest tests/ -v -m "not integration"

# Run integration tests (requires OPENAI_API_KEY)
pytest tests/ -v -m "integration"

# Run everything
pytest tests/ -v
```

### Test Structure

| Category | Marker | What it tests | LLM calls |
|----------|--------|---------------|-----------|
| **Unit** | _(default)_ | Deterministic logic, mocked LLMs, data models | Fully mocked |
| **Trajectory** | _(default)_ | Agent step sequences (which nodes ran, prompt content, call order) | Fully mocked |
| **Integration** | `@pytest.mark.integration` | End-to-end with real LLM calls | Real API calls |

Unit and trajectory tests run in CI on every push. Integration tests run only when secrets are available (push to `develop`, not fork PRs).

### Trajectory Testing

Instead of asserting on the exact LLM output string, trajectory tests verify the **steps** the agent took:

- Which data-fetching functions were called and with what arguments
- Whether the LLM was invoked with the correct context (symbol, data)
- That the graph executed all expected nodes in the right order
- That invalid inputs short-circuit the pipeline correctly

Use `make_trajectory_tracker()` from `conftest.py` to instrument any agent.

All tests run offline with mocked data sources and mocked LLM calls — no API key required.

## Project Structure

```
Dockerfile                         # Multi-stage container image
docker-compose.yml                 # Compose v2 — local dev (builds from source)
docker-compose.prod.yml            # Compose v2 — production (pulls pre-built GHCR images)
.dockerignore                      # Files excluded from Docker build context
.github/
└── workflows/
    ├── develop-ci.yml             # CI — lint, type-check, test (develop branch)
    └── main-ci-cd.yml             # CI/CD — same checks + deploy to Azure (main branch)
admin/                             # SQLAdmin database panel
├── app.py                     #   Flask + SQLAdmin wiring & model views
├── Dockerfile                 #   Container image for admin panel
└── requirements.txt           #   Python dependencies
infra/
└── azure/                     # Azure deployment (IaC)
    ├── main.bicep             #   Bicep template (all resources)
    ├── main.bicepparam        #   Default parameter values
    ├── deploy.sh              #   One-command deploy script
    └── teardown.sh            #   Destroy all Azure resources
ph_stocks_advisor/
├── __init__.py
├── main.py                    # CLI entry point (ph-advisor)
├── web/                       # Flask web application + Celery worker
│   ├── __init__.py
│   ├── app.py                 #   Flask factory, routes, CLI (ph-advisor-web)
│   ├── auth.py                #   Entra ID + Google OAuth2 authentication blueprint
│   ├── rate_limit.py          #   Per-user daily analysis rate limiting (Redis)
│   ├── celery_app.py          #   Celery instance & configuration
│   ├── tasks.py               #   Celery task definitions (analyse_stock)
│   ├── progress.py            #   Redis Pub/Sub progress publisher + subscriber (SSE)
│   ├── templates/             #   Jinja2 HTML templates
│   │   ├── base.html          #     Shared layout
│   │   ├── index.html         #     Landing page with analysis form
│   │   ├── login.html         #     Sign-in page (Microsoft + Google buttons)
│   │   ├── report.html        #     Single report view
│   │   ├── history.html       #     Report history table
│   │   └── no_report.html     #     404 / no report found
│   └── static/                #   Static assets
│       ├── style.css          #     Main stylesheet (dark glassmorphism theme)
│       ├── app.js             #     Client-side SSE streaming (polling fallback)
│       ├── portfolio.js       #     Holdings modal & portfolio analysis (elevated users)
│       └── report-viz.js      #     Report data visualization enhancements
├── export/                    # Pluggable output formatters (Open/Closed)
│   ├── __init__.py            #   FORMATTER_REGISTRY & get_formatter()
│   ├── formatter.py           #   OutputFormatter ABC, parse_sections(), export_cli()
│   ├── pdf.py                 #   PdfFormatter  (fpdf2, ph-advisor-pdf)
│   └── html.py                #   HtmlFormatter (pure-Python, ph-advisor-html)
├── agents/
│   ├── __init__.py
│   ├── specialists.py         # 6 specialist agent classes (4 with LLM tool calling for web search)
│   ├── consolidator.py        # Consolidator agent
│   ├── portfolio.py           # Portfolio agent (personalised hold/accumulate/trim)
│   ├── prompts.py             # Prompt templates per agent
│   └── web_search_tools.py    # LangChain @tool wrappers for Tavily web search
├── data/
│   ├── __init__.py
│   ├── models.py              # Pydantic data models & graph state
│   ├── tools.py               # Re-export façade (backward compat)
│   ├── clients/               # External API clients
│   │   ├── dragonfi.py        #   DragonFi API (price, dividends, valuation, news)
│   │   ├── pse_edge.py        #   PSE EDGE daily OHLCV history
│   │   ├── pse_edge_dividends.py  # PSE EDGE declared dividend scraper (SEC Form 6-1)
│   │   ├── pse_edge_company_dividends.py  # PSE EDGE company dividends page scraper (ex-date, rate, payment)
│   │   ├── tradingview.py     #   TradingView scanner (performance & volatility)
│   │   └── tavily_search.py   #   Tavily web search integration
│   ├── services/              # Domain services (orchestrate clients → models)
│   │   ├── price.py           #   Current price & catalyst detection
│   │   ├── dividend.py        #   Dividend data & sustainability analysis
│   │   ├── movement.py        #   1-year movement, candlestick, TV perf
│   │   ├── valuation.py       #   Fair-value estimation (Graham Number)
│   │   ├── controversy.py     #   Price anomalies & risk news
│   │   └── sentiment.py       #   Global events & macro-risk sentiment
│   └── analysis/              # Pure data analysis (no I/O)
│       └── candlestick.py     #   Candlestick pattern detection
├── graph/
│   ├── __init__.py
│   └── workflow.py            # LangGraph workflow & agent registry
└── infra/
    ├── __init__.py
    ├── config.py              # Settings, LLM / repository factory & Redis pool
    ├── repository.py          # Abstract repository interface
    ├── repository_sqlite.py   # SQLite implementation (default)
    └── repository_postgres.py # PostgreSQL implementation

tests/
├── conftest.py                # Shared fixtures, mock helpers & trajectory tracker
├── test_tools.py
├── test_agents.py
├── test_auth.py               # Entra ID auth blueprint tests
├── test_company_dividends.py  # DividendAnnouncement model & company page scraper tests
├── test_consolidator.py
├── test_export.py             # OutputFormatter, PDF, HTML, CLI tests
├── test_graph.py
├── test_trajectory.py          # Trajectory tests (agent step sequences & graph order)
├── test_dedup.py               # Concurrent analysis deduplication tests
├── test_healthz.py             # Heartbeat endpoint tests
├── test_rate_limit.py          # Per-user daily rate limiting tests
├── test_portfolio.py           # Portfolio holdings & advisory feature tests
├── test_repository.py
├── test_sse.py                # SSE progress streaming tests
└── test_user_type.py          # User type system (elevated bypass) tests
```

### CI/CD

Two GitHub Actions workflows follow a **develop → main** promotion strategy:

| Workflow | Branch | Jobs | Deploys? |
|----------|--------|------|----------|
| `develop-ci.yml` | `develop` | Lint, type-check, unit tests, integration tests | No |
| `main-ci-cd.yml` | `main` | Same CI checks + build & push GHCR images + deploy | Yes (push only) |

Both use `uv` for fast dependency installation and share the same quality gates:

1. **Ruff** — lint (style + security rules) & format check
2. **Pyright** — type checking in basic mode
3. **Unit tests** — all `pytest` tests except `@pytest.mark.integration` (no secrets needed)
4. **Integration tests** — real LLM calls (only when `OPENAI_API_KEY` secret is available)

#### Deployment Targets

The `main` workflow supports **two deployment targets** that run in parallel. Each is auto-detected based on which secrets are configured — enable one, both, or neither:

On every push to `main`, a **build-images** job builds Docker images and pushes them to **GitHub Container Registry** (`ghcr.io`). The deploy jobs run after images are published:

| Target | Triggered when | How it works |
|--------|---------------|--------------|
| **Azure Container Apps** | `AZURE_CREDENTIALS` secret is set | Runs `deploy.sh --update`, tags images with commit SHA |
| **Docker Compose via SSH** | `DEPLOY_SSH_KEY` secret is set | SSHs into the server, pulls pre-built GHCR images via `docker-compose.prod.yml`, restarts services, runs health check |

#### GitHub Secrets

| Secret | Used by | Required? | Purpose |
|--------|---------|-----------|---------|
| `OPENAI_API_KEY` | CI (both workflows) | Yes | LLM calls in integration tests |
| `LANGCHAIN_API_KEY` | CI (both workflows) | No | LangSmith tracing in integration tests |
| **Azure deployment** | | | |
| `AZURE_CREDENTIALS` | `deploy-azure` job | For Azure | Service principal JSON (`az ad sp create-for-rbac --json-auth`) |
| `TAVILY_API_KEY` | `deploy-azure` job | No | Web search (passed to Azure env vars) |
| **SSH deployment** | | | |
| `DEPLOY_HOST` | `deploy-ssh` job | For SSH | Production server hostname or IP |
| `DEPLOY_USER` | `deploy-ssh` job | For SSH | SSH username |
| `DEPLOY_SSH_KEY` | `deploy-ssh` job | For SSH | SSH private key (e.g. `id_ed25519`) |
| `DEPLOY_PORT` | `deploy-ssh` job | No | SSH port (default: `22`) |

#### GitHub Variables (optional)

| Variable | Default | Purpose |
|----------|---------|---------|
| `AZURE_RESOURCE_GROUP` | `ph-stocks-advisor-rg` | Azure resource group name |
| `AZURE_APP_NAME` | `phstocks` | Azure Container Apps name prefix |
| `DEPLOY_PATH` | `~/ph-stocks-advisor` | Project directory on the SSH server |

#### SSH Server Setup (Pre-built Images)

The SSH deploy job uses **pre-built images from GHCR** — the server does **not** need the source code or Git.

On the target machine, do a one-time setup:

```bash
mkdir -p ~/ph-stocks-advisor && cd ~/ph-stocks-advisor

# 1. Copy docker-compose.prod.yml from the repo (or download it)
curl -fsSL https://raw.githubusercontent.com/<owner>/agentic-ph-stocks-advisor/main/docker-compose.prod.yml \
  -o docker-compose.prod.yml

# 2. Create .env
cat > .env <<'EOF'
OPENAI_API_KEY=sk-...
POSTGRES_PASSWORD=<strong-password>
ADMIN_PASSWORD=<strong-password>
APP_IMAGE=ghcr.io/<owner>/agentic-ph-stocks-advisor:latest
ADMIN_IMAGE=ghcr.io/<owner>/agentic-ph-stocks-advisor-admin:latest
EOF

# 3. Log in to GHCR (only needed for private repos)
echo $GITHUB_PAT | docker login ghcr.io -u <username> --password-stdin

# 4. First launch
docker compose -f docker-compose.prod.yml up -d
```

After this, every push to `main` will automatically pull new images and restart the services.

> **Deploying to another device?** Copy `docker-compose.prod.yml` + `.env` to any machine with Docker, set `APP_IMAGE` and `ADMIN_IMAGE`, and run `docker compose -f docker-compose.prod.yml up -d`.

## Environment Variables

All settings live in `.env` (see [.env.example](.env.example)). Only `OPENAI_API_KEY` is required.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | **Yes** | — | OpenAI API key |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | Primary (heavy) LLM model used by the consolidator agent |
| `OPENAI_MINI_MODEL` | No | `gpt-4o-mini` | Lighter LLM model used by the five specialist agents |
| `OPENAI_TEMPERATURE` | No | `0.2` | LLM temperature |
| `TAVILY_API_KEY` | No | — | Tavily web search key (graceful degradation when absent) |
| `TAVILY_MAX_RESULTS` | No | `5` | Max results per Tavily search call |
| `TAVILY_SEARCH_DEPTH` | No | `basic` | Tavily search depth (`basic` or `advanced`) |
| `DB_BACKEND` | No | `sqlite` | `sqlite` or `postgres` |
| `SQLITE_PATH` | No | `reports.db` | Path to the SQLite database file |
| `POSTGRES_DSN` | No | `postgresql://localhost:5432/ph_advisor` | PostgreSQL connection string |
| `POSTGRES_USER` | No | `ph_advisor` | Postgres user (Docker Compose only) |
| `POSTGRES_PASSWORD` | No | `ph_advisor` | Postgres password (Docker Compose only) |
| `POSTGRES_DB` | No | `ph_advisor` | Postgres database name (Docker Compose only) |
| `POSTGRES_PORT` | No | `5432` | Host port for Postgres (Docker Compose only) |
| `DRAGONFI_BASE_URL` | No | `https://api.dragonfi.ph/api/v2` | DragonFi API base URL |
| `PSE_EDGE_BASE_URL` | No | `https://edge.pse.com.ph` | PSE EDGE base URL |
| `TRADINGVIEW_SCANNER_URL` | No | `https://scanner.tradingview.com/philippines/scan` | TradingView scanner endpoint |
| `HTTP_TIMEOUT` | No | `15` | HTTP request timeout (seconds) |
| `TIMEZONE` | No | `Asia/Manila` | IANA timezone or UTC/GMT offset (e.g. `Asia/Manila`, `UTC+8`, `GMT-5`) |
| `OUTPUT_DIR` | No | _(empty — cwd)_ | Base directory for exported PDF/HTML files |
| `TREND_UP_THRESHOLD` | No | `5` | % change above which trend = uptrend |
| `TREND_DOWN_THRESHOLD` | No | `-5` | % change below which trend = downtrend |
| `SPIKE_STD_MULTIPLIER` | No | `3` | × daily-return std-dev to flag a spike |
| `SPIKE_MIN_ABS_RETURN` | No | `0.05` | Minimum |return| to count as a spike |
| `HIGH_VOLATILITY_THRESHOLD` | No | `0.03` | Daily std above this = "high volatility" |
| `OVERVALUATION_MULTIPLIER` | No | `1.3` | price / avg > this = overvaluation risk |
| `DISTRESS_MULTIPLIER` | No | `0.7` | price / avg < this = distress risk |
| `CATALYST_YIELD_THRESHOLD` | No | `3.0` | Dividend yield (%) to trigger catalyst |
| `CATALYST_RANGE_PCT` | No | `65` | % of 52-week range for catalyst detection |
| `CATALYST_DAY_CHANGE_PCT` | No | `0.5` | Daily % change to trigger momentum catalyst |
| `CATALYST_NEAR_HIGH_PCT` | No | `5` | % gap to 52-week high for "near high" catalyst |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL (broker + result backend for Celery) |
| `REDIS_PORT` | No | `6379` | Host port for Redis (Docker Compose only) |
| `WEB_PORT` | No | `5000` | Host port for the Flask web UI (Docker Compose only) |
| `ADMIN_PORT` | No | `8085` | Host port for the SQLAdmin panel (Docker Compose only) |
| `ADMIN_SECRET_KEY` | No | `sqladmin-dev-…` | Flask secret key for the admin panel |
| `ENTRA_CLIENT_ID` | No | — | Microsoft Entra ID application (client) ID (enables login) |
| `ENTRA_CLIENT_SECRET` | No | — | Entra ID client secret |
| `ENTRA_TENANT_ID` | No | `common` | Entra ID tenant ID (or `common` for multi-tenant) |
| `ENTRA_REDIRECT_PATH` | No | `/auth/callback` | OAuth2 redirect path (Microsoft) |
| `GOOGLE_CLIENT_ID` | No | — | Google OAuth2 client ID (enables Google login) |
| `GOOGLE_CLIENT_SECRET` | No | — | Google OAuth2 client secret |
| `GOOGLE_REDIRECT_PATH` | No | `/auth/google/callback` | OAuth2 redirect path (Google) |
| `FLASK_SECRET_KEY` | No | _(dev placeholder)_ | Flask session encryption key |
| `DAILY_ANALYSIS_LIMIT` | No | `5` | Max successful first-time analyses per user per UTC day (failed queries are not counted; resets at 00:00 UTC) |
| `WEB_WORKERS` | No | `4` | Gunicorn worker processes |
| `WEB_WORKER_CLASS` | No | `gevent` | Gunicorn worker class (`gevent`, `gthread`, `sync`, etc.) |
| `WEB_WORKER_CONNECTIONS` | No | `1000` | Max simultaneous connections per gevent worker |
| `WEB_THREADS` | No | `2` | Threads per Gunicorn worker (only used with `gthread` worker class) |
| `WEB_TIMEOUT` | No | `120` | Gunicorn worker timeout in seconds |
| `PG_POOL_MIN` | No | `2` | Minimum PostgreSQL connections in pool |
| `PG_POOL_MAX` | No | `5` | Maximum PostgreSQL connections in pool |
| `REDIS_MAX_CONNECTIONS` | No | `10` | Maximum connections in the shared Redis pool |
| `CELERY_CONCURRENCY` | No | `4` | Celery worker concurrency (prefork processes) |
| `APP_IMAGE` | No | `ghcr.io/OWNER/agentic-ph-stocks-advisor:latest` | App Docker image for `docker-compose.prod.yml` |
| `ADMIN_IMAGE` | No | `ghcr.io/OWNER/agentic-ph-stocks-advisor-admin:latest` | Admin Docker image for `docker-compose.prod.yml` |

## Scaling Tiers

The Bicep template defaults to minimal resources. Override via deploy parameters to scale up — no code changes needed.

| Parameter | Hobby (~100/mo) | Small (~1K users) | Scale (100K+) |
|-----------|----------------:|-------------------:|--------------:|
| `pgSkuName` / `pgSkuTier` | B1ms / Burstable | B1ms / Burstable | D2ds_v5 / GeneralPurpose |
| `webCpu` / `webMemory` | 0.25 / 0.5Gi | 0.5 / 1Gi | 2 / 4Gi |
| `workerCpu` / `workerMemory` | 0.25 / 0.5Gi | 0.5 / 1Gi | 1 / 2Gi |
| `redisCpu` / `redisMemory` | 0.25 / 0.5Gi | 0.25 / 0.5Gi | 0.5 / 1Gi |
| `redisMaxMemory` | 64mb | 128mb | 512mb |
| `webWorkers` | 1 | 2 | 4 |
| `pgPoolMax` | 5 | 10 | 20 |
| `redisMaxConnections` | 10 | 20 | 50 |
| `celeryConcurrency` | 2 | 4 | 4 |
| `webMaxReplicas` | 1 | 3 | 10 |
| `workerMaxReplicas` | 1 | 3 | 10 |
| **Est. monthly cost** | **~$100** | **~$176** | **~$340** |

Deploy defaults are the **Hobby** tier. To scale up:

```bash
# Small tier (~1K users)
az deployment group create ... --parameters main.bicepparam \
  --parameters webCpu='0.5' webMemory='1Gi' workerCpu='0.5' workerMemory='1Gi' \
    redisMaxMemory='128mb' webWorkers='2' pgPoolMax='10' redisMaxConnections='20' \
    celeryConcurrency='4' webMaxReplicas=3 workerMaxReplicas=3

# Scale tier (100K+ users)
az deployment group create ... --parameters main.bicepparam \
  --parameters pgSkuName='Standard_D2ds_v5' pgSkuTier='GeneralPurpose' \
    webCpu='2' webMemory='4Gi' workerCpu='1' workerMemory='2Gi' \
    redisCpu='0.5' redisMemory='1Gi' redisMaxMemory='512mb' \
    webWorkers='4' pgPoolMax='20' redisMaxConnections='50' \
    celeryConcurrency='4' webMaxReplicas=10 workerMaxReplicas=10
```
