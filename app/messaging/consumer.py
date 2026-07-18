"""RabbitMQ connection + command consumer (ADR-002 §5, §13).

At-least-once delivery varsayilir; her mesaj duplicate-safe islenmelidir.
Bu iskelet asamasinda handler fake pipeline'i cagirir. Idempotency ve retry
Adim 4'te eklenecek.
"""
from __future__ import annotations

import ssl

import pika

from app.config import get_settings
from app.messaging.topology import COMMAND_BINDINGS, declare_topology


def _ssl_options(settings) -> pika.SSLOptions | None:
    """TLS/AMQPS kurulumu (bulgu, 18 Temmuz 2026 - Berke review #6).

    `ssl.create_default_context` varsayilan olarak hem CA zincirini hem de
    hostname'i dogrular (`check_hostname=True`) -- bu yuzden sunucu sertifikasi
    gecersiz veya baglanilan host sertifikadaki adla uyusmuyorsa baglanti
    reddedilir. `rabbitmq_ca_cert_path` bos ise sistem CA trust store
    kullanilir (ör. Railway'in yonetilen bir RabbitMQ'su icin yeterli olabilir);
    ozel bir CA (ör. self-signed broker) icin dosya yolu verilir.
    """
    if not settings.rabbitmq_use_tls:
        return None
    context = ssl.create_default_context(cafile=settings.rabbitmq_ca_cert_path or None)
    return pika.SSLOptions(context, settings.rabbitmq_host)


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
        ssl_options=_ssl_options(settings),
    )
    return pika.BlockingConnection(params)


def start_consuming(on_command, *, state=None) -> None:
    """Command queue'larini tuketmeye baslar (bloklar).

    on_command(channel, method, properties, body) callback'i her mesaj icin
    cagirilir; ack/nack sorumlulugu callback'e aittir.

    `state` verilirse (bkz. app.worker_health.WorkerState), gercekten
    consume etmeye baslarken/biterken isaretlenir -- worker'in /health/ready
    endpoint'i bunu okur (ADR-007 §9.2, §31).
    """
    connection = build_connection()
    channel = connection.channel()
    # Publisher confirm: broker onaylamadan basic_publish "basarili" sayilmaz
    # (bulgu, 16 Temmuz 2026 - ADR-002 SS5.3 "persistent, guvenilir yayinlama").
    channel.confirm_delivery()
    declare_topology(channel)
    channel.basic_qos(prefetch_count=get_settings().worker_prefetch)

    for binding in COMMAND_BINDINGS:
        channel.basic_consume(queue=binding.queue, on_message_callback=on_command)

    try:
        if state is not None:
            state.mark_consuming(True)
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        if state is not None:
            state.mark_consuming(False)
        connection.close()
