"""Pipeline calisirken lease heartbeat (ADR-002 §17.1).

Bulgu (17 Temmuz 2026, Berke review #3): lease hic yenilenmiyordu. Uzun suren
pipeline adimlari (buyuk OCR+LLM, video) lease_seconds'i asarsa, gercekten
calismakta olan bir worker'in job'i yanlislikla "coktu" sayilip baskasina
devredilebiliyordu. Bu modul, pipeline calisirken arka planda periyodik
olarak lease'i yeniler; pipeline'in kendisi (document_extraction/video_analysis)
hic degistirilmez.
"""
from __future__ import annotations

import threading
from typing import Callable, TypeVar

T = TypeVar("T")


def run_with_heartbeat(
    operation: Callable[[], T],
    *,
    renew_lease: Callable[[], bool],
    interval_seconds: float,
) -> T:
    """`operation()`'i calistirirken arka planda periyodik `renew_lease()` cagirir.

    `renew_lease()` False donerse (lease baskasina reclaim edilmis) heartbeat
    sessizce durur; `operation()` yine de tamamlanana kadar calisir, ama
    cagiran taraf (worker.py) sonucu commit etmeye calistiginda zaten
    reddedilecektir -- boylece hesaplama yarida kesilmez, yalniz sonuc
    guvenli sekilde atilir.
    """
    stop_event = threading.Event()

    def _heartbeat() -> None:
        while not stop_event.wait(interval_seconds):
            if not renew_lease():
                return

    thread = threading.Thread(target=_heartbeat, daemon=True)
    thread.start()
    try:
        return operation()
    finally:
        stop_event.set()
        thread.join(timeout=1.0)
