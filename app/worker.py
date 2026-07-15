"""ai-worker entrypoint (ADR-001 §21, ADR-002 §2).

Akis: RabbitMQ command tuket -> contract validation -> (fake) pipeline ->
completed/failed result event yayinla -> ack.

Bu iskelet asamasinda pipeline FAKE'tir (ADR-004 §12). Idempotency, retry ve
gercek AI pipeline sonraki adimlarda eklenecek.
"""
from __future__ import annotations

import json
import logging

from app.contracts.errors import ContractViolation
from app.contracts.validation import validate_command
from app.messaging.consumer import start_consuming
from app.messaging.publisher import publish_result
from app.pipeline.fake import build_completed_event

logger = logging.getLogger("ai-worker")


def handle_command(channel, method, properties, body: bytes) -> None:
    """Tek bir command mesajini isler."""
    routing_key = method.routing_key
    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        # Parse edilemeyen mesaj: dead-letter'a dussun (requeue yok).
        logger.warning("command JSON parse failed rk=%s -> dead-letter", routing_key)
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    # Cancel komutlari bu iskelette no-op olarak ack'lenir (Adim 4'te ele alinacak).
    if raw.get("eventType") == "ai.job.cancel.requested.v1":
        logger.info("cancel received jobId=%s (best-effort, iskelet no-op)", raw.get("jobId"))
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return

    try:
        envelope = validate_command(raw)
    except ContractViolation as exc:
        # Contract violation business rejection degildir; requeue etme.
        logger.warning("contract violation code=%s jobId=%s", exc.code.value, raw.get("jobId"))
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    completed = build_completed_event(envelope.model_dump())
    published_rk = publish_result(channel, completed, completed=True)
    logger.info(
        "job completed jobId=%s jobType=%s -> %s",
        envelope.jobId,
        envelope.jobType,
        published_rk,
    )
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("ai-worker starting; consuming command queues")
    start_consuming(handle_command)


if __name__ == "__main__":
    main()
