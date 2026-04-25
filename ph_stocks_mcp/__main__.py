"""Entry point: ``python -m ph_stocks_mcp`` runs the streamable-HTTP server."""

from __future__ import annotations

import logging

from ph_stocks_mcp.server import build_server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = build_server()
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
