.PHONY: install test lint check run smoke demo-offline
install: ; uv sync
test:    ; uv run pytest --cov=ctviz --cov-report=term-missing
lint:    ; uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
check:   lint test
run:     ; uv run uvicorn ctviz.api.app:app --reload
smoke:   ; uv run pytest -m live tests/live/test_smoke.py -v
# S8 Task 8.1: no .env, no OpenAI/OpenRouter key, no network -- answers only the canned example
# questions in examples/canned_plans.json, served from tests/fixtures/ctgov/*.json.gz.
demo-offline: ; PLANNER_MODE=replay uv run uvicorn ctviz.api.app:app --port 8000
