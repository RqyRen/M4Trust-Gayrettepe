"""ai-worker entrypoint (ADR-001 §21, ADR-002 §2).

Akis:
  RabbitMQ command
    -> contract validation
    -> idempotency (duplicate-safe, §17.1)
    -> pipeline + teknik retry (§18.1)
    -> completed / failed result event (§11, §12)
    -> ack

Pipeline bu asamada FAKE'tir (ADR-004 §12); gercek AI pipeline sonraki
adimlarda bu noktaya takilacak.
"""
from __future__ import annotations

import json
import logging
import time

from app.common.idempotency import Resolution, build_job_store, identity_of
from app.common.retry import DEFAULT_POLICY, run_with_retry
from app.config import get_settings
from app.contracts.errors import ContractViolation, PipelineFailure
from app.contracts.validation import validate_command, validate_outgoing
from app.messaging.consumer import start_consuming
from app.messaging.publisher import PublishConfirmationFailed, publish_result
from app.pipeline.failure import build_failed_event
from app.pipeline.registry import pipeline_for
from app.worker_health import WorkerState, start_health_server

logger = logging.getLogger("ai-worker")

_store = build_job_store()


def _publish_and_record(channel, request: dict, identity, event: dict, *, completed: bool) -> None:
    routing_key = publish_result(channel, event, completed=completed)
    _store.mark_terminal(identity, event)
    logger.info(
        "job terminal jobId=%s jobType=%s completed=%s -> %s",
        request["jobId"],
        request["jobType"],
        completed,
        routing_key,
    )


def handle_command(channel, method, properties, body: bytes) -> None:
    """Tek bir command mesajini duplicate-safe olarak isler."""
    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("command JSON parse failed rk=%s -> dead-letter", method.routing_key)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Cancel best-effort; ilk surumde ayri cancelled event zorunlu degil (§20).
    if raw.get("eventType") == "ai.job.cancel.requested.v1":
        logger.info("cancel received jobId=%s (best-effort)", raw.get("jobId"))
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    try:
        envelope = validate_command(raw)
    except ContractViolation as exc:
        logger.warning("contract violation code=%s jobId=%s", exc.code.value, raw.get("jobId"))
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    request = envelope.model_dump()
    identity = identity_of(request)
    resolved = _store.resolve(identity)

    if resolved.resolution is Resolution.CONFLICT:
        # Ayni jobId + farkli input hash: contract violation (§17.1).
        logger.warning("job identity conflict jobId=%s -> dead-letter", identity.job_id)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    if resolved.resolution is Resolution.IN_PROGRESS:
        # Devam eden isi yeniden baslatma; duplicate teslimati sessizce ack'le.
        logger.info("duplicate while in progress jobId=%s -> skipped", identity.job_id)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    if resolved.resolution is Resolution.TERMINAL:
        # Tamamlanan isi tekrar calistirma; onceki terminal sonucu yeniden yayinla.
        event = resolved.terminal_event
        completed = event["eventType"] == "ai.job.completed.v1"
        try:
            publish_result(channel, event, completed=completed)
        except PublishConfirmationFailed:
            # Kayit zaten terminal; forget YOK -- sadece bu redelivery'i dead-letter'a birak.
            logger.exception("broker did not confirm terminal republish jobId=%s -> dead-letter", identity.job_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        logger.info("duplicate after terminal jobId=%s -> republished", identity.job_id)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    started = time.monotonic()
    pipeline = pipeline_for(envelope.jobType)
    try:
        event, attempts = run_with_retry(lambda attempt: pipeline(request), DEFAULT_POLICY)
    except PipelineFailure as failure:
        duration_ms = int((time.monotonic() - started) * 1000)
        failed_event = build_failed_event(
            request, failure, max_attempts=DEFAULT_POLICY.max_attempts, duration_ms=duration_ms
        )
        try:
            # Completed event'ler pipeline icinde dogrulanir; failed event'ler
            # burada uretildigi icin ayni garanti burada saglanmali (bulgu,
            # 16 Temmuz 2026: bu kontrol daha once hic yoktu).
            validate_outgoing(failed_event)
        except ContractViolation:
            _store.forget(identity)
            logger.exception("failed event failed schema validation jobId=%s -> dead-letter", identity.job_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        logger.warning(
            "job failed jobId=%s code=%s attempt=%s/%s",
            identity.job_id,
            failure.code.value,
            failure.attempt_number,
            DEFAULT_POLICY.max_attempts,
        )
        try:
            _publish_and_record(channel, request, identity, failed_event, completed=False)
        except PublishConfirmationFailed:
            _store.forget(identity)
            logger.exception("broker did not confirm failed event jobId=%s -> dead-letter", identity.job_id)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return
    except Exception:
        # Beklenmeyen hata: kaydi geri al ki mesaj yeniden islenebilsin.
        _store.forget(identity)
        logger.exception("unexpected worker error jobId=%s -> dead-letter", identity.job_id)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    logger.info("job succeeded jobId=%s attempts=%s", identity.job_id, attempts)
    try:
        _publish_and_record(channel, request, identity, event, completed=True)
    except PublishConfirmationFailed:
        _store.forget(identity)
        logger.exception("broker did not confirm completed event jobId=%s -> dead-letter", identity.job_id)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    state = WorkerState()
    start_health_server(state, port=settings.worker_health_port)
    logger.info("ai-worker starting; consuming command queues")
    start_consuming(handle_command, state=state)


if __name__ == "__main__":
    main()
