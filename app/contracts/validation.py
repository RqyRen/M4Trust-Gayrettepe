"""Incoming command validation (ADR-002 §7, §14, §17.1; ADR-003 §18.2).

ai-worker RabbitMQ'dan gelen command event'lerini business islemden ONCE
contract acisindan dogrular. Contract violation business rejection degildir.
"""
from __future__ import annotations

from app.config import get_settings
from app.contracts.envelope import EventEnvelope
from app.contracts.errors import ContractViolation, ErrorCode
from app.contracts.schema_store import validator_for_id

# Desteklenen command event türleri (ADR-002 §4).
_REQUESTED = "ai.job.requested.v1"
_CANCEL_REQUESTED = "ai.job.cancel.requested.v1"

# (eventType, jobType) -> concrete schema $id (ADR-002 §22 deterministik konvansiyon).
_SCHEMA_IDS: dict[tuple[str, str | None], str] = {
    (_REQUESTED, "DOCUMENT_EXTRACTION"): "https://schemas.m4trust.internal/ai/document-extraction/requested-event/1.0.0",
    (_REQUESTED, "VIDEO_ANALYSIS"): "https://schemas.m4trust.internal/ai/video-analysis/requested-event/1.0.0",
    (_CANCEL_REQUESTED, None): "https://schemas.m4trust.internal/ai/job/cancel-requested-event/1.0.0",
}


def _select_schema_id(event_type: str, job_type: str | None) -> str:
    if event_type == _CANCEL_REQUESTED:
        return _SCHEMA_IDS[(_CANCEL_REQUESTED, None)]
    key = (event_type, job_type)
    if key not in _SCHEMA_IDS:
        raise ContractViolation(
            ErrorCode.MISSING_REQUIRED_FIELD,
            f"unsupported command: eventType={event_type!r} jobType={job_type!r}",
        )
    return _SCHEMA_IDS[key]


def validate_command(raw: dict) -> EventEnvelope:
    """Ham command dict'ini dogrular ve EventEnvelope dondurur.

    Basarisizlikta uygun stable error code ile ContractViolation firlatir.
    """
    if not isinstance(raw, dict):
        raise ContractViolation(ErrorCode.MISSING_REQUIRED_FIELD, "command payload must be a JSON object")

    event_type = raw.get("eventType")
    job_type = raw.get("jobType")
    schema_version = raw.get("schemaVersion")

    # Desteklenmeyen schema version'i acik kod ile reddet (ADR-002 §17.1).
    if schema_version is not None and schema_version not in get_settings().supported_schema_versions:
        raise ContractViolation(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"unsupported schemaVersion: {schema_version!r}",
        )

    schema_id = _select_schema_id(event_type, job_type)

    validator = validator_for_id(schema_id)
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))
    if errors:
        # Sadece JSON path + kural; ham deger / PII tasinmaz (ADR-002 §12.3).
        detail = "; ".join(f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.validator}" for e in errors[:5])
        raise ContractViolation(ErrorCode.MISSING_REQUIRED_FIELD, f"schema validation failed: {detail}")

    return EventEnvelope.model_validate(raw)
