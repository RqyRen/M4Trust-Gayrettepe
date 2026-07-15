"""Teknik retry politikasi (ADR-002 §18.1).

- Retryable teknik hatalarda en fazla 3 deneme.
- Attempt 2 ve 3 exponential backoff + jitter ile.
- Retry hep AYNI jobId altinda yurur; yeni job uretilmez.
- Non-retryable hata derhal terminal olur.
- Exact sureler operasyonel konfigurasyondur, contract'in parcasi DEGILDIR.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from app.contracts.errors import PipelineFailure, retry_recommended_for

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0


DEFAULT_POLICY = RetryPolicy()


def backoff_delay(attempt: int, policy: RetryPolicy, rand: Callable[[], float] = random.random) -> float:
    """attempt=1 sonrasi beklenecek sure: exponential + full jitter."""
    ceiling = min(policy.base_delay_seconds * (2 ** (attempt - 1)), policy.max_delay_seconds)
    return ceiling * rand()


def run_with_retry(
    operation: Callable[[int], T],
    policy: RetryPolicy = DEFAULT_POLICY,
    *,
    sleeper: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> tuple[T, int]:
    """operation(attempt_number) calistirir.

    Doner: (sonuc, kullanilan_attempt_sayisi)
    Firlatir: PipelineFailure — `attempt_number` alani doldurulmus olarak.
    """
    last_failure: PipelineFailure | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation(attempt), attempt
        except PipelineFailure as failure:
            failure.attempt_number = attempt
            last_failure = failure
            # Non-retryable veya son deneme -> terminal.
            if not retry_recommended_for(failure.code) or attempt == policy.max_attempts:
                raise
            sleeper(backoff_delay(attempt, policy, rand))

    assert last_failure is not None  # pragma: no cover - donguden cikilamaz
    raise last_failure
