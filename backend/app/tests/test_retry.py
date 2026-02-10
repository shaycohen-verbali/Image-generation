from app.services.retry import RetryExceededError, with_backoff


def test_with_backoff_retries_until_success() -> None:
    calls = {"count": 0}

    def flaky() -> str:
        calls["count"] += 1
        if calls["count"] < 3:
            raise ValueError("temporary")
        return "ok"

    result = with_backoff(flaky, retries=3, retryable=(ValueError,), base_delay=0)
    assert result == "ok"
    assert calls["count"] == 3


def test_with_backoff_raises_when_exhausted() -> None:
    def always_fail() -> str:
        raise ValueError("boom")

    try:
        with_backoff(always_fail, retries=1, retryable=(ValueError,), base_delay=0)
        assert False, "expected RetryExceededError"
    except RetryExceededError:
        assert True
