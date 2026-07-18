"""Best-effort cooperative cancellation (ADR-002 §20; Berke review #1).

`ai.job.cancel.requested.v1` sadece bir INTENT'tir; hicbir contract'ta
`cancelled` sonuc event'i tanimli degildir (contracts/schemas). Bu yuzden:

- Henuz claim edilmemis (PROCESSING'e gecmemis) bir job icin cancel intent'i
  gorulurse pipeline hic calistirilmaz, hicbir sonuc event'i yayinlanmaz --
  Spring cancel'i zaten kendisi istedi, bir terminal event beklemiyor.
- Pipeline bir checkpoint'i (indirme, provider cagrisi) GECTIKTEN SONRA biten
  is normal sekilde publish edilir; iptal isi geriye almaz. Gec tamamlanan
  sonucu kabul edip etmeme karari Spring'e birakilir (Berke review #1).

Cancel intent, idempotency kaydindan AYRI bir Redis anahtar alaninda tutulur:
cancel mesaji, ilgili job'in requested mesajindan ONCE gelebilir, bu yuzden
idempotency store'daki bir kayda bagimli olamaz.
"""
from __future__ import annotations

import redis

_KEY_PREFIX = "m4trust:cancellation:"


class JobCancelled(Exception):
    """Bir pipeline checkpoint'inde cancellation intent'i gorulunce firlatilir."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job {job_id} cancelled")
        self.job_id = job_id


class CancellationStore:
    """Paylasimli Redis tabanli cancellation-intent kaydi."""

    def __init__(self, client: "redis.Redis", *, ttl_seconds: int) -> None:
        self._redis = client
        self._ttl_seconds = ttl_seconds

    def _key(self, job_id: str) -> str:
        return f"{_KEY_PREFIX}{job_id}"

    def mark_cancelled(self, job_id: str, *, reason: str) -> None:
        self._redis.set(self._key(job_id), reason, ex=self._ttl_seconds)

    def is_cancelled(self, job_id: str) -> bool:
        return self._redis.exists(self._key(job_id)) == 1

    def check(self, job_id: str) -> None:
        """Checkpoint helper: cancel edilmisse `JobCancelled` firlatir."""
        if self.is_cancelled(job_id):
            raise JobCancelled(job_id)


def build_cancellation_store(settings=None) -> CancellationStore:
    """Config'ten `CancellationStore` insa eder."""
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
    return CancellationStore(client, ttl_seconds=settings.idempotency_ttl_seconds)
