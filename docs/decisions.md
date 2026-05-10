# Architectural Decisions & Project Conventions

A human-curated log of non-obvious decisions, the reasoning behind them, and
conventions that aren't self-evident from the code. Indexed by the long-term
memory DB (`source_type = doc`) so any future Copilot session — regardless of
model — can recover the *why* behind a choice with a single query.

## How to use this file

- **Read it** at the start of work in an unfamiliar area — query the memory:
  `python -m ph_stocks_advisor.memory --db .copilot-memory.db query "why did we choose X" --type doc`
- **Append to it** whenever you make a non-trivial choice or discover a non-
  obvious constraint. Keep entries short (5–15 lines). Newest at the top of
  each section.
- **Do not** record things that are already obvious from the code or the README.
  This file is for things a future reader would otherwise have to reverse-
  engineer or ask about.

### Entry template

```
### YYYY-MM-DD — Short title
**Status:** accepted | superseded by <date> | deprecated
**Context:** What problem or constraint forced a decision.
**Decision:** What we chose.
**Rationale:** Why this option over the obvious alternatives.
**Consequences:** What this commits us to / makes harder later.
```

---

## Architecture decisions

### 2026-05-11 — Pre-push hook keeps the memory DB in sync
**Status:** accepted
**Context:** The "run `update` at end of task" rule relied on discipline; we
were one forgotten command away from pushing commits whose changes weren't yet
indexed, leaving the next session with stale context.
**Decision:** Tracked `scripts/pre-push` runs `ph_stocks_advisor.memory update`
before every `git push`. `scripts/install-git-hooks.sh` symlinks it into
`.git/hooks/`. Skipped silently when no venv or `OPENAI_API_KEY` is available;
bypassable with `SKIP_MEMORY_UPDATE=1 git push`. Never blocks a push on
failure.
**Rationale:** Removes the human step without forcing it on environments that
can't run it (CI, fresh clones, offline machines).
**Consequences:** New contributors must run `./scripts/install-git-hooks.sh`
once after cloning — flagged in the README. The hook adds ~1s per push when
there are no changes, more if many files were touched (one embedding call per
changed file).

### 2026-05-11 — Long-term memory uses SQLite + sqlite-vec, not a hosted vector DB
**Status:** accepted
**Context:** We needed cross-session project memory for Copilot that survives
model switches and can be queried from CLI / CI / any IDE.
**Decision:** Persist embeddings in a single git-ignored `.copilot-memory.db`
file using `sqlite-vec`, with OpenAI `text-embedding-3-small` (1536-dim).
**Rationale:** Zero infrastructure, no auth, file-based portability, and the
embedding cost is negligible (full project ≈ a fraction of a cent). A hosted
DB (Pinecone / Qdrant) would add ops burden without solving anything we have.
**Consequences:** Single-machine by default; if we ever want shared team
memory we'll need to either commit a serialised export or run a small server.

### 2026-05-11 — Treat the memory DB as a router, not a reader
**Status:** accepted
**Context:** Early instructions told Copilot to "always query before any task,"
which wasted tokens on trivial edits and sometimes returned truncated chunks
that misled the model.
**Decision:** Query only before *broad* exploration; treat hits as pointers and
always `read_file` the real source for editing context. Update once per task,
not per file. Codified in `.github/copilot-instructions.md`.
**Rationale:** Chunks are 1.4k chars and may cut mid-function — unsafe for
edits. Querying when the target file is already known is pure overhead.
**Consequences:** The DB earns its keep on orientation tasks ("where is X?")
and is intentionally skipped on focused edits.

### 2026-05-11 — Release tag is the source of truth for `pyproject.toml` version
**Status:** accepted
**Context:** Manual version bumps drifted from release tags.
**Decision:** A `sync-version.yml` workflow rewrites `pyproject.toml` on every
published GitHub Release and force-moves the tag to the sync commit so tag and
tree always agree.
**Rationale:** One source of truth (the release), zero human steps, and
existing CI is unaffected (`[skip ci]` on the bot commit).
**Consequences:** Releases must be created via the GitHub UI / API, not by
hand-editing `pyproject.toml`. The bot needs push access to `main` (PAT or
GitHub App in the branch-protection bypass list).

### Earlier — MCP server is the only data path for agents
**Status:** accepted
**Context:** Agents originally called domain services in-process, which made
remote/distributed deployments awkward and coupled agents to repo internals.
**Decision:** All data-fetching tools dispatch through the PH Stocks Advisor
MCP server. There is no in-process fallback — a missing `MCP_SERVER_URL` is a
hard configuration error.
**Rationale:** Single integration surface for agents; lets us swap the backing
implementation (mock, real, remote) without touching agent code (DIP).
**Consequences:** Local development requires the MCP server running. Tests mock
the MCP client, not the underlying services.

### Earlier — Consolidator uses structured output with regex fallback
**Status:** accepted
**Context:** Earlier consolidator versions parsed verdicts from free-form prose
with regex, which was brittle (false positives on words like "buyback").
**Decision:** Prefer `BaseChatModel.with_structured_output(ConsolidationResponse)`
to get a typed `Verdict`. Fall back to regex extraction only when the LLM
provider doesn't support structured output.
**Rationale:** Eliminates parsing bugs on capable models; preserves
compatibility on older ones.
**Consequences:** Adding a new verdict type means updating the Pydantic model,
the prompt, *and* the regex fallback.

---

## Conventions worth knowing

### 2026-05-11 — Tests assert behaviour, not function signatures
Per `.github/copilot-instructions.md`: prefer integration-style tests that
exercise a complete feature path with mocked external dependencies (LLM,
network) but real internal logic. Test names read as behaviour specs
(`test_dividend_agent_flags_unsustainable_payout`).

### 2026-05-11 — New agents are registered, not wired
Add specialist agents via `AGENT_REGISTRY` in `ph_stocks_advisor/graph/workflow.py`.
Add export formats by subclassing `OutputFormatter` and registering in
`FORMATTER_REGISTRY`. Existing code should not need to change (Open/Closed).

### 2026-05-11 — Pin dependencies with lower bounds, not upper
`pyproject.toml` uses `>=` only. Lock files (uv) handle reproducibility for
CI/deploy; library-style lower bounds keep us free to upgrade locally without
constant churn.

---

## Lessons learned (things that bit us)

### 2026-05-11 — Branch protection bypass requires the right identity
The default `GITHUB_TOKEN` (acting as `github-actions[bot]`) **cannot** be
added to a classic branch-protection bypass list. For `main`-bypass workflows
either (a) use a PAT or GitHub App on the bypass list, or (b) move to
Rulesets, where "GitHub Actions" is itself a valid bypass actor.

<!-- Add new entries above this line. Keep the file under ~500 lines; archive
older sections to docs/decisions-archive.md when it grows past that. -->
