"""RedisJobStore testleri (ADR-002 §17.1) — coklu worker replica'sinda paylasimli kayit.

Gercek local Redis uzerinden calisir (docker-compose: `m4trust-redis`), ayni
RabbitMQ/MinIO testlerindeki gibi (ucretsiz/local altyapi gercek calistirilir).
Redis erisilemezse test modulu atlanir.
"""
from __future__ import annotations

import threading
import uuid

import pytest
import redis as redis_lib

from app.common.idempotency import JobIdentity, RedisJobStore, Resolution

_REDIS_HOST = "localhost"
_REDIS_PORT = 6379


def _redis_available() -> bool:
    try:
        client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, socket_connect_timeout=1)
        return client.ping()
    except redis_lib.RedisError:
        return False


pytestmark = pytest.mark.skipif(not _redis_available(), reason="local Redis (docker-compose) not reachable")


@pytest.fixture
def store() -> RedisJobStore:
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    yield RedisJobStore(client, ttl_seconds=30)
    for key in client.scan_iter("m4trust:idempotency:*"):
        client.delete(key)


def _identity() -> JobIdentity:
    return JobIdentity(job_id=str(uuid.uuid4()), job_type="DOCUMENT_EXTRACTION", input_sha256="a" * 64)


def test_first_delivery_is_new_then_in_progress(store: RedisJobStore) -> None:
    identity = _identity()
    assert store.resolve(identity).resolution is Resolution.NEW
    assert store.resolve(identity).resolution is Resolution.IN_PROGRESS


def test_duplicate_after_terminal_republishes_previous_result(store: RedisJobStore) -> None:
    identity = _identity()
    store.resolve(identity)
    terminal = {"eventType": "ai.job.completed.v1", "jobId": identity.job_id}
    store.mark_terminal(identity, terminal)

    result = store.resolve(identity)
    assert result.resolution is Resolution.TERMINAL
    assert result.terminal_event == terminal


def test_same_job_id_different_input_hash_is_conflict(store: RedisJobStore) -> None:
    identity = _identity()
    store.resolve(identity)

    tampered = JobIdentity(job_id=identity.job_id, job_type=identity.job_type, input_sha256="f" * 64)
    assert store.resolve(tampered).resolution is Resolution.CONFLICT


def test_forget_allows_retry_only_if_not_terminal(store: RedisJobStore) -> None:
    identity = _identity()
    store.resolve(identity)
    store.forget(identity)
    assert store.resolve(identity).resolution is Resolution.NEW

    store.resolve(identity)
    store.mark_terminal(identity, {"eventType": "ai.job.completed.v1", "jobId": identity.job_id})
    store.forget(identity)  # terminal kayit korunmali
    assert store.resolve(identity).resolution is Resolution.TERMINAL


def test_concurrent_resolve_across_shared_store_yields_exactly_one_new(store: RedisJobStore) -> None:
    """Coklu worker'in ayni jobId'i ayni anda islemeye baslamasi senaryosu.

    Bu, in-memory JobStore'un cozemedigi asil sorundu: her worker process
    kendi hafizasinda ayri kayit tuttugu icin birden fazlasi NEW donebiliyordu.
    Burada TEK bir Redis'e 16 thread ayni anda `resolve()` cagiriyor; Redis'in
    atomik SET NX'i sayesinde tam olarak biri NEW, digerleri IN_PROGRESS almali.
    """
    identity = _identity()
    resolutions: list[Resolution] = []
    lock = threading.Lock()
    barrier = threading.Barrier(16)

    def worker() -> None:
        barrier.wait()
        result = store.resolve(identity)
        with lock:
            resolutions.append(result.resolution)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert resolutions.count(Resolution.NEW) == 1
    assert resolutions.count(Resolution.IN_PROGRESS) == 15
