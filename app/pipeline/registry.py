"""jobType -> pipeline yonlendirmesi.

Her job turu, request envelope'unu alip canonical completed event dondurur.
Sozlesme ayni kaldigi surece implementasyon serbestce degisebilir (ADR-002 §26).
"""
from __future__ import annotations

from typing import Callable

from app.pipeline.document_extraction import pipeline as document_extraction
from app.pipeline.video_analysis import pipeline as video_analysis

PIPELINES: dict[str, Callable[..., dict]] = {
    "DOCUMENT_EXTRACTION": document_extraction.run,
    "VIDEO_ANALYSIS": video_analysis.run,
}


def pipeline_for(job_type: str) -> Callable[..., dict]:
    if job_type not in PIPELINES:
        raise ValueError(f"no pipeline registered for jobType {job_type!r}")
    return PIPELINES[job_type]
