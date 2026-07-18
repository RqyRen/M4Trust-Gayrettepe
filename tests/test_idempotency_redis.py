"""RedisJobStore testleri (ADR-002 §17.1) — coklu worker replica'sinda paylasimli kayit.

Gercek local Redis uzerinden calisir (docker-compose: `m4trust-redis`), ayni
RabbitMQ/MinIO testlerindeki gibi (ucretsiz/local altyapi gercek calistirilir).
Redis erisilemezse test modulu atlanir.
"""
from __future__ import annotations

import threading
import time
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
    yield RedisJobStore(client, ttl_seconds=30, lease_seconds=30)
    for key in client.scan_iter("m4trust:idempotency:*"):
        client.delete(key)


@pytest.fixture
def short_lease_store() -> RedisJobStore:
    """lease_seconds cok kisa -- crash/reclaim senaryosunu gercek zamanla test eder."""
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    yield RedisJobStore(client, ttl_seconds=30, lease_seconds=1)
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


# --- Lease-based crash recovery (bulgu, 16 Temmuz 2026) ---

def test_expired_lease_is_reclaimed_by_another_worker(short_lease_store: RedisJobStore) -> None:
    """Worker crash simulasyonu: sahibi bir daha hic donmuyor, lease dolunca baskasi devralir."""
    identity = _identity()
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    crashed_worker = short_lease_store  # lease_seconds=1
    new_worker = RedisJobStore(client, ttl_seconds=30, lease_seconds=30)

    assert crashed_worker.resolve(identity).resolution is Resolution.NEW
    # Coken worker hic donmuyor (mark_terminal/forget cagirmiyor).
    time.sleep(1.2)  # lease suresi dolsun

    result = new_worker.resolve(identity)
    assert result.resolution is Resolution.NEW  # reclaim edildi, yeniden calistirilabilir


def test_reclaimed_job_owner_cannot_overwrite_new_owners_result(short_lease_store: RedisJobStore) -> None:
    """Eski (reclaim edilmis) sahip gec donup terminal yazmaya calisirsa reddedilir."""
    identity = _identity()
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    old_owner = short_lease_store  # lease_seconds=1
    new_owner = RedisJobStore(client, ttl_seconds=30, lease_seconds=30)

    assert old_owner.resolve(identity).resolution is Resolution.NEW
    time.sleep(1.2)
    assert new_owner.resolve(identity).resolution is Resolution.NEW  # reclaim

    new_result = {"eventType": "ai.job.completed.v1", "jobId": identity.job_id, "source": "new_owner"}
    assert new_owner.mark_terminal(identity, new_result) is True

    # Eski sahip artik cok gec donuyor -- yazmasi SESSIZCE REDDEDILMELI, ezmemeli.
    stale_result = {"eventType": "ai.job.completed.v1", "jobId": identity.job_id, "source": "zombie_old_owner"}
    assert old_owner.mark_terminal(identity, stale_result) is False

    final = new_owner.resolve(identity)
    assert final.resolution is Resolution.TERMINAL
    assert final.terminal_event == new_result  # zombie yazma hicbir sey degistirmedi


def test_active_lease_is_not_reclaimed(store: RedisJobStore) -> None:
    """Lease hala gecerliyse (worker aktif calisiyor) baskasi devralamaz."""
    identity = _identity()
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    active_worker = store  # lease_seconds=30, henuz dolmadi
    other_worker = RedisJobStore(client, ttl_seconds=30, lease_seconds=30)

    assert active_worker.resolve(identity).resolution is Resolution.NEW
    assert other_worker.resolve(identity).resolution is Resolution.IN_PROGRESS  # reclaim YOK


# --- PENDING_PUBLISH: publish-oncesi durable commit (bulgu, 17 Temmuz 2026) ---

def test_commit_pending_publish_requires_current_ownership(store: RedisJobStore) -> None:
    identity = _identity()
    store.resolve(identity)
    event = {"eventType": "ai.job.completed.v1", "jobId": identity.job_id}
    assert store.commit_pending_publish(identity, event) is True


