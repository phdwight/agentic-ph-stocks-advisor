"""Entry point: ``python -m ph_stocks_mcp`` runs the streamable-HTTP server."""

from __future__ import annotations

import os

from ph_stocks_advisor.infra.logging import configure_logging
from ph_stocks_mcp.server import build_server


def main() -> None:
    configure_logging()
    host = os.getenv("MCP_HOST", "0.0.0.0")  # noqa: S104  bind-all is intended inside containers
    port = int(os.getenv("MCP_PORT", "8000"))
    server = build_server()
    server.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
