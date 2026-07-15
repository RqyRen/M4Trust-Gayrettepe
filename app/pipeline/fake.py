"""Fake (mock) pipeline — dev-only (ADR-004 §12-13).

Gercek LLM/OCR/RAG/video modeli CALISTIRMAZ. Gelen request envelope'undan,
canonical success-result fixture payload'ini kullanarak schema-valid bir
completed event uretir. Amac: RabbitMQ + contract + messaging sinirini gercek
calistirmak. Gercek pipeline Adim 6-8'de bu modulun yerini alacak.

Scenario secim mekanizmasi production event contract'ina alan olarak
EKLENMEZ (ADR-004 §13).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

_EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples"

# jobType -> canonical success-result fixture (payload kaynagi).
_RESULT_FIXTURES: dict[str, Path] = {
    "DOCUMENT_EXTRACTION": _EXAMPLES / "document-extraction" / "success-result.json",
    "VIDEO_ANALYSIS": _EXAMPLES / "video-analysis" / "success-result.json",
}


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_completed_event(request: dict) -> dict:
    """Request envelope'undan canonical `ai.job.completed.v1` event'i uretir."""
    job_type = request["jobType"]
    fixture_path = _RESULT_FIXTURES.get(job_type)
    if fixture_path is None:
        raise ValueError(f"fake pipeline: unsupported jobType {job_type!r}")

    payload = json.loads(fixture_path.read_text(encoding="utf-8"))["payload"]

    return {
        "eventId": str(uuid.uuid4()),
        "eventType": "ai.job.completed.v1",
        "schemaVersion": "1.0.0",
        "occurredAt": _utc_now_z(),
        "correlationId": request["correlationId"],
        "causationId": request["eventId"],  # bu event'i doguran command
        "jobId": request["jobId"],
        "jobType": job_type,
        "tenantId": request["tenantId"],
        "transactionId": request["transactionId"],
        "subjectId": request["subjectId"],
        "idempotencyKey": f"result:{request['jobId']}",
        "producer": {"service": "m4trust-ai-worker", "version": get_settings().service_version},
        "payload": payload,
    }
