"""CancellationStore testleri (Berke review #1) — gercek local Redis uzerinden.

Ayni docker-compose Redis'i kullanir (ADR-002 §17.1 idempotency testleriyle
ayni altyapi, bkz. test_idempotency_redis.py). Redis erisilemezse test
modulu atlanir.
"""
from __future__ import annotations

import uuid

import pytest
import redis as redis_lib

from app.common.cancellation import CancellationStore, JobCancelled

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
def store() -> CancellationStore:
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    yield CancellationStore(client, ttl_seconds=30)
    for key in client.scan_iter("m4trust:cancellation:*"):
        client.delete(key)


def test_unmarked_job_is_not_cancelled(store: CancellationStore) -> None:
    assert store.is_cancelled(str(uuid.uuid4())) is False


def test_marked_job_is_cancelled(store: CancellationStore) -> None:
    job_id = str(uuid.uuid4())
    store.mark_cancelled(job_id, reason="USER_REQUESTED")
    assert store.is_cancelled(job_id) is True


def test_check_raises_only_for_cancelled_job(store: CancellationStore) -> None:
    cancelled_job = str(uuid.uuid4())
    other_job = str(uuid.uuid4())
    store.mark_cancelled(cancelled_job, reason="TRANSACTION_CANCELLED")

    store.check(other_job)  # firlatmaz

    with pytest.raises(JobCancelled) as exc:
        store.check(cancelled_job)
    assert exc.value.job_id == cancelled_job


def test_cancellation_intent_is_visible_across_store_instances(store: CancellationStore) -> None:
    """Paylasimli Redis: bir worker'in ACK'ledigi cancel mesaji, baska bir
    worker process'inin (ayri baglanti, ayni anahtar alani) pipeline'inda da
    gorulebilmeli -- coklu replica arasinda cancellation intent paylasilir.
    """
    job_id = str(uuid.uuid4())
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    other_worker_store = CancellationStore(client, ttl_seconds=30)

    store.mark_cancelled(job_id, reason="JOB_SUPERSEDED")

    assert other_worker_store.is_cancelled(job_id) is True
