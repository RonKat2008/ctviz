.PHONY: install test lint check run smoke
install: ; uv sync
test:    ; uv run pytest --cov=ctviz --cov-report=term-missing
lint:    ; uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
check:   lint test
run:     ; uv run uvicorn ctviz.api.app:app --reload
smoke:   ; uv run pytest -m live tests/live/test_smoke.py -v