def test_commit_pending_publish_fails_for_non_owner(store: RedisJobStore) -> None:
    identity = _identity()
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    owner = store
    impostor = RedisJobStore(client, ttl_seconds=30, lease_seconds=30)

    assert owner.resolve(identity).resolution is Resolution.NEW
    event = {"eventType": "ai.job.completed.v1", "jobId": identity.job_id}
    assert impostor.commit_pending_publish(identity, event) is False


def test_crash_between_publish_and_mark_terminal_does_not_recompute(store: RedisJobStore) -> None:
    """Berke review #2: publish confirm edildikten sonra ama mark_terminal'dan
    ONCE coken bir worker'da, redelivery pipeline'i BASTAN calistirmamali --
    ayni (zaten hesaplanmis) event'i yeniden publish etmeyi denemeli.
    """
    identity = _identity()
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)

    assert store.resolve(identity).resolution is Resolution.NEW
    computed_event = {"eventType": "ai.job.completed.v1", "jobId": identity.job_id, "result": "unique-llm-output"}
    assert store.commit_pending_publish(identity, computed_event) is True
    # Worker burada "coker" -- mark_terminal() hic cagrilmadan.

    # Redelivery: baska bir worker (ya da ayni worker restart) resolve eder.
    recovering_worker = RedisJobStore(client, ttl_seconds=30, lease_seconds=30)
    result = recovering_worker.resolve(identity)

    assert result.resolution is Resolution.PENDING_PUBLISH
    assert result.terminal_event == computed_event  # AYNI event -- pipeline tekrar calismadi

    assert recovering_worker.mark_terminal(identity, computed_event) is True
    final = recovering_worker.resolve(identity)
    assert final.resolution is Resolution.TERMINAL
    assert final.terminal_event == computed_event


def test_pending_publish_is_reclaimed_after_lease_expiry(short_lease_store: RedisJobStore) -> None:
    identity = _identity()
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)

    assert short_lease_store.resolve(identity).resolution is Resolution.NEW
    computed_event = {"eventType": "ai.job.completed.v1", "jobId": identity.job_id}
    assert short_lease_store.commit_pending_publish(identity, computed_event) is True
    time.sleep(1.2)  # lease_seconds=1 dolsun

    new_worker = RedisJobStore(client, ttl_seconds=30, lease_seconds=30)
    result = new_worker.resolve(identity)
    assert result.resolution is Resolution.PENDING_PUBLISH
    assert result.terminal_event == computed_event


# --- Heartbeat: renew_lease (bulgu, 17 Temmuz 2026) ---

def test_renew_lease_extends_lease_while_still_owner(short_lease_store: RedisJobStore) -> None:
    """Heartbeat simulasyonu: 1sn'lik lease'i ust uste yenileyerek, tek basina
    coktan dolmus olacak bir lease'i uzun sureli aktif tutar."""
    identity = _identity()
    assert short_lease_store.resolve(identity).resolution is Resolution.NEW

    for _ in range(4):
        time.sleep(0.4)  # 4 x 0.4s = 1.6s, tek seferlik 1s lease'i coktan asar
        assert short_lease_store.renew_lease(identity) is True

    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    other_worker = RedisJobStore(client, ttl_seconds=30, lease_seconds=30)
    # Heartbeat sayesinde lease surekli tazelendi -- baskasi hala devralamaz.
    assert other_worker.resolve(identity).resolution is Resolution.IN_PROGRESS


def test_renew_lease_fails_after_reclaim(short_lease_store: RedisJobStore) -> None:
    identity = _identity()
    client = redis_lib.Redis(host=_REDIS_HOST, port=_REDIS_PORT, decode_responses=True)
    old_owner = short_lease_store  # lease_seconds=1
    new_owner = RedisJobStore(client, ttl_seconds=30, lease_seconds=30)

    assert old_owner.resolve(identity).resolution is Resolution.NEW
    time.sleep(1.2)
    assert new_owner.resolve(identity).resolution is Resolution.NEW  # reclaim

    # Eski sahip artik lease'ini yenileyemez -- kaybettigini fark edebilir.
    assert old_owner.renew_lease(identity) is False
