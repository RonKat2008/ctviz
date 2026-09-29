"""POST /v1/visualize is rate limited (per client + global daily cap); other routes are not."""

from typing import Any

from fastapi.testclient import TestClient

from ctviz.agent.planner import Planner
from ctviz.api.app import app, get_planner, get_rate_limiter
from ctviz.api.rate_limit import RateLimiter
from ctviz.errors import OutOfScopeError

BODY = {"query": "Which drug is most effective for lung cancer?"}


class _RefusingBackend:
    """A planner backend that refuses instantly, so each request is cheap and offline."""

    def complete(self, system: str, user: str) -> Any:
        raise OutOfScopeError("Efficacy ranking is out of scope.")


def _limited(dependency_overrides: dict, per_minute: int) -> None:
    dependency_overrides[get_planner] = lambda: Planner(_RefusingBackend())
    limiter = RateLimiter(per_minute=per_minute, per_day=0)
    dependency_overrides[get_rate_limiter] = lambda: limiter


def test_visualize_returns_429_rate_limited_with_retry_after_past_the_limit(
    dependency_overrides: dict,
) -> None:
    _limited(dependency_overrides, per_minute=2)

    with TestClient(app) as client:
        statuses = [client.post("/v1/visualize", json=BODY).status_code for _ in range(2)]
        blocked = client.post("/v1/visualize", json=BODY)

    body = blocked.json()
    assert statuses == [200, 200]
    assert blocked.status_code == 429
    assert body["ok"] is False
    assert body["error"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) > 0


def test_health_schema_and_ui_are_never_rate_limited(dependency_overrides: dict) -> None:
    _limited(dependency_overrides, per_minute=1)

    with TestClient(app) as client:
        client.post("/v1/visualize", json=BODY)
        codes = {client.get(path).status_code for path in ("/health", "/v1/schema") * 5}

    assert codes == {200}


def test_rate_limit_defaults_are_on_and_configurable(monkeypatch: Any) -> None:
    from ctviz.config import Settings

    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    monkeypatch.setenv("RATE_LIMIT_PER_DAY", "7")

    settings = Settings(_env_file=None)

    assert (settings.rate_limit_per_minute, settings.rate_limit_per_day) == (3, 7)
    assert Settings.model_fields["rate_limit_per_minute"].default > 0
    assert Settings.model_fields["rate_limit_per_day"].default > 0
