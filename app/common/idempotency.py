"""Job identity ve duplicate-safe islem (ADR-002 §13, §17.1).

At-least-once delivery varsayilir; ayni job birden fazla kez gelebilir.
Job identity: jobId + jobType + input.sha256.

Ayni kombinasyon tekrar gelirse:
- devam eden is yeniden BASLATILMAZ,
- tamamlanan is tekrar CALISTIRILMAZ; onceki terminal sonuc yeniden yayinlanir,
- ayni jobId farkli input hash ile gelirse bu bir CONFLICT'tir (contract violation).

Iki implementasyon vardir, ayni arayuzu (resolve/mark_terminal/forget) paylasir
(ADR-001 §4.2 - kalici storage'a gecince arayuz korunur):
- `JobStore`: in-memory, process-local. Tek worker/testler icin yeterli, ama
  coklu worker replica'sinda her worker kendi hafizasinda ayri kayit tutar ve
  duplicate/terminal tespiti replica'lar arasinda KIRILIR.
- `RedisJobStore`: paylasimli, kalici. Coklu replica'da tekilligi Redis'in
  atomik `SET NX` komutuyla garanti eder (bkz. asagida).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from enum import Enum

import redis


@dataclass(frozen=True)
class JobIdentity:
    job_id: str
    job_type: str
    input_sha256: str


class Resolution(str, Enum):
    NEW = "NEW"                  # ilk kez goruldu -> calistir
    IN_PROGRESS = "IN_PROGRESS"  # ayni is zaten calisiyor -> yeniden baslatma
    TERMINAL = "TERMINAL"        # terminal sonuc var -> yeniden yayinla
    CONFLICT = "CONFLICT"        # ayni jobId, farkli input hash -> contract violation


@dataclass(frozen=True)
class ResolveResult:
    resolution: Resolution
    terminal_event: dict | None = None


def identity_of(envelope: dict) -> JobIdentity:
    """Requested event'ten job identity uretir."""
    return JobIdentity(
        job_id=envelope["jobId"],
        job_type=envelope["jobType"],
        input_sha256=envelope["payload"]["input"]["sha256"],
    )


@dataclass
class _Entry:
    identity: JobIdentity
    terminal_event: dict | None = None


class JobStore:
    """In-memory, duplicate-safe job kaydi."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    def resolve(self, identity: JobIdentity) -> ResolveResult:
        with self._lock:
            entry = self._entries.get(identity.job_id)
            if entry is None:
                self._entries[identity.job_id] = _Entry(identity=identity)
                return ResolveResult(Resolution.NEW)

            if entry.identity != identity:
                # Ayni jobId ile farkli input hash contract violation'dir (§17.1).
                return ResolveResult(Resolution.CONFLICT)

            if entry.terminal_event is not None:
                return ResolveResult(Resolution.TERMINAL, entry.terminal_event)

            return ResolveResult(Resolution.IN_PROGRESS)

    def mark_terminal(self, identity: JobIdentity, event: dict) -> None:
        """Terminal sonucu (completed veya failed) kaydeder."""
        with self._lock:
            self._entries[identity.job_id] = _Entry(identity=identity, terminal_event=event)

    def forget(self, identity: JobIdentity) -> None:
        """Basarisiz baslangicta kaydi geri alir (yeni denemeye izin verir)."""
        with self._lock:
            entry = self._entries.get(identity.job_id)
            if entry is not None and entry.terminal_event is None:
                del self._entries[identity.job_id]


_KEY_PREFIX = "m4trust:idempotency:"


class RedisJobStore:
    """Paylasimli, kalici job kaydi (coklu worker replica'si icin).

    Tekillik garantisi Redis'in atomik `SET key value NX` komutuna dayanir:
    ayni jobId icin birden fazla worker ayni anda `resolve()` cagirsa bile
    yalnizca biri "olustur" islemini kazanir, digerleri kazananin yazdigi
    kaydi okur. Bu, in-memory `JobStore`'un process-local kilidinin process
    sinirlarini asamamasi sorununu cozer.
    """

    def __init__(self, client: "redis.Redis", *, ttl_seconds: int) -> None:
        self._redis = client
        self._ttl_seconds = ttl_seconds

    def _key(self, job_id: str) -> str:
        return f"{_KEY_PREFIX}{job_id}"

    @staticmethod
    def _encode(identity: JobIdentity, *, terminal_event: dict | None) -> str:
        return json.dumps(
            {
                "jobType": identity.job_type,
                "inputSha256": identity.input_sha256,
                "terminalEvent": terminal_event,
            }
        )

    def resolve(self, identity: JobIdentity) -> ResolveResult:
        key = self._key(identity.job_id)
        fresh_payload = self._encode(identity, terminal_event=None)

        if self._redis.set(key, fresh_payload, nx=True, ex=self._ttl_seconds):
            return ResolveResult(Resolution.NEW)

        raw = self._redis.get(key)
        if raw is None:
            # TTL suresi dolan kayit ile SET NX basarisizligi arasinda cok
            # kisa bir pencerede kayboldu; guvenle yeni is olarak ele alinir.
            if self._redis.set(key, fresh_payload, nx=True, ex=self._ttl_seconds):
                return ResolveResult(Resolution.NEW)
            raw = self._redis.get(key)

        entry = json.loads(raw)
        if entry["jobType"] != identity.job_type or entry["inputSha256"] != identity.input_sha256:
            # Ayni jobId ile farkli input hash contract violation'dir (§17.1).
            return ResolveResult(Resolution.CONFLICT)

        if entry["terminalEvent"] is not None:
            return ResolveResult(Resolution.TERMINAL, entry["terminalEvent"])

        return ResolveResult(Resolution.IN_PROGRESS)

    def mark_terminal(self, identity: JobIdentity, event: dict) -> None:
        """Terminal sonucu (completed veya failed) kaydeder."""
        key = self._key(identity.job_id)
        payload = self._encode(identity, terminal_event=event)
        self._redis.set(key, payload, ex=self._ttl_seconds)

    def forget(self, identity: JobIdentity) -> None:
        """Basarisiz baslangicta kaydi geri alir (yeni denemeye izin verir).

        Terminal kayit varsa SILINMEZ (baska bir worker arada tamamlamis
        olabilir) — WATCH/MULTI ile okuma-ve-silme atomik yapilir.
        """
        key = self._key(identity.job_id)
        with self._redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is not None and json.loads(raw)["terminalEvent"] is None:
                    pipe.multi()
                    pipe.delete(key)
                    pipe.execute()
                else:
                    pipe.unwatch()
            except redis.WatchError:
                # Kayit arada degisti (ör. baska worker tamamladi); silme atlanir.
                pass


def build_job_store(settings=None) -> RedisJobStore:
    """Config'ten `RedisJobStore` insa eder (ADR-002 §17.1 - paylasimli kayit)."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    client = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password or None,
        decode_responses=True,
    )
    return RedisJobStore(client, ttl_seconds=settings.idempotency_ttl_seconds)
