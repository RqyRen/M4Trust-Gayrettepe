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
from app.common.cancellation import JobCancelled
from app.common.idempotency import JobStore, Resolution, ResolveResult, identity_of
from app.contracts.errors import ErrorCode, PipelineFailure
from app.messaging.publisher import PublishConfirmationFailed

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"


class _FakeMethod:
    delivery_tag = 1
    routing_key = "test"


class _FakeCancellationStore:
    """Redis'e dokunmayan test double'i -- bu dosya RabbitMQ/Redis gerektirmez."""

    def __init__(self, cancelled_job_ids: set[str] | None = None) -> None:
        self.cancelled = set(cancelled_job_ids or ())
        self.mark_calls: list[tuple[str, str]] = []

    def mark_cancelled(self, job_id: str, *, reason: str) -> None:
        self.cancelled.add(job_id)
        self.mark_calls.append((job_id, reason))

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self.cancelled

    def check(self, job_id: str) -> None:
        if job_id in self.cancelled:
            raise JobCancelled(job_id)


def _request() -> dict:
    return json.loads((_EXAMPLES / "document-extraction/full-request.json").read_text(encoding="utf-8"))


def _request_body() -> bytes:
    return json.dumps(_request()).encode("utf-8")


def _cancel_message() -> dict:
    return json.loads((_EXAMPLES / "job/cancel-request.json").read_text(encoding="utf-8"))


def test_schema_invalid_failed_event_is_dead_lettered_not_published(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_store", JobStore())
    monkeypatch.setattr(worker, "_cancellation", _FakeCancellationStore())

    def _broken_pipeline(request: dict, **_kwargs) -> dict:
        raise PipelineFailure(ErrorCode.CORRUPTED_FILE, "unreadable source")  # non-retryable, tek deneme

    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: _broken_pipeline)
    # Kasten eksik alanli, schema-invalid bir failed event uretir.
    monkeypatch.setattr(worker, "build_failed_event", lambda *a, **kw: {"eventType": "ai.job.failed.v1"})

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()
    channel.basic_publish.assert_not_called()


def test_unconfirmed_publish_preserves_computed_result_for_retry(monkeypatch) -> None:
    # Bulgu (16 Temmuz 2026 + 17 Temmuz 2026 guncelleme): broker mesaji confirm
    # etmezse job sessizce "tamamlandi" sayilmamali (dead-letter). Ama artik
    # hesaplanan sonuc da KAYBOLMAMALI/unutulmamali (bulgu, 17 Temmuz 2026 -
    # Berke review #2): pipeline'i (LLM dahil, deterministik olmayabilir)
    # bastan calistirmak yerine redelivery ayni sonucu yeniden publish etmeyi
    # denemeli. commit_pending_publish() bu yuzden publish'ten ONCE cagrilir.
    monkeypatch.setattr(worker, "_store", JobStore())
    monkeypatch.setattr(worker, "_cancellation", _FakeCancellationStore())

    computed_event = {"eventType": "ai.job.completed.v1", "jobType": "DOCUMENT_EXTRACTION"}

    def _ok_pipeline(request: dict, **_kwargs) -> dict:
        return computed_event

    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: _ok_pipeline)

    def _broken_publish(channel, event, *, completed):
        raise PublishConfirmationFailed("broker did not confirm")

    monkeypatch.setattr(worker, "publish_result", _broken_publish)

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()
    # Hesaplanan sonuc korunmus olmali -- bir sonraki denemede TEKRAR
    # CALISTIRILMAZ, ayni event yeniden publish edilmeye calisilir.
    identity = identity_of(_request())
    result = worker._store.resolve(identity)
    assert result.resolution is Resolution.TERMINAL
    assert result.terminal_event == computed_event


class _PendingPublishStore:
    """resolve() hep PENDING_PUBLISH doner -- pipeline'in tekrar CALISMADIGINI
    kanitlamak icin (bulgu, 17 Temmuz 2026 - Berke review #2)."""

    def __init__(self, pending_event: dict) -> None:
        self._pending_event = pending_event
        self.mark_terminal_calls: list[dict] = []

    def resolve(self, identity):
        return ResolveResult(Resolution.PENDING_PUBLISH, self._pending_event)

    def mark_terminal(self, identity, event):
        self.mark_terminal_calls.append(event)
        return True


def test_pending_publish_redelivery_republishes_without_recomputing(monkeypatch) -> None:
    pending_event = {"eventType": "ai.job.completed.v1", "jobType": "DOCUMENT_EXTRACTION", "result": "already-computed"}
    fake_store = _PendingPublishStore(pending_event)
    monkeypatch.setattr(worker, "_store", fake_store)

    pipeline_calls: list[int] = []
    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: pipeline_calls.append(1))

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    assert pipeline_calls == []  # pipeline HIC calismadi -- yeniden hesaplama yok
    channel.basic_publish.assert_called_once()
    published_body = json.loads(channel.basic_publish.call_args.kwargs["body"])
    assert published_body == pending_event
    assert fake_store.mark_terminal_calls == [pending_event]
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()


class _LeaseLostStore:
    """commit_pending_publish() hep False doner (lease baskasina reclaim edilmis)."""

    def resolve(self, identity):
        return ResolveResult(Resolution.NEW)

    def commit_pending_publish(self, identity, event):
        return False


