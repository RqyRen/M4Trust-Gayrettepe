"""Result event publisher (ADR-002 §5.3).

Completed/failed event'leri m4trust.ai.events exchange'ine, jobType'a uygun
result routing key ile persistent olarak basar.

Channel `confirm_delivery()` modundadir (bkz. consumer.py); broker mesaji
onaylamazsa/nack'lerse/unroutable ise `basic_publish` istisna firlatir. Bu
sessizce "gonderildi sanildi ama gitmedi" senaryosunu engeller (bulgu,
16 Temmuz 2026).
"""
from __future__ import annotations

import json

import pika
import pika.exceptions

from app.messaging.topology import EXCHANGE_EVENTS, RESULT_ROUTING

_PERSISTENT = pika.BasicProperties(content_type="application/json", delivery_mode=2)


class PublishConfirmationFailed(Exception):
    """Broker publish'i confirm etmedi (unroutable veya nack)."""


def _routing_key(job_type: str, *, completed: bool) -> str:
    if job_type not in RESULT_ROUTING:
        raise ValueError(f"no result routing for jobType {job_type!r}")
    completed_rk, failed_rk = RESULT_ROUTING[job_type]
    return completed_rk if completed else failed_rk


def publish_result(channel, event: dict, *, completed: bool) -> str:
    """Result event'ini yayinlar; kullanilan routing key'i dondurur.

    Firlatir: PublishConfirmationFailed -- broker mesaji kabul etmedi.
    """
    routing_key = _routing_key(event["jobType"], completed=completed)
    try:
        channel.basic_publish(
            exchange=EXCHANGE_EVENTS,
            routing_key=routing_key,
            body=json.dumps(event).encode("utf-8"),
            properties=_PERSISTENT,
            # mandatory=True olmadan confirm_delivery() yalniz "broker mesaji
            # aldi"yi garanti eder; routable-degil (baglanti/binding kopmus)
            # mesajlar sessizce dusurulur. mandatory, bunu da UnroutableError'a cevirir.
            mandatory=True,
        )
    except (pika.exceptions.UnroutableError, pika.exceptions.NackError) as exc:
        raise PublishConfirmationFailed(f"broker did not confirm publish for routingKey={routing_key}") from exc
    return routing_key
