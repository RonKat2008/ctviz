"""The in-memory rate limiter: per-client sliding minute window + a global daily cap."""

from ctviz.api.rate_limit import RateLimiter


class FakeClock:
    """A settable monotonic clock (seconds)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_allows_up_to_the_per_minute_limit_then_blocks_with_retry_after() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=3, per_day=0, clock=clock)

    decisions = [limiter.check("1.2.3.4") for _ in range(4)]

    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert 0 < decisions[-1].retry_after_s <= 60


def test_rate_limiter_window_slides_so_old_requests_stop_counting() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=2, per_day=0, clock=clock)
    limiter.check("a")
    limiter.check("a")

    clock.now += 61

    assert limiter.check("a").allowed is True


def test_rate_limiter_counts_each_client_separately() -> None:
    limiter = RateLimiter(per_minute=1, per_day=0, clock=FakeClock())
    limiter.check("a")

    assert limiter.check("a").allowed is False
    assert limiter.check("b").allowed is True


def test_rate_limiter_global_daily_cap_applies_across_clients() -> None:
    limiter = RateLimiter(per_minute=0, per_day=2, clock=FakeClock())

    results = [limiter.check(ip).allowed for ip in ("a", "b", "c")]

    assert results == [True, True, False]


def test_rate_limiter_zero_disables_both_limits() -> None:
    limiter = RateLimiter(per_minute=0, per_day=0, clock=FakeClock())

    assert all(limiter.check("a").allowed for _ in range(1000))


def test_rate_limiter_blocked_requests_do_not_consume_quota() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=1, per_day=0, clock=clock)
    limiter.check("a")
    for _ in range(5):
        limiter.check("a")

    clock.now += 61

    assert limiter.check("a").allowed is True
