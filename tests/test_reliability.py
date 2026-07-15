"""Minimum kritik guvenilirlik testleri (ADR-002 §17.1, §18.1, §12; ADR-004 §7).

Duplicate/idempotency ve retry sinirlari kritik invariant'lardir; bunlar
otomatik test edilir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.common.idempotency import JobStore, Resolution, identity_of
from app.common.retry import RetryPolicy, backoff_delay, run_with_retry
from app.contracts.errors import ErrorCode, PipelineFailure
from app.contracts.schema_store import validator_for_id
from app.pipeline.failure import build_failed_event

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"

_FAILED_SCHEMA_ID = {
    "DOCUMENT_EXTRACTION": "https://schemas.m4trust.internal/ai/document-extraction/failed-event/1.0.0",
    "VIDEO_ANALYSIS": "https://schemas.m4trust.internal/ai/video-analysis/failed-event/1.0.0",
}


def _request(rel: str = "document-extraction/full-request.json") -> dict:
    return json.loads((_EXAMPLES / rel).read_text(encoding="utf-8"))


# --- Idempotency (§17.1) ---

def test_first_delivery_is_new_then_in_progress() -> None:
    store = JobStore()
    identity = identity_of(_request())
    assert store.resolve(identity).resolution is Resolution.NEW
    # Ayni is calisirken gelen duplicate yeniden baslatilmaz.
    assert store.resolve(identity).resolution is Resolution.IN_PROGRESS


def test_duplicate_after_terminal_republishes_previous_result() -> None:
    store = JobStore()
    identity = identity_of(_request())
    store.resolve(identity)
    terminal = {"eventType": "ai.job.completed.v1", "jobId": identity.job_id}
    store.mark_terminal(identity, terminal)

    result = store.resolve(identity)
    assert result.resolution is Resolution.TERMINAL
    assert result.terminal_event is terminal  # tekrar calistirilmaz, yeniden yayinlanir


def test_same_job_id_different_input_hash_is_conflict() -> None:
    store = JobStore()
    original = _request()
    store.resolve(identity_of(original))

    tampered = _request()
    tampered["payload"]["input"]["sha256"] = "f" * 64  # ayni jobId, farkli hash
    assert store.resolve(identity_of(tampered)).resolution is Resolution.CONFLICT


# --- Retry (§18.1) ---

def test_retryable_failure_retries_until_max_attempts() -> None:
    attempts: list[int] = []

    def always_timeout(attempt: int):
        attempts.append(attempt)
        raise PipelineFailure(ErrorCode.MODEL_PROVIDER_TIMEOUT, "dependency timed out")

    with pytest.raises(PipelineFailure) as exc:
        run_with_retry(always_timeout, RetryPolicy(max_attempts=3), sleeper=lambda _: None, rand=lambda: 0.0)

    assert attempts == [1, 2, 3]  # tam 3 deneme
    assert exc.value.attempt_number == 3


def test_non_retryable_failure_is_not_retried() -> None:
    attempts: list[int] = []

    def corrupted(attempt: int):
        attempts.append(attempt)
        raise PipelineFailure(ErrorCode.CONTENT_HASH_MISMATCH, "hash mismatch")

    with pytest.raises(PipelineFailure):
        run_with_retry(corrupted, RetryPolicy(max_attempts=3), sleeper=lambda _: None, rand=lambda: 0.0)

    assert attempts == [1]  # tek deneme; hash uyusmazligi retry edilmez


def test_transient_failure_then_success() -> None:
    def flaky(attempt: int):
        if attempt < 3:
            raise PipelineFailure(ErrorCode.MODEL_PROVIDER_UNAVAILABLE, "provider unavailable")
        return "ok"

    result, attempts = run_with_retry(flaky, RetryPolicy(max_attempts=3), sleeper=lambda _: None, rand=lambda: 0.0)
    assert (result, attempts) == ("ok", 3)


def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=4.0)
    # rand=1.0 -> full jitter tavani
    assert backoff_delay(1, policy, rand=lambda: 1.0) == 1.0
    assert backoff_delay(2, policy, rand=lambda: 1.0) == 2.0
    assert backoff_delay(3, policy, rand=lambda: 1.0) == 4.0
    assert backoff_delay(9, policy, rand=lambda: 1.0) == 4.0  # tavanda kalir
    # jitter uygulanir
    assert backoff_delay(2, policy, rand=lambda: 0.0) == 0.0


# --- Failure event contract (§12) ---

@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("document-extraction/full-request.json", ErrorCode.MODEL_PROVIDER_TIMEOUT),
        ("document-extraction/full-request.json", ErrorCode.CONTENT_HASH_MISMATCH),
        ("video-analysis/full-request.json", ErrorCode.RETRIEVAL_SERVICE_UNAVAILABLE),
        ("video-analysis/full-request.json", ErrorCode.UNSUPPORTED_MEDIA_TYPE),
    ],
)
def test_failed_event_is_schema_valid(fixture: str, code: ErrorCode) -> None:
    request = _request(fixture)
    failure = PipelineFailure(code, "safe technical message", details={"reason": "timeout"})
    failure.attempt_number = 3

    event = build_failed_event(request, failure, max_attempts=3, duration_ms=1234)

    assert event["eventType"] == "ai.job.failed.v1"
    assert event["causationId"] == request["eventId"]
    validator = validator_for_id(_FAILED_SCHEMA_ID[request["jobType"]])
    errors = list(validator.iter_errors(event))
    assert errors == [], "; ".join(e.message for e in errors)


def test_failed_event_binds_category_and_retry_flag() -> None:
    request = _request()
    failure = PipelineFailure(ErrorCode.MODEL_PROVIDER_TIMEOUT, "timed out")
    event = build_failed_event(request, failure, max_attempts=3)
    error = event["payload"]["error"]
    assert error["category"] == "RETRYABLE_TECHNICAL"
    assert error["retryRecommended"] is True

    failure = PipelineFailure(ErrorCode.CORRUPTED_FILE, "corrupted")
    event = build_failed_event(request, failure, max_attempts=3)
    error = event["payload"]["error"]
    assert error["category"] == "NON_RETRYABLE_TECHNICAL"
    assert error["retryRecommended"] is False
