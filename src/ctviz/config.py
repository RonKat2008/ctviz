"""Runtime settings (from environment / .env) and named constants used across ctviz."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

CTGOV_BASE_URL = "https://clinicaltrials.gov/api/v2"
PAGE_SIZE = 1000  # API hard cap per page
MAX_RECORDS = 20_000  # per-cohort fetch cap (D6)
# Q1: strict match for conditions only if measured rate >= 97% on the S2 fixtures (see DEVLOG).
# Measured 2026-09-28: glioblastoma 94.97%, ms_recruiting 87.91%, psoriasis_p2 99.22%.
CONDITIONS_STRICT = False
HTTP_TIMEOUT_S = 20.0
HTTP_CONNECT_TIMEOUT_S = 5.0
HTTP_RETRIES = 3
LLM_TIMEOUT_S = 30.0
# Fix B -- the judge's time budget (§4.5). Each OpenRouter call times out at JUDGE_TIMEOUT_S;
# ALL judge time in one /visualize request (both reviews, the one retry on a schema-invalid
# output or a 429, and any Retry-After wait) shares JUDGE_REQUEST_BUDGET_S, and no call (first
# or retry) starts with less than JUDGE_MIN_CALL_S left -- it fails open instead. Worst case:
# judge total <= 12 s, inside §4.5's judge (3 s) + revise-loop (+10 s) = 13 s envelope, which
# is what the judge gates (was: 2 reviews x (20 s + 2 s wait + 20 s) = 84 s). Typical: one
# 0.5-1.5 s call. The per-request total stays ~30 s + the judge's share (§4.5 worst case).
JUDGE_TIMEOUT_S = 8.0
JUDGE_REQUEST_BUDGET_S = 12.0
JUDGE_MIN_CALL_S = 2.0
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
