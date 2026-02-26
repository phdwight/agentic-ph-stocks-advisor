# Philippine Stock Market AI Advisor

An agentic AI application that analyses Philippine Stock Exchange (PSE) listed stocks and provides a **BUY** or **NOT BUY** verdict in plain English.

Built with **LangGraph 1.0** + **LangChain 1.2** using a multi-agent architecture.

## Architecture

Five specialist agents run **in parallel**, each responsible for a single analysis dimension. A **consolidator agent** synthesises their outputs into a final investor-friendly report.

```
START ──┬── Price Agent ────────────┐
        ├── Dividend Agent ─────────┤
        ├── Price Movement Agent ───┼── Consolidator ── Final Report
        ├── Valuation Agent ────────┤
        └── Controversy Agent ──────┘
```

| Agent | Responsibility |
|-------|---------------|
| **Price Agent** | Current price vs 52-week range |
| **Dividend Agent** | Yield, payout ratio, sustainability |
| **Movement Agent** | 1-year trend, volatility, monthly patterns |
| **Valuation Agent** | PE/PB ratios, Graham-number fair value estimate |
| **Controversy Agent** | Sudden price spikes, statistical anomalies, risk flags |
| **Consolidator** | Merges all analyses → plain-English report with verdict |

## SOLID Principles Applied

- **S**ingle Responsibility – each module handles one concern (models, tools, agents, prompts, config, graph)
- **O**pen/Closed – new agents can be added by registering a node; existing nodes need no changes
- **L**iskov Substitution – agents depend on `BaseChatModel`, any LLM provider works
- **I**nterface Segregation – tool functions return narrow, typed data slices instead of a monolithic blob
- **D**ependency Inversion – agents accept an abstract LLM interface, not a concrete class

## Setup

```bash
# 1. Create virtual environment
python -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Configure API key
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...
```

## Usage

```bash
python -m ph_stocks_advisor.main TEL    # PLDT
python -m ph_stocks_advisor.main SM     # SM Investments
python -m ph_stocks_advisor.main BDO    # BDO Unibank
python -m ph_stocks_advisor.main ALI    # Ayala Land
python -m ph_stocks_advisor.main JFC    # Jollibee
```

## Testing

```bash
pytest tests/ -v
```

All tests run offline with mocked yfinance data and mocked LLM calls — no API key required.

## Project Structure

```
ph_stocks_advisor/
├── __init__.py
├── config.py          # Settings & LLM factory
├── models.py          # Pydantic data models & graph state
├── prompts.py         # Prompt templates per agent
├── tools.py           # yfinance data-fetching functions
├── agents.py          # 5 specialist agent classes
├── consolidator.py    # Consolidator agent
├── graph.py           # LangGraph workflow definition
└── main.py            # CLI entry point

tests/
├── conftest.py        # Shared fixtures & mock helpers
├── test_models.py
├── test_tools.py
├── test_agents.py
├── test_consolidator.py
└── test_graph.py
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Your OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model to use |
| `OPENAI_TEMPERATURE` | `0.2` | LLM temperature |
