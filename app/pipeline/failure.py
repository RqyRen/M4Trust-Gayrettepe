"""Terminal failure event uretimi (ADR-002 §12).

FastAPI teknik hatalari stable error contract'ina cevirir. Bu event Spring
tarafinda BUSINESS REJECTION olarak yorumlanmaz (ADR-001 §14.1); yalnizca
ilgili AI job'in teknik olarak tamamlanamadigini bildirir.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.contracts.errors import PipelineFailure, retry_recommended_for

_PIPELINE_VERSION = {
    "DOCUMENT_EXTRACTION": "doc-pipeline-1.0.0",
    "VIDEO_ANALYSIS": "video-pipeline-1.0.0",
}


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_failed_event(
    request: dict,
    failure: PipelineFailure,
    *,
    max_attempts: int,
    duration_ms: int = 0,
) -> dict:
    """Request envelope + PipelineFailure'dan canonical `ai.job.failed.v1` uretir."""
    job_type = request["jobType"]
    return {
        "eventId": str(uuid.uuid4()),
        "eventType": "ai.job.failed.v1",
        "schemaVersion": "1.0.0",
        "occurredAt": _utc_now_z(),
        "correlationId": request["correlationId"],
        "causationId": request["eventId"],
        "jobId": request["jobId"],
        "jobType": job_type,
        "tenantId": request["tenantId"],
        "transactionId": request["transactionId"],
        "subjectId": request["subjectId"],
        "idempotencyKey": f"failure:{request['jobId']}",
        "producer": {"service": "m4trust-ai-worker", "version": get_settings().service_version},
        "payload": {
            "error": {
                "category": failure.category.value,
                "code": failure.code.value,
                "message": failure.message,
                # Kategori <-> retry baglantisi contract'ta kapali policy (§12.1).
                "retryRecommended": retry_recommended_for(failure.code),
                "details": failure.details,
            },
            "attempt": {
                "attemptNumber": failure.attempt_number,
                "maxAttempts": max_attempts,
            },
            "technicalMetadata": {
                "pipelineVersion": _PIPELINE_VERSION.get(job_type, "unknown-pipeline-1.0.0"),
                "durationMs": duration_ms,
            },
        },
    }
