"""Lease heartbeat testleri (ADR-002 §17.1) — bulgu, 17 Temmuz 2026."""
from __future__ import annotations

import threading
import time

from app.common.heartbeat import run_with_heartbeat


def test_renew_lease_is_called_periodically_during_long_operation() -> None:
    calls = []
    lock = threading.Lock()

    def _renew() -> bool:
        with lock:
            calls.append(time.monotonic())
        return True

    def _slow_operation() -> str:
        time.sleep(0.35)
        return "done"

    result = run_with_heartbeat(_slow_operation, renew_lease=_renew, interval_seconds=0.1)

    assert result == "done"
    assert len(calls) >= 2  # ~0.35s / 0.1s araligi -> en az birkac heartbeat


def test_heartbeat_thread_does_not_hang_when_operation_is_fast() -> None:
    calls = []

    def _renew() -> bool:
        calls.append(1)
        return True

    started = time.monotonic()
    result = run_with_heartbeat(lambda: "fast", renew_lease=_renew, interval_seconds=5.0)
    elapsed = time.monotonic() - started

    assert result == "fast"
    assert elapsed < 2.0  # thread.join(timeout=1.0) sayesinde takilip kalmaz
    assert calls == []  # islem interval'dan cok kisa surdu, hic heartbeat olmadi


def test_heartbeat_stops_quietly_when_lease_lost() -> None:
    calls = []

    def _renew() -> bool:
        calls.append(1)
        return False  # lease kaybedildi

    def _slow_operation() -> str:
        time.sleep(0.3)
        return "done"

    result = run_with_heartbeat(_slow_operation, renew_lease=_renew, interval_seconds=0.1)

    assert result == "done"  # operation yine de tamamlanir
    assert len(calls) == 1  # ilk renew False donunce heartbeat thread'i durur, tekrar denemez
