"""API test fixtures: dependency-override cleanup, and settings isolated from the real `.env`."""

from collections.abc import Iterator
from typing import Any

import pytest

from ctviz.api import app as app_module
from ctviz.config import Settings


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every API test builds `Settings` from the process env only, never the real `.env` file.

    Local development keeps a real `OPENAI_API_KEY` in `.env`; without this fixture, the app's
    lifespan would build a real `OpenAIPlannerBackend` from it on every API test. Deleting the two
    provider env vars *and* forcing `_env_file=None` means an API test never depends on -- or can
    accidentally read -- that file, per this project's no-network, no-`.env` test rule.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(app_module, "get_settings", lambda: Settings(_env_file=None))


@pytest.fixture
def dependency_overrides() -> Iterator[dict[Any, Any]]:
    """`app.dependency_overrides`, guaranteed to be cleared after the test even if it raises."""
    try:
        yield app_module.app.dependency_overrides
    finally:
        app_module.app.dependency_overrides.clear()
