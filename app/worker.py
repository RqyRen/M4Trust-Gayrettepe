"""ai-worker entrypoint (ADR-001 §21, ADR-002 §2).

Akis:
  RabbitMQ command
    -> contract validation
    -> idempotency (duplicate-safe, §17.1)
    -> pipeline + teknik retry (§18.1)
    -> completed / failed result event (§11, §12)
    -> ack
"""
from __future__ import annotations

import json
import logging
import os
import time

from app.common import metrics
from app.common.cancellation import JobCancelled, build_cancellation_store
from app.common.heartbeat import run_with_heartbeat
from app.common.idempotency import Resolution, build_job_store, identity_of
from app.common.logging_setup import configure_logging
from app.common.retry import DEFAULT_POLICY, run_with_retry
from app.config import get_settings
from app.contracts.errors import ContractViolation, ErrorCode, PipelineFailure
from app.contracts.validation import (
    validate_cancel_semantic_consistency,
    validate_command,
    validate_outgoing,
    validate_semantic_consistency,
)
from app.messaging.consumer import start_consuming
from app.messaging.publisher import PublishConfirmationFailed, publish_result
from app.pipeline.failure import build_failed_event
from app.pipeline.registry import pipeline_for
from app.worker_health import WorkerState, start_health_server

logger = logging.getLogger("ai-worker")

_store = build_job_store()
_cancellation = build_cancellation_store()


def _publish_and_record(channel, request: dict, identity, event: dict, *, completed: bool) -> None:
    routing_key = publish_result(channel, event, completed=completed)
    recorded = _store.mark_terminal(identity, event)
    if not recorded:
        # Lease baska bir worker'a reclaim edilmis (bu worker cok yavas kalmis
        # olabilir); Spring'e yine de yayinlandi, ama bu artik zombie bir
        # yazma -- kayit baskasinin sonucuna dokunulmadan atlandi (bulgu,
        # 16 Temmuz 2026).
        metrics.increment("lease_reclaim_count")
        logger.warning(
            "terminal record rejected (lease reclaimed by another worker) jobId=%s",
            request["jobId"],
        )
    logger.info(
        "job terminal -> %s",
        routing_key,
        extra={"jobId": request["jobId"], "jobType": request["jobType"], "correlationId": request["correlationId"]},
    )


