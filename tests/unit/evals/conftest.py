"""Eval-harness tests run fully offline: settings never read the real `.env`."""

import pytest

from ctviz.api import app as app_module
from ctviz.config import Settings


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build `Settings` from the process env only, with no provider keys (no network, no .env)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("PLANNER_MODE", raising=False)
    monkeypatch.setattr(app_module, "get_settings", lambda: Settings(_env_file=None))
