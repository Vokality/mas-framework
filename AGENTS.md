# Guidance for Project Agents

## Project overview

mas-framework is a Python multi-agent runtime.

## Architecture

- Written in Python using `uv`
- Minimal dependencies

## Development

- Package manager: `uv` (prefix python commands with `uv run`)
- Testing framework: `pytest` (run with `uv run pytest`)
- Linter: `ruff` (run with `uv run ruff check .`)
- Formatter: `ruff` (run with `uv run ruff format .`)
- Type checks: `ty` (run with `uv run ty check`)
- Python version: >=3.13 (target runtime)
- Strict typing is required:
  - Add type annotations for all new/changed code (especially public APIs)
  - Avoid `Any` and broad unions when a precise type is feasible
  - Avoid `# type: ignore`; if unavoidable, justify it inline and scope it narrowly

## Engineering Rules

Coding style: All code must be clean, documented and minimal. That means:

- Organize code by functionality
- Organize related code/modules into packages
- Keep It Simple Stupid (KISS) by reducing the "Concept Count". That means, strive for fewer functions or methods, fewer helpers. If a helper is only called by a single callsite, then prefer to inline it into the caller.
- At the same time, Don't Repeat Yourself (DRY)
- There is a tension between KISS and DRY. If you find yourself in a situation where you're forced to make a helper method just to avoid repeating yourself, the best solution is to look for a way to avoid even having to do the complicated work at all.
- If some code looks heavyweight, perhaps with lots of conditionals, then think harder for a more elegant way of achieving it.
- Prefer docstrings for modules/classes/public functions. Use comments sparingly for non-obvious intent and invariants (avoid restating the code).
- Your work is incomplete until you've performed all the checks.
- Your decisions must be rooted in evidence and truth.
