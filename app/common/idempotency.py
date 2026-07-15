"""Job identity ve duplicate-safe islem (ADR-002 §13, §17.1).

At-least-once delivery varsayilir; ayni job birden fazla kez gelebilir.
Job identity: jobId + jobType + input.sha256.

Ayni kombinasyon tekrar gelirse:
- devam eden is yeniden BASLATILMAZ,
- tamamlanan is tekrar CALISTIRILMAZ; onceki terminal sonuc yeniden yayinlanir,
- ayni jobId farkli input hash ile gelirse bu bir CONFLICT'tir (contract violation).

Store process-local'dir; FastAPI kendi teknik calisma verisinin sahibidir
(ADR-001 §4.2). Kalici teknik storage gerekirse bu arayuz korunarak degistirilir.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum


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
