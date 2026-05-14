"""Entry point: ``python -m ph_stocks_mcp`` runs the streamable-HTTP server."""

from __future__ import annotations

from ph_stocks_advisor.infra.logging import configure_logging
from ph_stocks_mcp.server import build_server


def main() -> None:
    configure_logging()
    server = build_server()
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
