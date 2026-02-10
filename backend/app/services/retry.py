from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RetryExceededError(RuntimeError):
    pass


def with_backoff(
    fn: Callable[[], T],
    *,
    retries: int,
    retryable: tuple[type[BaseException], ...],
    base_delay: float = 0.5,
) -> T:
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except retryable as exc:  # type: ignore[misc]
            last_error = exc
            if attempt >= retries:
                break
            delay = (2**attempt) * base_delay + random.uniform(0.0, 0.25)
            time.sleep(delay)
    raise RetryExceededError(str(last_error)) from last_error
