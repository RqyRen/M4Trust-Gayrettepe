"""Result event publisher (ADR-002 §5.3).

Completed/failed event'leri m4trust.ai.events exchange'ine, jobType'a uygun
result routing key ile persistent olarak basar.
"""
from __future__ import annotations

import json

import pika

from app.messaging.topology import EXCHANGE_EVENTS, RESULT_ROUTING

_PERSISTENT = pika.BasicProperties(content_type="application/json", delivery_mode=2)


def _routing_key(job_type: str, *, completed: bool) -> str:
    if job_type not in RESULT_ROUTING:
        raise ValueError(f"no result routing for jobType {job_type!r}")
    completed_rk, failed_rk = RESULT_ROUTING[job_type]
    return completed_rk if completed else failed_rk


def publish_result(channel, event: dict, *, completed: bool) -> str:
    """Result event'ini yayinlar; kullanilan routing key'i dondurur."""
    routing_key = _routing_key(event["jobType"], completed=completed)
    channel.basic_publish(
        exchange=EXCHANGE_EVENTS,
        routing_key=routing_key,
        body=json.dumps(event).encode("utf-8"),
        properties=_PERSISTENT,
    )
    return routing_key
