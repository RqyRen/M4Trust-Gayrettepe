"""Contract violation / operasyonel sayaclar (Berke review #12).

Thread-safe, tamamen in-process (ADR-007 §15 minimum dependency -- Prometheus
client gibi bir kutuphane eklenmedi). Worker process'inin omru boyunca
birikir, restart'ta sifirlanir; kalici bir zaman serisi degildir. Her artis
ayni zamanda yapilandirilmis bir log satiri da uretir (ADR-007 §32-33), boylece
disaridaki bir log-tabanli aggregator (varsa) da bu sayaclari gorebilir --
ayri bir /metrics endpoint'i disinda ek bir entegrasyon gerektirmez.

Sayac isimleri Berke review #12'de onerilen metriklerle birebir eslesir.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger("ai-worker.metrics")

_lock = threading.Lock()
_counters: dict[str, int] = {
    "contract_violation_count": 0,
    "unsupported_schema_version_count": 0,
    "job_identity_conflict_count": 0,
    "dead_letter_count": 0,
    "cancellation_requested_count": 0,
    "cancellation_completed_count": 0,
    "lease_reclaim_count": 0,
    "terminal_republish_count": 0,
    "provider_retry_count": 0,
}


def increment(name: str, *, amount: int = 1) -> None:
    """Bilinen bir sayaci artirir. Bilinmeyen isim programlama hatasidir (KeyError)."""
    if name not in _counters:
        raise KeyError(f"unknown metric: {name!r}")
    with _lock:
        _counters[name] += amount
        value = _counters[name]
    logger.info("metric incremented", extra={"metric": name, "metricValue": value})


def snapshot() -> dict[str, int]:
    """Tum sayaclarin o anki degerlerinin kopyasi (mutasyona kapali)."""
    with _lock:
        return dict(_counters)
