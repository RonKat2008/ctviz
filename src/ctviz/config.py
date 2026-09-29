"""Runtime settings (from environment / .env) and named constants used across ctviz."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CTGOV_BASE_URL = "https://clinicaltrials.gov/api/v2"
PAGE_SIZE = 1000  # API hard cap per page
MAX_RECORDS = 20_000  # per-cohort fetch cap (D6)
HTTP_TIMEOUT_S = 20.0
HTTP_CONNECT_TIMEOUT_S = 5.0
HTTP_RETRIES = 3
LLM_TIMEOUT_S = 30.0
COHORT_FETCH_CONCURRENCY = 4
CACHE_TTL_S = 3600


class Settings(BaseSettings):
    """Environment-driven configuration. Keys are optional so the service can start without them."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    planner_mode: Literal["live", "replay"] = "live"
    planner_model: str = "gpt-5.4-mini"
    planner_reasoning_effort: Literal["low", "medium", "high"] = "low"
    judge_model: str = "google/gemini-2.5-flash-lite"
    app_url: str = "http://localhost:8000"
    app_name: str = "ctviz"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (cached so .env is read once)."""
    return Settings()
