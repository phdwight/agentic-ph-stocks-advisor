# infra

## Overview
Provides core cross-cutting infrastructure: environment-backed `Settings` and dependency factories (LLMs, Redis pools, DB repository singleton, email sender), transactional email delivery and templating via ZeptoMail or console logging, LLM auth/quota error classification, unified logging formatters, optional Langfuse tracing, PSE trading-session calendar calculations, and PostgreSQL/SQLite persistence repositories (`reports`, `user_symbols`, `users`, `webauthn_credentials`, `holdings`, `portfolio_reports`).

## Interface
- `[config.py:Settings]` - Environment-variable-backed configuration class for LLM providers/tiers/weights, Tavily, SQLite/Postgres DB, Redis, MCP server URL, API URLs, timezone, Entra/Google/WebAuthn auth, analysis thresholds, rate limits, and ZeptoMail.
- `[config.py:Settings.entra_authority]`, `[config.py:Settings.passkey_enabled]`, `[config.py:Settings.auth_enabled]`, `[config.py:Settings.google_enabled]`, `[config.py:Settings.entra_enabled]` - Boolean/URL configuration properties derived from `Settings`.
- `[config.py:get_settings]` - `@lru_cache(maxsize=1)` factory returning the singleton `Settings` instance.
- `[config.py:get_redis]`, `[config.py:get_redis_raw]` - Return `redis.Redis` clients backed by lazily initialized shared `ConnectionPool` instances (`decode_responses=True` and `decode_responses=False`).
- `[config.py:get_today]` - Returns today's `datetime.date` in the configured `Settings.timezone`.
- `[config.py:AGENT_LLM_SPECS]` - Dict mapping agent names (`consolidator`, `portfolio`, and 6 specialist agents) to `[provider:]tier` LLM specs.
- `[config.py:build_chat_model]` - Resolves a `[provider:]tier` spec into a `BaseChatModel` (`ChatOpenAI` or `ChatAnthropic`).
- `[config.py:build_structured_llm]` - Wraps a `BaseChatModel` with `.with_structured_output(schema)` (using `method='json_schema'` for `langchain_anthropic` models).
- `[config.py:get_agent_llm]`, `[config.py:get_llm]`, `[config.py:get_mini_llm]` - LLM factory functions for per-agent, primary (`consolidator`), and `small`-tier models.
- `[config.py:get_email_sender]` - Builds an `EmailSender` (`ZeptoMailSender` if `zeptomail_api_key` is set, else `ConsoleEmailSender`).
- `[config.py:get_repository]` - Lazily initializes and returns the singleton `AbstractReportRepository` (`PostgresReportRepository` or `SQLiteReportRepository`).
- `[email.py:ZEPTOMAIL_URL]`, `[email.py:EmailSendError]`, `[email.py:email_failure_is_quota]`, `[email.py:EmailSender]`, `[email.py:ConsoleEmailSender]`, `[email.py:ZeptoMailSender]`, `[email.py:build_email_sender]` - Transactional email delivery types and implementations.
- `[email.py:build_verification_email]`, `[email.py:build_login_code_email]`, `[email.py:build_report_email]` - HTML/subject email template builders for signup verification, OTP sign-in, and completed stock reports.
- `[llm_errors.py:AUTH_ERROR_MESSAGE]`, `[llm_errors.py:QUOTA_ERROR_MESSAGE]`, `[llm_errors.py:friendly_llm_error]` - Inspects LLM exception chains and maps quota/billing or authentication failures to user-facing messages.
- `[logging.py:LOG_FORMAT]`, `[logging.py:LOG_FORMAT_CELERY]`, `[logging.py:LOG_FORMAT_CELERY_TASK]`, `[logging.py:LOG_FORMAT_GUNICORN_ACCESS]`, `[logging.py:DATE_FORMAT]`, `[logging.py:configure_logging]` - Standardized logging format strings and root logger setup.
- `[repository.py:UserType]` - `IntEnum` defining user privilege levels: `NORMAL` (`0`) and `ELEVATED` (`1`).
- `[repository.py:UserRecord]`, `[repository.py:WebAuthnCredentialRecord]`, `[repository.py:HoldingRecord]`, `[repository.py:PortfolioReportRecord]`, `[repository.py:ReportRecord]` - Persistence data objects.
- `[repository.py:AbstractReportRepository]` - Abstract base class defining CRUD and query methods across reports, user symbols, users, WebAuthn credentials, holdings, and portfolio reports.
- `[repository_postgres.py:PostgresReportRepository]` - PostgreSQL implementation of `AbstractReportRepository` using `psycopg2.pool.ThreadedConnectionPool`.
- `[repository_sqlite.py:SQLiteReportRepository]` - SQLite implementation of `AbstractReportRepository` using thread-local `sqlite3.Connection` instances.
- `[tracing.py:build_langfuse_config]`, `[tracing.py:flush_langfuse]` - Langfuse callback configuration and span flushing helpers.
- `[trading_calendar.py:PHT]`, `[trading_calendar.py:MARKET_OPEN_HOUR]`, `[trading_calendar.py:MARKET_CLOSE_HOUR]`, `[trading_calendar.py:NON_TRADING_DATES]`, `[trading_calendar.py:is_trading_day]`, `[trading_calendar.py:is_market_open]`, `[trading_calendar.py:last_trading_close]`, `[trading_calendar.py:next_trading_close]`, `[trading_calendar.py:next_close_label]` - PSE trading-session calendar utilities (09:00–15:00 PHT, Mon–Fri).

