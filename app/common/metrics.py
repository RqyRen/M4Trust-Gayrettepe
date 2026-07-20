"""Contract violation / operasyonel sayaclar (Berke review #12).

Thread-safe, tamamen in-process (ADR-007 §15 minimum dependency -- Prometheus
client gibi bir kutuphane eklenmedi). Worker process'inin omru boyunca
birikir, restart'ta sifirlanir; kalici bir zaman serisi degildir. Her artis
ayni zamanda yapilandirilmis bir log satiri da uretir (ADR-007 §32-33), boylece
disaridaki bir log-tabanli aggregator (varsa) da bu sayaclari gorebilir --
ayri bir /metrics endpoint'i disinda ek bir entegrasyon gerektirmez.

Sayac isimleri Berke review #12'de onerilen metriklerle birebir eslesir.

`ai_*` on-ekli sayaclar ayri bir kaynaktan geliyor: ADR-002 SS30'un onerdigi
temel job-hacmi metrikleri (istek/basari/hata sayisi, sure, duplicate, gec
sonuc). Berke review #12'nin sayaclariyla KASITLI OLARAK birlestirilmedi --
ikisi farkli seyi olcuyor (review #12: guvenilirlik/hata-yolu olaylari,
ADR-002 SS30: genel is hacmi) ve isim celismesi/anlam karisikligi riski var.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger("ai-worker.metrics")

_lock = threading.Lock()
_counters: dict[str, float] = {
    "contract_violation_count": 0,
    "unsupported_schema_version_count": 0,
    "job_identity_conflict_count": 0,
    "dead_letter_count": 0,
    "cancellation_requested_count": 0,
    "cancellation_completed_count": 0,
    "lease_reclaim_count": 0,
    "terminal_republish_count": 0,
    "provider_retry_count": 0,
    # ADR-002 SS30 onerilen temel metrikler.
    "ai_jobs_requested_total": 0,
    "ai_jobs_completed_total": 0,
    "ai_jobs_failed_total": 0,
    "ai_job_duration_seconds_sum": 0.0,
    "ai_job_duration_seconds_count": 0,
    "ai_job_duplicate_total": 0,
    "ai_late_result_total": 0,
}


def increment(name: str, *, amount: float = 1) -> None:
    """Bilinen bir sayaci artirir. Bilinmeyen isim programlama hatasidir (KeyError)."""
    if name not in _counters:
        raise KeyError(f"unknown metric: {name!r}")
    with _lock:
        _counters[name] += amount
        value = _counters[name]
    logger.info("metric incremented", extra={"metric": name, "metricValue": value})


def snapshot() -> dict[str, float]:
    """Tum sayaclarin o anki degerlerinin kopyasi (mutasyona kapali)."""
    with _lock:
        return dict(_counters)
