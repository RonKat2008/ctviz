# ctviz dev log

## 2026-09-28 — S0: scaffold

- Scaffolded the `ctviz` package with `uv` (Python 3.12, `src/` layout) plus ruff, mypy, pytest
  (async auto mode, `live` marker off by default) and a `Makefile` (`test`, `lint`, `check`,
  `run`, `smoke`).
- Added `config.py` (named constants + `Settings` with `SecretStr` keys) and `errors.py`
  (the exception hierarchy the API layer maps to error codes).
- Removed the `uv init` template `main()` / console script: it only printed a greeting and the
  project has no CLI entry point yet.
- **Decision: no agent framework.** The agent is a fixed two-call pipeline (planner, then judge
  with at most one revise), so plain OpenAI SDK + Pydantic is enough. It keeps every step
  unit-testable with fake backends, and the control flow reads top to bottom in one module.
