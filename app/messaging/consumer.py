"""RabbitMQ connection + command consumer (ADR-002 §5, §13).

At-least-once delivery varsayilir; her mesaj duplicate-safe islenmelidir.
Bu iskelet asamasinda handler fake pipeline'i cagirir. Idempotency ve retry
Adim 4'te eklenecek.
"""
from __future__ import annotations

import pika

from app.config import get_settings
from app.messaging.topology import COMMAND_BINDINGS, declare_topology


def build_connection() -> pika.BlockingConnection:
    settings = get_settings()
    credentials = pika.PlainCredentials(settings.rabbitmq_user, settings.rabbitmq_password)
    params = pika.ConnectionParameters(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        virtual_host=settings.rabbitmq_vhost,
        credentials=credentials,
        heartbeat=30,
        blocked_connection_timeout=15,
    )
    return pika.BlockingConnection(params)


def start_consuming(on_command) -> None:
    """Command queue'larini tuketmeye baslar (bloklar).

    on_command(channel, method, properties, body) callback'i her mesaj icin
    cagirilir; ack/nack sorumlulugu callback'e aittir.
    """
    connection = build_connection()
    channel = connection.channel()
    declare_topology(channel)
    channel.basic_qos(prefetch_count=get_settings().worker_prefetch)

    for binding in COMMAND_BINDINGS:
        channel.basic_consume(queue=binding.queue, on_message_callback=on_command)

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        connection.close()
