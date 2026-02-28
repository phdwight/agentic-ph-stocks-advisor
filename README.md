# Philippine Stock Market AI Advisor

An agentic AI application that analyses Philippine Stock Exchange (PSE) listed stocks and provides a **BUY** or **NOT BUY** verdict in plain English.

Built with **LangGraph** + **LangChain** using a multi-agent architecture. Requires Python ≥ 3.11.

## Architecture

A **validation node** checks the stock symbol, then five specialist agents run **in parallel**, each responsible for a single analysis dimension. A **consolidator agent** synthesises their outputs into a final investor-friendly report.

```
                        ┌── Price Agent ────────────┐
                        ├── Dividend Agent ─────────┤
START → Validate ──────►├── Movement Agent ─────────┼──► Consolidator ──► END
                        ├── Valuation Agent ────────┤
                        └── Controversy Agent ──────┘
```

| Agent | Responsibility |
|-------|---------------|
| **Price Agent** | Current price vs 52-week range, price catalysts |
| **Dividend Agent** | Yield, payout ratio, sustainability, REIT rules (RA 9856), income/revenue/FCF trends |
| **Movement Agent** | 1-year trend, max drawdown, candlestick patterns, TradingView multi-period performance, web news |
| **Valuation Agent** | PE/PB/PEG ratios, Graham Number fair value estimate |
| **Controversy Agent** | Price spike detection, risk factors, web news & controversies |
| **Consolidator** | Merges all analyses → prose summary with BUY / NOT BUY verdict |

### Data Sources

The data layer cascades through multiple sources for resilience:

| Source | API Key | Usage |
|--------|---------|-------|
| **DragonFi** (`api.dragonfi.ph`) | Not required | Primary — price, dividends, valuation, financials, news, symbol validation |
| **PSE EDGE** (`edge.pse.com.ph`) | Not required | Primary for daily OHLCV history, spike detection, and declared dividend disclosures (SEC Form 6-1) |
| **TradingView Scanner** | Not required | Multi-period performance & volatility |
| **Tavily** | Optional | Web search for dividend news, general news, and controversies |

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
| **web** | Flask web UI (port 5000) |
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
ph-advisor-web                        # start on http://127.0.0.1:5000
ph-advisor-web --port 8080            # custom port
ph-advisor-web --host 0.0.0.0         # expose to network
ph-advisor-web --debug                # enable Flask debug mode
```

The web interface lets you enter a stock symbol, kicks off the analysis in the background, and displays the report in the browser once complete. You can also browse report history for any symbol.

Reports are automatically persisted to a local SQLite database (`reports.db` by default) after each analysis.

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
3. Build the Docker image and push it to ACR
4. Update both Container Apps with the new image
5. Print the public HTTPS URL

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
pytest tests/ -v
```

All tests run offline with mocked data sources and mocked LLM calls — no API key required.

## Project Structure

```
Dockerfile                         # Multi-stage container image
docker-compose.yml                 # Compose v2 (app + Postgres)
.dockerignore                      # Files excluded from Docker build context
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
│   ├── celery_app.py          #   Celery instance & configuration
│   ├── tasks.py               #   Celery task definitions (analyse_stock)
│   ├── templates/             #   Jinja2 HTML templates
│   │   ├── base.html          #     Shared layout
│   │   ├── index.html         #     Landing page with analysis form
│   │   ├── login.html         #     Sign-in page (Microsoft + Google buttons)
│   │   ├── report.html        #     Single report view
│   │   ├── history.html       #     Report history table
│   │   └── no_report.html     #     404 / no report found
│   └── static/                #   Static assets
│       ├── style.css          #     Main stylesheet
│       └── app.js             #     Client-side Celery task polling
├── export/                    # Pluggable output formatters (Open/Closed)
│   ├── __init__.py            #   FORMATTER_REGISTRY & get_formatter()
│   ├── formatter.py           #   OutputFormatter ABC, parse_sections(), export_cli()
│   ├── pdf.py                 #   PdfFormatter  (fpdf2, ph-advisor-pdf)
│   └── html.py                #   HtmlFormatter (pure-Python, ph-advisor-html)
├── agents/
│   ├── __init__.py
│   ├── specialists.py         # 5 specialist agent classes
│   ├── consolidator.py        # Consolidator agent
│   └── prompts.py             # Prompt templates per agent
├── data/
│   ├── __init__.py
│   ├── models.py              # Pydantic data models & graph state
│   ├── tools.py               # Re-export façade (backward compat)
│   ├── clients/               # External API clients
│   │   ├── dragonfi.py        #   DragonFi API (price, dividends, valuation, news)
│   │   ├── pse_edge.py        #   PSE EDGE daily OHLCV history
│   │   ├── pse_edge_dividends.py  # PSE EDGE declared dividend scraper (SEC Form 6-1)
│   │   ├── tradingview.py     #   TradingView scanner (performance & volatility)
│   │   └── tavily_search.py   #   Tavily web search integration
│   ├── services/              # Domain services (orchestrate clients → models)
│   │   ├── price.py           #   Current price & catalyst detection
│   │   ├── dividend.py        #   Dividend data & sustainability analysis
│   │   ├── movement.py        #   1-year movement, candlestick, TV perf
│   │   ├── valuation.py       #   Fair-value estimation (Graham Number)
│   │   └── controversy.py     #   Price anomalies & risk news
│   └── analysis/              # Pure data analysis (no I/O)
│       └── candlestick.py     #   Candlestick pattern detection
├── graph/
│   ├── __init__.py
│   └── workflow.py            # LangGraph workflow & agent registry
└── infra/
    ├── __init__.py
    ├── config.py              # Settings & LLM / repository factory
    ├── repository.py          # Abstract repository interface
    ├── repository_sqlite.py   # SQLite implementation (default)
    └── repository_postgres.py # PostgreSQL implementation

tests/
├── conftest.py                # Shared fixtures & mock helpers
├── test_models.py
├── test_tools.py
├── test_agents.py
├── test_auth.py               # Entra ID auth blueprint tests
├── test_consolidator.py
├── test_export.py             # OutputFormatter, PDF, HTML, CLI tests
├── test_graph.py
└── test_repository.py
```

## Environment Variables

All settings live in `.env` (see [.env.example](.env.example)). Only `OPENAI_API_KEY` is required.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | **Yes** | — | OpenAI API key |
| `OPENAI_MODEL` | No | `gpt-4o-mini` | LLM model name |
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
| `ENTRA_CLIENT_ID` | No | — | Microsoft Entra ID application (client) ID (enables login) |
| `ENTRA_CLIENT_SECRET` | No | — | Entra ID client secret |
| `ENTRA_TENANT_ID` | No | `common` | Entra ID tenant ID (or `common` for multi-tenant) |
| `ENTRA_REDIRECT_PATH` | No | `/auth/callback` | OAuth2 redirect path (Microsoft) |
| `GOOGLE_CLIENT_ID` | No | — | Google OAuth2 client ID (enables Google login) |
| `GOOGLE_CLIENT_SECRET` | No | — | Google OAuth2 client secret |
| `GOOGLE_REDIRECT_PATH` | No | `/auth/google/callback` | OAuth2 redirect path (Google) |
| `FLASK_SECRET_KEY` | No | _(dev placeholder)_ | Flask session encryption key |
