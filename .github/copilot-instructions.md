# Copilot Instructions

These instructions are automatically loaded by GitHub Copilot on every interaction.

## README Maintenance

**Every time you modify the codebase, check whether `README.md` needs updating and apply changes if so.**

Specifically, update the README when any of the following change:

### Architecture & Agents
- An agent is added, removed, or renamed → update the **Architecture** diagram, agent table, and project structure
- Agent responsibilities change (new data sources, new analysis dimensions) → update the agent table descriptions

### Data Sources
- A new data source is added or an existing one is removed → update the **Data Sources** table
- API key requirements change → update the table's "API Key" column

### CLI & Usage
- CLI arguments, flags, or entry points change (`pyproject.toml` `[project.scripts]`) → update the **Usage** section
- New commands or subcommands are introduced → add examples

### Environment Variables
- An environment variable is added, removed, or its default changes → update the **Environment Variables** table
- Mark new required variables clearly

### Project Structure
- Files or directories are added, removed, or renamed → update the **Project Structure** tree
- Include a brief comment for each file describing its purpose

### Dependencies & Setup
- Dependencies change in `pyproject.toml` → update **Setup** instructions if install commands are affected
- New optional dependency groups → document the install command

### Testing
- Testing approach changes (new frameworks, new fixtures) → update the **Testing** section

## SOLID Principles

**Strictly follow SOLID principles when adding or modifying code.**

- **Single Responsibility** – every module, class, and function should have exactly one reason to change. Do not combine unrelated concerns in the same file or class.
- **Open/Closed** – extend behaviour by adding new modules or classes (e.g. a new agent node, a new repository backend) rather than modifying existing ones.
- **Liskov Substitution** – any subclass or implementation must be a drop-in replacement for its base. Agents must work with any `BaseChatModel`; repositories must honour the `AbstractReportRepository` contract.
- **Interface Segregation** – keep interfaces narrow and focused. Data-fetching functions should return small, typed models — not large multi-purpose dicts.
- **Dependency Inversion** – depend on abstractions, not concrete classes. Inject the LLM, repository, and settings rather than importing concrete implementations directly.

When creating new code, verify each of these principles before finishing.

## Testing Requirements

**Always add or update tests when modifying the codebase.**

- Write tests that verify **functionality and behaviour**, not individual function signatures. Test what the code *does*, not how it's structured internally.
- Prefer integration-style tests that exercise a complete feature path (e.g. "given this stock data, the agent produces a valid analysis") over unit tests that merely assert a single function was called.
- Mock external dependencies (LLM, APIs, databases) but keep the logic under test as real as possible.
- Each new agent, data source, or feature must have corresponding tests before the work is considered complete.
- Use descriptive test names that read as behaviour specifications (e.g. `test_dividend_agent_flags_unsustainable_payout`).

## General Rules

- Keep the README concise — prefer tables and code blocks over prose
- Use the existing section order; do not rearrange sections without reason
- Do not add version-pinned numbers to the README header (e.g. avoid "LangGraph 1.0"); use plain names instead
- When in doubt, update the README — a slightly over-updated README is better than a stale one
