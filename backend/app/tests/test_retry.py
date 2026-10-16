import pytest

from app.services.retry import with_backoff


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
    original_error = ValueError("boom")
    original_error.request_json = {"model": "test-model"}
    original_error.response_json = {"status_code": 503}

    def always_fail() -> str:
        raise original_error

    with pytest.raises(ValueError) as captured:
        with_backoff(always_fail, retries=1, retryable=(ValueError,), base_delay=0)
    assert captured.value is original_error
    assert captured.value.request_json == {"model": "test-model"}
    assert captured.value.response_json == {"status_code": 503}
