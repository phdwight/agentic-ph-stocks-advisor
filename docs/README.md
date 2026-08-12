# PH Stocks Advisor — Agent Documentation

How a Philippine Stock Exchange ticker becomes a verdict: six specialist agents, one consolidator, and every seam in between — the web boundary, the queue, the LangGraph workflow, the MCP data plane, and one analysis traced end to end.

The app is live at [phstockadvisor.sakayandgo.com](https://phstockadvisor.sakayandgo.com) and published to `ghcr.io/phdwight/agentic-ph-stocks-advisor`. Versioning is tag-driven — the running app, the git tag, and the image tag always match (check `GET /version`).

## How these docs are organised

**Reference card** — the condensed architecture reference, one page per concern:

1. [Architecture & layering](reference/01-architecture-and-layering.md) — the five processes, the one direction of dependency, and the trading-hours freshness invariant everything protects.
2. [Core class map](reference/02-core-class-map.md) — the seven LLM-backed agents, the three swappable protocols, sign-in, the verdict scale, and the ship pipeline.
3. [Configuration matrix](reference/03-configuration-matrix.md) — the environment variables that change the shape of the system.
4. [Golden path](reference/04-golden-path.md) — one request traced through every layer.

**Deep dives** — the long-form documents:

- [High-level design](high-level-design.md) — full system design, component responsibilities, security model.
- [Agent developer onboarding](agent-developer-onboarding.md) — how to work on the codebase: layout, conventions, test culture.

The repository [README](https://github.com/phdwight/agentic-ph-stocks-advisor#readme) covers setup, usage, deployment, and CI/CD.