## Invariants
- `[config.py:_env]` - Treats unset or empty/whitespace-only environment variables as absent, falling back through candidate keys to the final default.
- `[config.py:_resolve_spec]` - LLM provider must be in `('openai', 'anthropic')` and tier must be in `('large', 'medium', 'small')`; otherwise raises `ValueError`.
- `[config.py:build_chat_model]` - Raises `ValueError` if `provider == 'openai'` without `openai_api_key` or `provider == 'anthropic'` without `anthropic_api_key`; omits `temperature` for OpenAI unless `openai_send_temperature` is `True` and always omits `temperature` for Anthropic.
- `[config.py:build_structured_llm]` - Uses `method='json_schema'` for `langchain_anthropic` models and default structured output method for other providers.
- `[email.py:ZeptoMailSender.send]` - Raises `EmailSendError` on `requests.RequestException` or when HTTP response `status_code >= 400`.
- `[llm_errors.py:friendly_llm_error]` - Checks quota indicators (`insufficient_quota`, `billing`, `exceeded your current quota`) before auth indicators (`401`/`403`, `AuthenticationError`, `PermissionDeniedError`, key-invalid phrases) across the deduplicated `__cause__`/`__context__` chain.
- `[repository.py:UserType]` - `NORMAL = 0` is subject to daily analysis limits and cached-report deduplication; `ELEVATED = 1` is exempt from daily limits and can re-analyse stocks with cached reports.
- `[repository.py:WebAuthnCredentialRecord]` - Binary fields (`credential_id`, `public_key`) are stored as base64url-encoded strings across both SQLite and Postgres.
- `[repository.py:AbstractReportRepository.add_user_symbol]` - Idempotent per `(user_id, symbol)` pair (`ON CONFLICT (user_id, symbol) DO NOTHING` in Postgres; `INSERT OR IGNORE` in SQLite) and uppercases `symbol`.
- `[repository.py:AbstractReportRepository.save_user]` - Upserts on `oid` updating `name`, `email`, `provider`, and `last_login_at`, while preserving `user_type` and enforcing write-once consent (`disclaimer_version` and `disclaimer_accepted_at` use `COALESCE(users.<col>, EXCLUDED.<col>)` so subsequent sign-ins never overwrite initial consent).
- `[repository.py:AbstractReportRepository.delete_webauthn_credential]` - Deletion is scoped by both `credential_id` and `user_oid` (`WHERE credential_id = ... AND user_oid = ...`) so users can only delete their own passkeys.
- `[repository.py:AbstractReportRepository.save_holding]` - Idempotent upsert on `(user_id, symbol)` (`ON CONFLICT (user_id, symbol) DO UPDATE`) and normalizes `symbol` to uppercase across `save_holding`, `get_holding`, `delete_holding`, `save_portfolio_report`, `get_portfolio_report`, `get_latest_by_symbol`, and `list_by_symbol`.
- `[repository_postgres.py:_INIT_LOCK_KEY]` - `PostgresReportRepository.initialize` acquires transaction-scoped advisory lock `pg_advisory_xact_lock(0x70485341)` before executing DDL and migrations to prevent concurrent worker race conditions on `pg_class`.
- `[repository_postgres.py:PostgresReportRepository._conn]` - Validates borrowed pool connections via `SELECT 1` (`_is_conn_alive`) up to `max(self._max_conn, 1)` attempts, discards dead connections (`close=True`) on `OperationalError`/`InterfaceError`, and rolls back uncommitted transactions on other `psycopg2.Error` exceptions.
- `[repository_sqlite.py:SQLiteReportRepository._get_conn]` - Uses `threading.local()` to isolate connections per thread, and converts `:memory:` to `file::memory:?cache=shared` (`uri=True`) so multiple threads share the same in-memory database.
- `[tracing.py:_tracing_enabled]` - Tracing is enabled only when both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set and `LANGFUSE_TRACING_ENABLED` (lowercased/stripped) is not in `{'0', 'false', 'no', 'off', ''}`.
- `[trading_calendar.py:_now_pht]` - Raises `ValueError('now must be timezone-aware')` if a naive `datetime` (`tzinfo is None`) is passed.
- `[trading_calendar.py:last_trading_close]` & `[trading_calendar.py:next_trading_close]` - Always return tz-aware UTC `datetime` objects corresponding to 15:00 PHT on a trading day (`weekday() < 5` and not in `NON_TRADING_DATES`).

