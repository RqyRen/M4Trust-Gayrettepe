"""Contract violation / operasyonel sayac testleri (Berke review #12).

_counters modul-seviyesi global oldugu ve worker.py gercek kod yollarinda
artirildigi icin (diger test dosyalari da bu sayaclari dolayli olarak
artirabilir), testler mutlak deger yerine ONCESI/SONRASI delta karsilastirir.
"""
from __future__ import annotations

import pytest

from app.common import metrics


def test_increment_increases_by_one_by_default() -> None:
    before = metrics.snapshot()["dead_letter_count"]
    metrics.increment("dead_letter_count")
    after = metrics.snapshot()["dead_letter_count"]
    assert after == before + 1


def test_increment_supports_custom_amount() -> None:
    before = metrics.snapshot()["provider_retry_count"]
    metrics.increment("provider_retry_count", amount=3)
    after = metrics.snapshot()["provider_retry_count"]
    assert after == before + 3


def test_unknown_metric_name_raises() -> None:
    with pytest.raises(KeyError):
        metrics.increment("not_a_real_metric")


def test_snapshot_returns_a_copy_not_the_live_dict() -> None:
    snap = metrics.snapshot()
    snap["dead_letter_count"] = 999999
    assert metrics.snapshot()["dead_letter_count"] != 999999


def test_all_berke_review_12_metrics_are_tracked() -> None:
    # Berke review #12'nin onerdigi 8 metrigin hepsi tanimli olmali.
    expected = {
        "contract_violation_count",
        "unsupported_schema_version_count",
        "job_identity_conflict_count",
        "dead_letter_count",
        "cancellation_requested_count",
        "cancellation_completed_count",
        "lease_reclaim_count",
        "terminal_republish_count",
        "provider_retry_count",
    }
    assert expected <= set(metrics.snapshot())
