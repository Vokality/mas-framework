# Claude AI Instructions

**Note**: This project uses [bd (beads)](https://github.com/steveyegge/beads) for issue tracking. Use `bd` commands instead of markdown TODOs. See AGENTS.md for workflow details.


## Development

- Package manager: `uv`
- Testing framework: `basedpyright` (run with `uvx basedpyright src`)
- Linter: `ruff` (run with `uv run ruff check .`)
- Formatter: `ruff` (run with `uv run ruff format .`)
- Type checks: `pyright` (run with `uv run pyright`)
- Python version: 3.14
- Strict typing is required

## Engineering Rules

- Organize code by functionality
- Organize related code/modules into packages
- Keep things DRY