## Side Effects
- `[config.py:load_dotenv]` - Executes `dotenv.load_dotenv()` at module import time, mutating process environment variables from `.env`.
- `[config.py:get_redis]` & `[config.py:get_redis_raw]` - Lazily create Redis `ConnectionPool` instances in module globals `_redis_pool` and `_redis_pool_raw`.
- `[config.py:get_repository]` - Instantiates `PostgresReportRepository` or `SQLiteReportRepository`, calls `repo.initialize()` (initializing DB schema/connection pool), and caches it in module global `_repository`.
- `[email.py:ZeptoMailSender.send]` - Performs an outbound HTTPS POST request to `https://api.zeptomail.com/v1.1/email`.
- `[logging.py:configure_logging]` - Calls `logging.basicConfig(..., force=True)` on the root logger, clears handlers on `uvicorn`/`gunicorn` loggers, and mutates `uvicorn.config.LOGGING_CONFIG` if `uvicorn` is installed.
- `[repository_postgres.py:PostgresReportRepository.initialize]` & `[repository_sqlite.py:SQLiteReportRepository.initialize]` - Execute DDL and schema migrations creating/altering tables (`reports`, `user_symbols`, `users`, `webauthn_credentials`, `holdings`, `portfolio_reports`) and indexes (`idx_reports_symbol_created`, `idx_webauthn_user`, `idx_portfolio_reports_user_symbol`).
- `[repository_postgres.py:PostgresReportRepository.save]` & `[repository_sqlite.py:SQLiteReportRepository.save]` - Mutate `record.id` in-place with the database-generated `id` after inserting into `reports` (and `save_portfolio_report` into `portfolio_reports`).
- `[tracing.py:flush_langfuse]` - Calls `langfuse.get_client().flush()` to export buffered OpenTelemetry spans over the network.

## Verification
- Run: `pytest`
