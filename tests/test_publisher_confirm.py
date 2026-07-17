"""Publisher confirm testleri (ADR-002 §5.3) -- gercek RabbitMQ ile.

Bulgu (16 Temmuz 2026): `m4trust.ai.events` exchange'ine su an hicbir queue
bagli degil (Spring kendi consumer queue'sunu henuz deklare etmedi -- bu
Spring'in sorumlulugu, ADR-002 §5.4). Bu, confirm+mandatory OLMADAN, Spring
hazir olmadan basilan her completed/failed event'in SESSIZCE kayboldugu
anlamina geliyor. confirm_delivery()+mandatory=True bunu artik gorunur kilar
(PublishConfirmationFailed).

Bu testler gercek RabbitMQ'ya karsi calisir; erisilemezse atlanir.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.messaging.consumer import build_connection
from app.messaging.publisher import PublishConfirmationFailed, publish_result
from app.messaging.topology import EXCHANGE_EVENTS, declare_topology


def _rabbitmq_available() -> bool:
    try:
        conn = build_connection()
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _rabbitmq_available(), reason="local RabbitMQ (docker-compose) not reachable")


def _completed_event() -> dict:
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": "ai.job.completed.v1",
        "jobType": "DOCUMENT_EXTRACTION",
    }


def test_publish_without_bound_consumer_raises_publish_confirmation_failed() -> None:
    """Spring henuz baglanmamissa (gercek durum) publish sessizce kaybolmaz, hata verir."""
    connection = build_connection()
    channel = connection.channel()
    declare_topology(channel)
    channel.confirm_delivery()

    with pytest.raises(PublishConfirmationFailed):
        publish_result(channel, _completed_event(), completed=True)

    connection.close()


def test_publish_with_bound_consumer_is_confirmed_and_delivered() -> None:
    """Spring'in gelecekte yapacagi gibi bir queue bagliyken publish basarili olur ve mesaj gercekten ulasir."""
    connection = build_connection()
    channel = connection.channel()
    declare_topology(channel)

    temp_queue = f"test.spring-simulation.{uuid.uuid4().hex}"
    channel.queue_declare(temp_queue, durable=False, auto_delete=True)
    channel.queue_bind(temp_queue, EXCHANGE_EVENTS, routing_key="ai.document-extraction.completed.v1")
    channel.confirm_delivery()

    event = _completed_event()
    routing_key = publish_result(channel, event, completed=True)
    assert routing_key == "ai.document-extraction.completed.v1"

    method, _properties, body = channel.basic_get(temp_queue, auto_ack=True)
    assert method is not None, "published message never reached the simulated consumer queue"
    assert json.loads(body)["eventId"] == event["eventId"]

    channel.queue_delete(temp_queue)
    connection.close()
