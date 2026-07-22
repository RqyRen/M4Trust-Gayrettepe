"""RabbitMQ connection + command consumer (ADR-002 §5, §13).

At-least-once delivery varsayilir; her mesaj duplicate-safe islenmelidir.
Bu iskelet asamasinda handler fake pipeline'i cagirir. Idempotency ve retry
Adim 4'te eklenecek.
"""
from __future__ import annotations

import ssl
import threading
from typing import Callable, TypeVar

import pika

from app.config import get_settings
from app.messaging.topology import COMMAND_BINDINGS, declare_topology

T = TypeVar("T")


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


def run_while_pumping_connection(
    operation: Callable[[], T],
    *,
    connection: pika.BlockingConnection,
    poll_interval: float = 5.0,
) -> T:
    """`operation()`'i arka plan thread'inde calistirirken cagiran (connection'i
    sahiplenen) thread'de periyodik `connection.process_data_events()` cagirir.

    Bulgu (22 Temmuz 2026, Railway'de kanitlandi): `on_message_callback` (worker.py
    handle_command) icinde uzun suren senkron bir cagri (LLM istegi, birkac dakika
    surebiliyor) oldugunda, pika `heartbeat=30` icin gereken soket I/O'sunu hic
    isleyemiyordu -- cunku connection'in sahibi olan thread tamamen o cagriya
    bloklanmisti. CloudAMQP karsilik gormeyince TCP baglantisini kendisi kapatiyor,
    sonuc zaten Redis'e guvenle yazildiktan SONRA `BrokenPipeError` firlatiliyordu
    (veri kaybi yok, ama gereksiz reconnect/gurultu).

    `BlockingConnection.process_data_events()` mevcut bir consumer callback'inin
    icinden (nested/reentrant) cagrilmayi destekler -- `_acquire_event_dispatch`
    ic ice cagrilarda yeni bir callback dispatch etmez, ama soket I/O'yu (heartbeat
    dahil) yine de isler. Bu yuzden asil agir isi (LLM cagrisi dahil) ayri bir
    thread'e verip, connection'in sahibi olan thread'i sadece bu pompalama icin
    kullanmak guvenli: channel/connection islemleri (ack/nack/publish) hep bu
    thread'de, hep `operation()` bittikten SONRA yapiliyor -- iki thread arasinda
    hic pika nesnesi paylasilmiyor.
    """
    result: list[T] = []
    error: list[BaseException] = []
    done = threading.Event()

    def _run() -> None:
        try:
            result.append(operation())
        except BaseException as exc:  # noqa: BLE001 - cagiran thread'e aynen iletilir
            error.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    while not done.is_set():
        connection.process_data_events(time_limit=poll_interval)
    thread.join()

    if error:
        raise error[0]
    return result[0]


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
