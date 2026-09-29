import pytest

from ctviz.config import MAX_RECORDS, PAGE_SIZE, Settings

SETTINGS_ENV_VARS = ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "PLANNER_MODE", "PLANNER_MODEL")


def test_settings_defaults_do_not_require_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.openai_api_key is None
    assert settings.planner_model == "gpt-5.4-mini"
    assert settings.planner_mode == "live"


def test_settings_never_expose_keys_in_repr() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-secret-value")

    assert "sk-secret-value" not in repr(settings)


def test_fetch_limits_match_the_api() -> None:
    assert PAGE_SIZE == 1000
    assert MAX_RECORDS == 20_000
