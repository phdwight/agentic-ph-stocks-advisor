"""
PH Stocks Advisor — MCP server.

Exposes the data-fetching functions of the advisor as Model Context Protocol
(MCP) tools over Streamable HTTP, so they can be consumed by the advisor app
(or any MCP-compatible client) over the network instead of via in-process
Python imports.

Single Responsibility: this package only adapts existing domain services to
the MCP protocol — it contains no business logic of its own.
"""

from __future__ import annotations

__all__ = ["build_server"]

from ph_stocks_mcp.server import build_server
