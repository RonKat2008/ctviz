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
# ALL judge time in one /visualize request -- both reviews, every §9.5 tier (tier 1, then the
# tier-2 fallback after a schema-invalid output / 503 / transport error), the one 429 retry per
# tier and any Retry-After wait -- shares JUDGE_REQUEST_BUDGET_S: each review gets one deadline
# (clock + what the earlier review left) and no call or tier starts with less than
# JUDGE_MIN_CALL_S left -- it fails open instead. Worst case: judge total <= 12 s however many
# tiers run, inside §4.5's judge (3 s) + revise-loop (+10 s) = 13 s envelope, which is what the
# judge gates (was: 2 reviews x (20 s + 2 s wait + 20 s) = 84 s). Typical: one 0.5-1.5 s tier-1
# call; a tier-2 answer adds one more (~1-3 s). The request total stays ~30 s + this share.
JUDGE_TIMEOUT_S = 8.0
JUDGE_REQUEST_BUDGET_S = 12.0
JUDGE_MIN_CALL_S = 2.0
COHORT_FETCH_CONCURRENCY = 4
CACHE_TTL_S = 3600
CACHE_MAX_ENTRIES = 128  # D1: bounded LRU -- evicts the least-recently-used entry past this
CACHE_MAX_BYTES = 256 * 1024 * 1024  # §10.2: total raw-body bytes held; LRU-evicts past this
VERSION_CACHE_TTL_S = 600  # /version is fetched once per process per this TTL (§10.2)
VERSION_NEGATIVE_TTL_S = 60  # a failed /version is not retried per request for this long
RETRY_AFTER_CAP_S = 5.0  # D2: a 429's Retry-After (seconds form) is honored, capped at this
# §4.5: fetch worst case is 15-25 s inside a ~30 s request. One request makes HTTP_RETRIES (3)
# attempts, i.e. at most 2 sleeps, each <= RETRY_AFTER_CAP_S, so retry sleeps total <= 10 s;
# RETRY_BUDGET_S makes that ceiling explicit: no sleep may exceed what remains of it.
RETRY_BUDGET_S = 10.0
RETRY_JITTER_FRACTION = 0.25  # D2: exponential backoff gets up to +25% random jitter


class Settings(BaseSettings):
    """Environment-driven configuration. Keys are optional so the service can start without them."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    planner_mode: Literal["live", "replay"] = "live"
    planner_model: str = "gpt-5.4-mini"
    planner_reasoning_effort: Literal["low", "medium", "high"] = "low"
    judge_model: str = "google/gemini-2.5-flash-lite"
    judge_fallback_model: str = "anthropic/claude-haiku-4.5"  # §9.5 tier 2; "" disables it
    # POST /v1/visualize limits (0 disables): per client IP per minute, and a global daily cap
    # that bounds LLM spend. Trust X-Forwarded-For only behind a proxy you control.
    rate_limit_per_minute: int = 10
    rate_limit_per_day: int = 500
    rate_limit_trust_forwarded_for: bool = False
    app_url: str = "http://localhost:8000"
    app_name: str = "ctviz"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (cached so .env is read once)."""
    return Settings()
