"""Shared pytest fixtures: settings isolation so no test leaks its env into another."""

from collections.abc import Iterator

import pytest

from ctviz.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Clear the `get_settings()` lru_cache before and after every test (S0 deferral)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