def test_lease_lost_before_publish_drops_result_without_publishing(monkeypatch) -> None:
    # Bulgu (17 Temmuz 2026 - Berke review #3): eskiden worker sahiplik
    # kontrolu yapmadan publish ediyordu. commit_pending_publish() False
    # donerse artik publish HIC denenmemeli.
    monkeypatch.setattr(worker, "_store", _LeaseLostStore())
    monkeypatch.setattr(worker, "_cancellation", _FakeCancellationStore())

    def _ok_pipeline(request: dict, **_kwargs) -> dict:
        return {"eventType": "ai.job.completed.v1", "jobType": "DOCUMENT_EXTRACTION"}

    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: _ok_pipeline)

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    channel.basic_publish.assert_not_called()
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()


def test_main_health_port_honors_railway_port_env_var(monkeypatch) -> None:
    """Bulgu (17 Temmuz 2026, Berke review): Dockerfile HEALTHCHECK'i her rol icin
    ayni ${PORT:-8000}'e bakiyor. Worker'in health server'i de $PORT'u
    onurlamazsa, Railway'de worker container'i saglikli olsa bile
    healthcheck yanlis portu kontrol eder ve unhealthy gorunur.
    """
    captured_port = {}
    monkeypatch.setattr(worker, "start_health_server", lambda state, *, port: captured_port.update(port=port))
    monkeypatch.setattr(worker, "start_consuming", lambda on_command, *, state: None)

    monkeypatch.setenv("PORT", "8000")
    worker.main()
    assert captured_port["port"] == 8000


def test_main_health_port_falls_back_to_settings_when_no_port_env(monkeypatch) -> None:
    captured_port = {}
    monkeypatch.setattr(worker, "start_health_server", lambda state, *, port: captured_port.update(port=port))
    monkeypatch.setattr(worker, "start_consuming", lambda on_command, *, state: None)

    monkeypatch.delenv("PORT", raising=False)
    worker.main()
    assert captured_port["port"] == worker.get_settings().worker_health_port


def test_valid_failed_event_is_published_and_acked(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_store", JobStore())
    monkeypatch.setattr(worker, "_cancellation", _FakeCancellationStore())

    def _broken_pipeline(request: dict, **_kwargs) -> dict:
        raise PipelineFailure(ErrorCode.CORRUPTED_FILE, "unreadable source")

    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: _broken_pipeline)

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    channel.basic_publish.assert_called_once()
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()


# --- Cancellation (Berke review #1: best-effort cooperative cancellation) ---


def test_valid_cancel_message_records_intent_and_acks(monkeypatch) -> None:
    # Bulgu (Berke review #1): eskiden cancel schema validation'dan gecmeden
    # dogrudan ACK'leniyor, hicbir intent kaydedilmiyordu.
    fake_cancellation = _FakeCancellationStore()
    monkeypatch.setattr(worker, "_cancellation", fake_cancellation)

    channel = MagicMock()
    body = json.dumps(_cancel_message()).encode("utf-8")
    worker.handle_command(channel, _FakeMethod(), None, body)

    assert fake_cancellation.mark_calls == [("19191919-1919-4191-8191-191919191919", "USER_REQUESTED")]
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()
    channel.basic_publish.assert_not_called()


def test_schema_invalid_cancel_message_is_dead_lettered(monkeypatch) -> None:
    fake_cancellation = _FakeCancellationStore()
    monkeypatch.setattr(worker, "_cancellation", fake_cancellation)

    payload = _cancel_message()
    payload["payload"]["reason"] = "NOT_A_VALID_REASON"  # enum ihlali
    body = json.dumps(payload).encode("utf-8")

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, body)

    channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=False)
    channel.basic_ack.assert_not_called()
    assert fake_cancellation.mark_calls == []  # schema-invalid intent hic kaydedilmedi


def test_job_cancelled_before_start_is_skipped_without_publish(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_store", JobStore())
    request = _request()
    monkeypatch.setattr(worker, "_cancellation", _FakeCancellationStore({request["jobId"]}))

    pipeline_calls: list[int] = []
    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: pipeline_calls.append(1))

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    assert pipeline_calls == []  # pipeline_for'a hic ulasilmadi -- henuz baslamamis job calistirilmadi
    channel.basic_publish.assert_not_called()
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()
    # Claim geri birakildi -- ayni jobId, cancel olmadan tekrar gelirse calisabilmeli.
    identity = identity_of(request)
    assert worker._store.resolve(identity).resolution is Resolution.NEW


def test_job_cancelled_mid_pipeline_is_skipped_without_publish(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_store", JobStore())
    fake_cancellation = _FakeCancellationStore()
    monkeypatch.setattr(worker, "_cancellation", fake_cancellation)

    def _pipeline_that_gets_cancelled_midway(request: dict, *, check_cancelled) -> dict:
        # Baska bir mesajla (paralel thread) cancel gelmis gibi simule eder,
        # sonra pipeline'in kendi checkpoint'i bunu gormeli.
        fake_cancellation.cancelled.add(request["jobId"])
        check_cancelled()
        raise AssertionError("check_cancelled should have raised JobCancelled")

    monkeypatch.setattr(worker, "pipeline_for", lambda job_type: _pipeline_that_gets_cancelled_midway)

    channel = MagicMock()
    worker.handle_command(channel, _FakeMethod(), None, _request_body())

    channel.basic_publish.assert_not_called()
    channel.basic_ack.assert_called_once_with(delivery_tag=1)
    channel.basic_nack.assert_not_called()
