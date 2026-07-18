"""Job identity ve duplicate-safe islem (ADR-002 §13, §17.1).

At-least-once delivery varsayilir; ayni job birden fazla kez gelebilir.
Job identity: jobId + jobType + input.sha256.

Ayni kombinasyon tekrar gelirse:
- devam eden is yeniden BASLATILMAZ,
- tamamlanan is tekrar CALISTIRILMAZ; onceki terminal sonuc yeniden yayinlanir,
- ayni jobId farkli input hash ile gelirse bu bir CONFLICT'tir (contract violation).

Iki implementasyon vardir, ayni arayuzu (resolve/mark_terminal/forget) paylasir
(ADR-001 §4.2 - kalici storage'a gecince arayuz korunur):
- `JobStore`: in-memory, process-local. Tek worker/testler icin yeterli;
  crash-recovery kavrami yok (process cokerse zaten butun state'i beraber gider).
- `RedisJobStore`: paylasimli, kalici, LEASE tabanli. Coklu replica'da
  tekilligi Redis'in atomik `SET NX` komutuyla garanti eder. Bir worker job'i
  "PROCESSING" olarak claim ederken kendi ownerId'sini ve bir leaseUntil
  suresi yazar; worker o job'i bitirmeden coker/kaybolursa, lease suresi
  dolunca BASKA bir worker devralabilir (bulgu, 16 Temmuz 2026: eskiden bu
  yoktu, coken worker'in job'i sonsuza kadar IN_PROGRESS kilitli kalirdi).
  Eski (artik reclaim edilmis) sahibin gec donup terminal sonucu ezmesi,
  yalniz mevcut ownerId sahibinin terminal yazabilmesiyle engellenir.

Uc durumlu state machine (bulgu, 17 Temmuz 2026 - Berke review #2/#3):
`PROCESSING -> PENDING_PUBLISH -> TERMINAL`. Eskiden akis "publish et, sonra
Redis'e terminal yaz" seklindeydi; broker confirm ettikten SONRA ama Redis
yazilmadan ONCE coken bir worker'da command redelivery pipeline'i BASTAN
calistirabiliyordu -- LLM deterministik olmadigi icin ayni jobId'ye iki farkli
completed event uretilebiliyordu. Artik hesaplanan event, publish'ten ONCE
`commit_pending_publish()` ile durable yazilir (yalniz lease sahibiyken).
Redelivery `PENDING_PUBLISH` durumunu gorurse pipeline'i TEKRAR CALISTIRMAZ;
zaten hesaplanmis event'i yeniden publish etmeyi dener. Ayrica `renew_lease()`
ile worker uzun suren pipeline adimlari sirasinda lease'ini yeniler (heartbeat) --
gercek islem suresi lease_seconds'i asarsa bile job baskasina kacmaz.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum

import redis


@dataclass(frozen=True)
class JobIdentity:
    job_id: str
    job_type: str
    input_sha256: str


class Resolution(str, Enum):
    NEW = "NEW"                        # ilk kez goruldu (veya reclaim edildi) -> calistir
    IN_PROGRESS = "IN_PROGRESS"        # ayni is zaten calisiyor -> yeniden baslatma
    PENDING_PUBLISH = "PENDING_PUBLISH"  # sonuc hesaplandi ama henuz confirm edilmedi -> YENIDEN CALISTIRMADAN yayinla
    TERMINAL = "TERMINAL"              # terminal sonuc var -> yeniden yayinla
    CONFLICT = "CONFLICT"              # ayni jobId, farkli input hash -> contract violation


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

    def mark_terminal(self, identity: JobIdentity, event: dict) -> bool:
        """Terminal sonucu (completed veya failed) kaydeder. Her zaman True doner
        (process-local'de sahiplik/reclaim kavrami yok -- bkz. RedisJobStore)."""
        with self._lock:
            self._entries[identity.job_id] = _Entry(identity=identity, terminal_event=event)
            return True

    def forget(self, identity: JobIdentity) -> None:
        """Basarisiz baslangicta kaydi geri alir (yeni denemeye izin verir)."""
        with self._lock:
            entry = self._entries.get(identity.job_id)
            if entry is not None and entry.terminal_event is None:
                del self._entries[identity.job_id]

    def commit_pending_publish(self, identity: JobIdentity, event: dict) -> bool:
        """Process-local'de PENDING_PUBLISH ayri bir durum degildir; dogrudan
        terminal yazar ve her zaman True doner (crash-recovery kavrami yok)."""
        return self.mark_terminal(identity, event)

    def renew_lease(self, identity: JobIdentity) -> bool:
        """Process-local'de lease kavrami yok; kayit varsa her zaman True doner."""
        with self._lock:
            return identity.job_id in self._entries


_KEY_PREFIX = "m4trust:idempotency:"


class RedisJobStore:
    """Paylasimli, kalici, lease tabanli job kaydi (coklu worker replica'si icin).

    Tekillik garantisi Redis'in atomik `SET key value NX` komutuna dayanir.
    Devam eden bir job'in kaydi `state=PROCESSING`, bu store'un kendine ozel
    `ownerId`'si ve bir `leaseUntil` zaman damgasi tasir. Lease suresi dolmus
    bir PROCESSING kaydi baska bir worker tarafindan devralinabilir (reclaim).
    Terminal yazma ve `forget()` YALNIZ mevcut ownerId sahibi tarafindan
    yapilabilir -- coken/yavaslayip lease'i kaybetmis eski bir worker'in gec
    donup taze sonucu ezmesi boylece engellenir.
    """

    def __init__(self, client: "redis.Redis", *, ttl_seconds: int, lease_seconds: int) -> None:
        self._redis = client
        self._ttl_seconds = ttl_seconds
        self._lease_seconds = lease_seconds
        # Bu process'e/store instance'ina ozel kimlik; ayni worker'in farkli
        # baslatmalari (restart) bile farkli owner sayilir.
        self._owner_id = uuid.uuid4().hex

    def _key(self, job_id: str) -> str:
        return f"{_KEY_PREFIX}{job_id}"

    def _encode_processing(self, identity: JobIdentity, *, lease_until: float, attempt: int) -> str:
        return json.dumps(
            {
                "jobType": identity.job_type,
                "inputSha256": identity.input_sha256,
                "state": "PROCESSING",
                "ownerId": self._owner_id,
                "leaseUntil": lease_until,
                "attempt": attempt,
                "pendingEvent": None,
                "terminalEvent": None,
            }
        )

    @staticmethod
    def _encode_terminal(identity: JobIdentity, *, event: dict) -> str:
        return json.dumps(
            {
                "jobType": identity.job_type,
                "inputSha256": identity.input_sha256,
                "state": "TERMINAL",
                "ownerId": None,
                "leaseUntil": None,
                "attempt": None,
                "pendingEvent": None,
                "terminalEvent": event,
            }
        )

    def resolve(self, identity: JobIdentity) -> ResolveResult:
        key = self._key(identity.job_id)
        now = time.time()
        fresh_payload = self._encode_processing(identity, lease_until=now + self._lease_seconds, attempt=1)

        if self._redis.set(key, fresh_payload, nx=True, ex=self._ttl_seconds):
            return ResolveResult(Resolution.NEW)

        with self._redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    raw = pipe.get(key)

                    if raw is None:
                        # TTL suresi dolan kayit ile SET NX basarisizligi arasinda
                        # cok kisa bir pencerede kayboldu; guvenle yeni is.
                        pipe.multi()
                        pipe.set(key, fresh_payload, ex=self._ttl_seconds)
                        pipe.execute()
                        return ResolveResult(Resolution.NEW)

                    entry = json.loads(raw)
                    if entry["jobType"] != identity.job_type or entry["inputSha256"] != identity.input_sha256:
                        # Ayni jobId ile farkli input hash contract violation'dir (§17.1).
                        pipe.unwatch()
                        return ResolveResult(Resolution.CONFLICT)

                    if entry["state"] == "TERMINAL":
                        pipe.unwatch()
                        return ResolveResult(Resolution.TERMINAL, entry["terminalEvent"])

                    if entry["state"] == "PENDING_PUBLISH":
                        # Sonuc zaten hesaplanmis ve durable yazilmis (bulgu,
                        # 17 Temmuz 2026) -- pipeline'i TEKRAR CALISTIRMA, ayni
                        # event'i devral ve yeniden publish etmeyi dene. Lease
                        # suresini BEKLEMEYIZ: bir zaten-hesaplanmis event'i
                        # yeniden yayinlamaya calismak her zaman guvenlidir
                        # (mark_terminal hala sahiplik kontrolu yapar), PROCESSING
                        # durumunun aksine (orada gercek yeniden hesaplama olur).
                        reclaim_payload = json.dumps(
                            {**entry, "ownerId": self._owner_id, "leaseUntil": now + self._lease_seconds,
                             "attempt": (entry.get("attempt") or 0) + 1}
                        )
                        pipe.multi()
                        pipe.set(key, reclaim_payload, ex=self._ttl_seconds)
                        pipe.execute()
                        return ResolveResult(Resolution.PENDING_PUBLISH, entry["pendingEvent"])

                    if entry["leaseUntil"] is not None and entry["leaseUntil"] > now:
                        # Baska bir worker aktif olarak isliyor, lease hala gecerli.
                        pipe.unwatch()
                        return ResolveResult(Resolution.IN_PROGRESS)

                    # Lease suresi dolmus (worker cokmus/kaybolmus olabilir) -> reclaim.
                    reclaim_payload = self._encode_processing(
                        identity, lease_until=now + self._lease_seconds, attempt=(entry.get("attempt") or 0) + 1
                    )
                    pipe.multi()
                    pipe.set(key, reclaim_payload, ex=self._ttl_seconds)
                    pipe.execute()
                    return ResolveResult(Resolution.NEW)
                except redis.WatchError:
                    # Baska bir worker ayni anda reclaim etti; en guncel kaydi tekrar oku.
                    continue

    def commit_pending_publish(self, identity: JobIdentity, event: dict) -> bool:
        """PROCESSING -> PENDING_PUBLISH gecisi; hesaplanan event'i publish'ten
        ONCE durable yazar (bulgu, 17 Temmuz 2026 - Berke review #2).

        Yalniz mevcut lease sahibiyken basarili olur. Bu, publish denemesinden
        once yapilmasi gereken son adimdir: basarisiz olursa (lease baskasina
        reclaim edilmis) publish HIC yapilmamalidir -- baskasi zaten devralmis
        veya bitirmis demektir.
        """
        key = self._key(identity.job_id)
        now = time.time()
        with self._redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is not None:
                    entry = json.loads(raw)
                    if entry.get("state") == "PROCESSING" and entry.get("ownerId") == self._owner_id:
                        entry["state"] = "PENDING_PUBLISH"
                        entry["pendingEvent"] = event
                        entry["leaseUntil"] = now + self._lease_seconds
                        pipe.multi()
                        pipe.set(key, json.dumps(entry), ex=self._ttl_seconds)
                        pipe.execute()
                        return True
                pipe.unwatch()
                return False
            except redis.WatchError:
                return False

    def renew_lease(self, identity: JobIdentity) -> bool:
        """Lease suresini uzatir (heartbeat). Hala mevcut sahipsem True,
        degilsem (reclaim edilmis veya terminal) False doner -- cagiran
        taraf bunu gorunce isi guvenle durdurabilir.
        """
        key = self._key(identity.job_id)
        now = time.time()
        with self._redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is not None:
                    entry = json.loads(raw)
                    if entry.get("ownerId") == self._owner_id and entry.get("state") in ("PROCESSING", "PENDING_PUBLISH"):
                        entry["leaseUntil"] = now + self._lease_seconds
                        pipe.multi()
                        pipe.set(key, json.dumps(entry), ex=self._ttl_seconds)
                        pipe.execute()
                        return True
                pipe.unwatch()
                return False
            except redis.WatchError:
                return False

    def mark_terminal(self, identity: JobIdentity, event: dict) -> bool:
        """PENDING_PUBLISH -> TERMINAL gecisi; publish confirm edildikten
        SONRA cagrilir.

        Yalniz mevcut lease sahibiyken basarili olur. Doner: kayit gercekten
        yazildiysa True, bu store artik sahip degilse (lease baskasina
        reclaim edilmis) False -- cagiran taraf bunu loglayabilir.
        """
        key = self._key(identity.job_id)
        payload = self._encode_terminal(identity, event=event)
        with self._redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is not None:
                    entry = json.loads(raw)
                    if entry.get("state") in ("PROCESSING", "PENDING_PUBLISH") and entry.get("ownerId") == self._owner_id:
                        pipe.multi()
                        pipe.set(key, payload, ex=self._ttl_seconds)
                        pipe.execute()
                        return True
                pipe.unwatch()
                return False
            except redis.WatchError:
                # Kayit arada degisti (ör. baskasi reclaim etti); zombie yazma engellendi.
                return False

    def forget(self, identity: JobIdentity) -> None:
        """Basarisiz baslangicta kaydi geri alir (yeni denemeye izin verir).

        Yalniz mevcut lease sahibiyken siler -- terminal kayit veya baskasinin
        reclaim ettigi kayit SILINMEZ.
        """
        key = self._key(identity.job_id)
        with self._redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is not None:
                    entry = json.loads(raw)
                    if entry.get("state") == "PROCESSING" and entry.get("ownerId") == self._owner_id:
                        pipe.multi()
                        pipe.delete(key)
                        pipe.execute()
                        return
                pipe.unwatch()
            except redis.WatchError:
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
    return RedisJobStore(client, ttl_seconds=settings.idempotency_ttl_seconds, lease_seconds=settings.idempotency_lease_seconds)
