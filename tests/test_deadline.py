"""`check_deadline` testleri (Berke review #5).

Suresi gecmis bir `payload.deadlineAt` stable `INVALID_DEADLINE` failure
uretmeli ve retry edilmemeli (pahali provider cagrisi tekrar denenmemeli).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contracts.errors import ErrorCode, PipelineFailure, retry_recommended_for
from app.pipeline.deadline import check_deadline


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_future_deadline_does_not_raise() -> None:
    future = _iso(datetime.now(timezone.utc) + timedelta(minutes=10))
    check_deadline(future)  # firlatmamali


def test_past_deadline_raises_invalid_deadline() -> None:
    past = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    with pytest.raises(PipelineFailure) as exc:
        check_deadline(past)
    assert exc.value.code is ErrorCode.INVALID_DEADLINE
    assert exc.value.category.value == "INVALID_INPUT"
    assert exc.value.details == {"field": "payload.deadlineAt", "reason": "deadline is in the past"}


def test_invalid_deadline_is_non_retryable() -> None:
    # Suresi gecmis bir job'i tekrar denemek anlamsizdir -- pahali provider
    # cagrisi bir daha yapilmamali (ADR-002 §12.1 kategori<->retry baglantisi).
    assert retry_recommended_for(ErrorCode.INVALID_DEADLINE) is False
