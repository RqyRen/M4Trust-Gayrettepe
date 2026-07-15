"""jobType -> pipeline yonlendirmesi.

Her job turu, request envelope'unu alip canonical completed event dondurur.
Sozlesme ayni kaldigi surece implementasyon serbestce degisebilir (ADR-002 §26).
"""
from __future__ import annotations

from typing import Callable

from app.pipeline import fake
from app.pipeline.document_extraction import pipeline as document_extraction

# VIDEO_ANALYSIS su an fake; Adim 7'de gercek pipeline ile degisecek.
PIPELINES: dict[str, Callable[[dict], dict]] = {
    "DOCUMENT_EXTRACTION": document_extraction.run,
    "VIDEO_ANALYSIS": fake.build_completed_event,
}


def pipeline_for(job_type: str) -> Callable[[dict], dict]:
    if job_type not in PIPELINES:
        raise ValueError(f"no pipeline registered for jobType {job_type!r}")
    return PIPELINES[job_type]
