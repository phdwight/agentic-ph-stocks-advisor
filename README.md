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

The project ships with a multi-stage `Dockerfile` and a Compose v2 file that runs the advisor alongside a dedicated PostgreSQL container.

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env — set at least OPENAI_API_KEY

# 2. Build & analyse a stock (one-shot)
docker compose run --rm advisor TEL

# 3. Analyse multiple stocks with PDF export
docker compose run --rm advisor SM BDO TEL --pdf

# 4. Stop the database when you're done
docker compose down            # keeps data in the pgdata volume
docker compose down -v         # removes the volume too
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

Reports are automatically persisted to a local SQLite database (`reports.db` by default) after each analysis.

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
ph_stocks_advisor/
├── __init__.py
├── main.py                    # CLI entry point (ph-advisor)
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