def handle_command(channel, method, properties, body: bytes) -> None:
    """Tek bir command mesajini duplicate-safe olarak isler."""
    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        metrics.increment("dead_letter_count")
        logger.warning("command JSON parse failed rk=%s -> dead-letter", method.routing_key)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Cancel best-effort; ayri cancelled event zorunlu degil (§20), ama mesaj
    # yine de contract validation'dan gecmeli (bulgu, 17 Temmuz 2026 - Berke
    # review #1: eskiden schema-invalid bir cancel bile sessizce ACK'leniyordu).
    if raw.get("eventType") == "ai.job.cancel.requested.v1":
        try:
            cancel_envelope = validate_command(raw)
        except ContractViolation as exc:
            metrics.increment("contract_violation_count")
            metrics.increment("dead_letter_count")
            if exc.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION:
                metrics.increment("unsupported_schema_version_count")
            logger.warning(
                "cancel contract violation code=%s -> dead-letter",
                exc.code.value,
                extra={"jobId": raw.get("jobId")},
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        cancel_request = cancel_envelope.model_dump()
        try:
            validate_cancel_semantic_consistency(cancel_request, routing_key=method.routing_key)
        except ContractViolation as exc:
            metrics.increment("contract_violation_count")
            metrics.increment("dead_letter_count")
            logger.warning(
                "cancel semantic contract violation code=%s -> dead-letter",
                exc.code.value,
                extra={"jobId": cancel_request.get("jobId")},
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        reason = cancel_request["payload"]["reason"]
        metrics.increment("cancellation_requested_count")
        _cancellation.mark_cancelled(cancel_request["jobId"], reason=reason)
        logger.info(
            "cancellation intent recorded reason=%s",
            reason,
            extra={"jobId": cancel_request["jobId"]},
        )
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    try:
        envelope = validate_command(raw)
    except ContractViolation as exc:
        metrics.increment("contract_violation_count")
        metrics.increment("dead_letter_count")
        if exc.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION:
            metrics.increment("unsupported_schema_version_count")
        logger.warning(
            "contract violation code=%s -> dead-letter",
            exc.code.value,
            extra={"jobId": raw.get("jobId")},
        )
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    request = envelope.model_dump()

    # Berke review #11: sekil gecerli ama tutarsiz mesajlari (yanlis routing
    # key'den gelmis, beklenmeyen bir producer, subjectId'nin belge/video
    # ID'siyle uyusmamasi) burada yakala.
    try:
        validate_semantic_consistency(request, routing_key=method.routing_key)
    except ContractViolation as exc:
        metrics.increment("contract_violation_count")
        metrics.increment("dead_letter_count")
        logger.warning(
            "semantic contract violation code=%s -> dead-letter",
            exc.code.value,
            extra={"jobId": raw.get("jobId")},
        )
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    identity = identity_of(request)
    resolved = _store.resolve(identity)
    log_context = {
        "jobId": identity.job_id,
        "jobType": identity.job_type,
        "correlationId": request["correlationId"],
    }

    if resolved.resolution is Resolution.CONFLICT:
        # Ayni jobId + farkli input hash: contract violation (§17.1).
        metrics.increment("job_identity_conflict_count")
        metrics.increment("dead_letter_count")
        logger.warning("job identity conflict -> dead-letter", extra=log_context)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # ADR-002 §30: gecerli (conflict olmayan) her job istegi burada sayilir --
    # ilk teslimat da, asagidaki dallardan biriyle duplicate sayilan
    # yeniden-teslimatlar da (at-least-once delivery, §13).
    metrics.increment("ai_jobs_requested_total")

    if resolved.resolution is Resolution.IN_PROGRESS:
        # Devam eden isi yeniden baslatma; duplicate teslimati sessizce ack'le.
        metrics.increment("ai_job_duplicate_total")
        logger.info("duplicate while in progress -> skipped", extra=log_context)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    if resolved.resolution is Resolution.TERMINAL:
        # Tamamlanan isi tekrar calistirma; onceki terminal sonucu yeniden yayinla.
        metrics.increment("ai_job_duplicate_total")
        event = resolved.terminal_event
        completed = event["eventType"] == "ai.job.completed.v1"
        try:
            publish_result(channel, event, completed=completed)
        except PublishConfirmationFailed:
            # Kayit zaten terminal; forget YOK -- sadece bu redelivery'i dead-letter'a birak.
            metrics.increment("dead_letter_count")
            logger.exception("broker did not confirm terminal republish -> dead-letter", extra=log_context)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        metrics.increment("terminal_republish_count")
        logger.info("duplicate after terminal -> republished", extra=log_context)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    if resolved.resolution is Resolution.PENDING_PUBLISH:
        # Bulgu (17 Temmuz 2026, Berke review #2): onceki calistirmada sonuc
        # zaten hesaplanip durable yazilmisti ama publish confirm edilememisti
        # (crash veya broker sorunu). Pipeline'i TEKRAR CALISTIRMADAN -- LLM
        # deterministik olmadigi icin ayni jobId'ye iki farkli sonuc uretmemek
        # adina -- ayni event'i yeniden publish etmeyi dener.
        metrics.increment("ai_job_duplicate_total")
        event = resolved.terminal_event
        completed = event["eventType"] == "ai.job.completed.v1"
        try:
            publish_result(channel, event, completed=completed)
        except PublishConfirmationFailed:
            metrics.increment("dead_letter_count")
            logger.exception("broker still did not confirm pending event -> dead-letter", extra=log_context)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        metrics.increment("terminal_republish_count")
        if not _store.mark_terminal(identity, event):
            metrics.increment("lease_reclaim_count")
            logger.warning("terminal record rejected after pending republish", extra=log_context)
        logger.info("pending publish retried -> republished and finalized", extra=log_context)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    if _cancellation.is_cancelled(identity.job_id):
        # Berke review #1: henuz baslamamis job calistirilmamali. Claim'i geri
        # birak ki lease bosuna idempotency_lease_seconds kadar kilitli kalmasin.
        _store.forget(identity)
        metrics.increment("cancellation_completed_count")
        logger.info("job cancelled before start -> skipped, no result published", extra=log_context)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    started = time.monotonic()
    pipeline = pipeline_for(envelope.jobType)
    heartbeat_interval = get_settings().idempotency_heartbeat_interval_seconds
    check_cancelled = lambda: _cancellation.check(identity.job_id)
    try:
        # Heartbeat (bulgu, 17 Temmuz 2026 - Berke review #3): pipeline uzun
        # surerse (buyuk OCR+LLM, video) lease'in periyodik yenilenmesi,
        # gercekten calisan bir worker'in job'i yanlislikla "coktu" sayilip
        # baskasina devredilmesini engeller.
        event, attempts = run_with_heartbeat(
            lambda: run_with_retry(lambda attempt: pipeline(request, check_cancelled=check_cancelled), DEFAULT_POLICY),
            renew_lease=lambda: _store.renew_lease(identity),
            interval_seconds=heartbeat_interval,
        )
    except JobCancelled:
        # Bulgu (Berke review #1): pipeline bir checkpoint'te iptali gordu.
        # Henuz hicbir sonuc durable yazilmadi (PENDING_PUBLISH'e ulasilmadi),
        # bu yuzden guvenle atlanabilir -- claim'i geri birak.
        _store.forget(identity)
        metrics.increment("cancellation_completed_count")
        logger.info("job cancelled mid-pipeline -> skipped, no result published", extra=log_context)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return
    except PipelineFailure as failure:
        duration_ms = int((time.monotonic() - started) * 1000)
        metrics.increment("ai_jobs_failed_total")
        metrics.increment("ai_job_duration_seconds_sum", amount=duration_ms / 1000)
        metrics.increment("ai_job_duration_seconds_count")
        if _cancellation.is_cancelled(identity.job_id):
            # ADR-002 §20: cancel, son check_cancelled() checkpoint'inden SONRA
            # gelmis olabilir -- pipeline yine de bitirip sonuc uretti. Best-effort
            # semantigine uygun olarak yine de yayinlanir, sadece "gec sonuc"
            # olarak sayilir; kabul/ret karari Spring'e ait.
            metrics.increment("ai_late_result_total")
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
            metrics.increment("contract_violation_count")
            metrics.increment("dead_letter_count")
            logger.exception("failed event failed schema validation -> dead-letter", extra=log_context)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        logger.warning(
            "job failed code=%s attempt=%s/%s",
            failure.code.value,
            failure.attempt_number,
            DEFAULT_POLICY.max_attempts,
            extra={**log_context, "attemptNumber": failure.attempt_number},
        )
        # Bulgu (17 Temmuz 2026, Berke review #2): publish'ten ONCE event'i
        # durable yazar (PENDING_PUBLISH) VE hala lease sahibi oldugunu
        # dogrular. Sahiplik kaybedilmisse publish HIC yapilmaz -- baskasi
        # zaten devralmis/bitirmis demektir.
        if not _store.commit_pending_publish(identity, failed_event):
            metrics.increment("lease_reclaim_count")
            logger.warning("lease reclaimed before publish; dropping this attempt's failed result", extra=log_context)
            channel.basic_ack(delivery_tag=method.delivery_tag)
            return
        try:
            _publish_and_record(channel, request, identity, failed_event, completed=False)
        except PublishConfirmationFailed:
            # forget YOK: event PENDING_PUBLISH olarak durable kaldi, sonraki
            # redelivery pipeline'i tekrar calistirmadan yeniden publish dener.
            metrics.increment("dead_letter_count")
            logger.exception(
                "broker did not confirm failed event -> dead-letter (preserved as PENDING_PUBLISH)",
                extra=log_context,
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return
    except Exception:
        # Beklenmeyen hata: kaydi geri al ki mesaj yeniden islenebilsin.
        _store.forget(identity)
        metrics.increment("dead_letter_count")
        logger.exception("unexpected worker error -> dead-letter", extra=log_context)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    duration_seconds = time.monotonic() - started
    metrics.increment("ai_jobs_completed_total")
    metrics.increment("ai_job_duration_seconds_sum", amount=duration_seconds)
    metrics.increment("ai_job_duration_seconds_count")
    if _cancellation.is_cancelled(identity.job_id):
        # ADR-002 §20: bkz. failure dalindaki ayni mantik icin yorum.
        metrics.increment("ai_late_result_total")
    logger.info("job succeeded attempts=%s", attempts, extra={**log_context, "attemptNumber": attempts})
    if not _store.commit_pending_publish(identity, event):
        metrics.increment("lease_reclaim_count")
        logger.warning("lease reclaimed before publish; dropping this attempt's result", extra=log_context)
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return
    try:
        _publish_and_record(channel, request, identity, event, completed=True)
    except PublishConfirmationFailed:
        metrics.increment("dead_letter_count")
        logger.exception(
            "broker did not confirm completed event -> dead-letter (preserved as PENDING_PUBLISH)",
            extra=log_context,
        )
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    settings = get_settings()
    configure_logging(
        service=settings.service_name,
        environment=settings.app_env,
        version=settings.service_version,
        level=settings.log_level,
    )
    state = WorkerState()
    # Bulgu (17 Temmuz 2026, Berke'nin review'u): Dockerfile HEALTHCHECK'i her
    # rol icin ayni ${PORT:-8000}'e bakiyor (ai-api ve ai-worker ayni image'i
    # kullaniyor). ai-api zaten Railway'in verdigi $PORT'u dinliyor; worker'in
    # health server'i da ayni degiskeni onurlarsa healthcheck rol-agnostik
    # calisir. $PORT yoksa (local dev) settings.worker_health_port'a duser --
    # yerelde ai-api ile ayni portta cakismamak icin.
    health_port = int(os.environ.get("PORT") or settings.worker_health_port)
    start_health_server(state, port=health_port)
    logger.info("ai-worker starting; consuming command queues")
    start_consuming(handle_command, state=state)


if __name__ == "__main__":
    main()
