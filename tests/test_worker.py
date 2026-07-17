"""ai-worker orkestrasyon testleri (ADR-002 §11, §12).

Bulgu (16 Temmuz 2026): completed event pipeline icinde dogrulaniyordu ama
failed event hicbir yerde dogrulanmiyordu -- schema-invalid bir failed event
teorik olarak broker'a cikabilirdi. Bu dosya failure yolunun artik dogrulandigini
ve schema-invalid ciktinin publish edilmeden dead-letter'a dustugunu kanitlar.

RabbitMQ/Redis gerektirmez: channel mock'lanir, JobStore in-memory'e cevrilir.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from app import worker
from app.common.idempotency import JobStore, Resolution, identity_of
from app.contracts.errors import ErrorCode, PipelineFailure
from app.messaging.publisher import PublishConfirmationFailed

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"


class _FakeMethod:
    delivery_tag = 1
    routing_key = "test"


def _request() -> dict:
    return json.loads((_EXAMPLES / "document-extraction/full-request.json").read_text(encoding="utf-8"))


def _request_body() -> bytes:
    return json.dumps(_request()).encode("utf-8")


def test_schema_invalid_failed_event_is_dead_lettered_not_published(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_store", JobStore())

    def _broken_pipeline(request: dict) -> dict:
        raise PipelineFailure(ErrorCode.CORRUPTED_FILE, "unreadable source")  # non-retryable, tek deneme

    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: _broken_pipeline)
    # Kasten eksik alanli, schema-invalid bir failed event uretir.
    monkeypatch.setattr(worker, "build_failed_event", lambda *a, **kw: {"eventType": "ai.job.failed.v1"})

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()
    channel.basic_publish.assert_not_called()


def test_unconfirmed_publish_is_dead_lettered_and_forgotten(monkeypatch) -> None:
    # Bulgu (16 Temmuz 2026): broker mesaji confirm etmezse (ör. Spring'in
    # queue'su henuz bagli degilse) job sessizce "tamamlandi" sayilmamali.
    monkeypatch.setattr(worker, "_store", JobStore())

    def _ok_pipeline(request: dict) -> dict:
        return {"eventType": "ai.job.completed.v1", "jobType": "DOCUMENT_EXTRACTION"}

    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: _ok_pipeline)

    def _broken_publish(channel, event, *, completed):
        raise PublishConfirmationFailed("broker did not confirm")

    monkeypatch.setattr(worker, "publish_result", _broken_publish)

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()
    # Kayit unutulmus olmali -- ayni job tekrar geldiginde NEW olarak islenebilsin.
    identity = identity_of(_request())
    assert worker._store.resolve(identity).resolution is Resolution.NEW


def test_valid_failed_event_is_published_and_acked(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_store", JobStore())

    def _broken_pipeline(request: dict) -> dict:
        raise PipelineFailure(ErrorCode.CORRUPTED_FILE, "unreadable source")

    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: _broken_pipeline)

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    channel.basic_publish.assert_called_once()
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()
