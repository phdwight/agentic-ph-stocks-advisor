"""
Data layer for the Philippine Stocks Advisor.

Subpackages
-----------
clients/    External API clients (DragonFi, PSE EDGE, TradingView, Tavily).
services/   Domain services that orchestrate clients into typed domain models.
analysis/   Pure data-analysis modules (candlestick pattern detection).

Top-level modules
-----------------
models.py   Pydantic domain models and shared graph state.
tools.py    Re-export façade — keeps old ``from data.tools import …`` working.
"""
