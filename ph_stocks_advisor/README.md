# ph_stocks_advisor

## Overview
Top-level package for the Agentic PH Stocks Advisor application, providing the CLI entry point (`main.py`) that orchestrates multi-agent Philippine Stock Exchange (PSE) stock analysis (`graph`), database persistence (`infra`), and report export (`export`), alongside subpackages for specialist agents (`agents`), domain models and MCP tools (`data`), and the Flask/Celery web interface (`web`).

## Interface
- `[main.py:main]` - `main(symbol: str | None = None) -> None`: CLI (`ph-advisor`) and programmatic entry point that configures logging, parses CLI arguments (`symbols`, `-o/--output`, and dynamic `--<fmt>` flags from `FORMATTER_REGISTRY`), executes multi-agent stock analysis per symbol via `_analyse_single`, persists reports, prints summaries via `_print_report`, and exports formatted files.
- Subdirectories:
  - [agents](./agents/README.md) - Specialist analysis agents (`PriceAgent`, `DividendAgent`, `MovementAgent`, `ValuationAgent`, `ControversyAgent`, `SentimentAgent`), `ConsolidatorAgent`, `PortfolioAgent`, and prompt templates.
  - [data](./data/README.md) - Domain Pydantic models (`FinalReport`, `Verdict`, etc.), synchronous MCP client (`get_client`, `_SyncMCPClient`), and market-data tool wrappers (`validate_symbol`, `fetch_*`).
  - [export](./export/README.md) - Pluggable report formatters (`FORMATTER_REGISTRY`, `get_formatter`, `PdfFormatter`, `HtmlFormatter`).
  - [graph](./graph/README.md) - LangGraph multi-agent workflow (`build_graph`, `run_analysis`, `AGENT_REGISTRY`, `GraphState`).
  - [infra](./infra/README.md) - Settings, LLM/DB/Redis factories (`get_settings`, `get_repository`, `configure_logging`), repositories (`PostgresReportRepository`, `SQLiteReportRepository`), email, tracing, and PSE trading calendar (`last_trading_close`).
  - [web](./web/README.md) - Flask web application (`create_app`), Celery app (`celery_app`), background tasks (`analyse_stock`, `portfolio_analyse_stock`), authentication, passkey, SSE progress, and rate limiting.

## Invariants
- `[main.py:_analyse_single]` - Stock symbols are normalized via `symbol.upper().replace('.PS', '')` before invoking `run_analysis(symbol)`.
- `[main.py:_analyse_single]` - If `result.get('final_report')` is returned as a `dict`, it is coerced into a `FinalReport` instance via `FinalReport(**report)`.
- `[main.py:_analyse_single]` - Database persistence errors are caught and logged to `stdout` without aborting report printing or file export, and `repo.close()` is guaranteed via a `finally` block.
- `[main.py:main]` - When analysing multiple symbols (`len(symbols) > 1`), any user-supplied `-o`/`--output` path is ignored (`output_path = None`) so each exported file auto-names to `<SYMBOL>_report<ext>`.
- `[main.py:main]` - Exits with code `130` (`sys.exit(130)`) on `KeyboardInterrupt` inside `_analyse_single`, and exits with code `1` (`sys.exit(1)`) if one or more symbols fail analysis.

## Side Effects
- `[main.py:main]` - Calls `configure_logging()` to initialize application logging and reads `sys.argv` via `argparse` (or directly checks `sys.argv` for `--<fmt>` flags when `symbol` is passed programmatically).
- `[main.py:_analyse_single]` - Invokes `run_analysis(symbol)` which executes external LLM calls and market data fetches via the LangGraph workflow.
- `[main.py:_analyse_single]` - Opens a database repository via `get_repository()`, saves a `ReportRecord` (`repo.save(record)`), and closes the connection (`repo.close()`).
- `[main.py:_analyse_single]` - Creates parent directories (`out.parent.mkdir(parents=True, exist_ok=True)`) and writes formatted report files (`formatter.write(rec, out)`) to the filesystem when export flags are specified.
- `[main.py:_print_report]` - Writes formatted analysis reports, status messages, and errors to `stdout`, and terminates the process via `sys.exit` on failure or interrupt.

## Verification
- Run: `pytest`
