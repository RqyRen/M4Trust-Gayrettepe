"""Minimum kritik contract validation testleri (ADR-002 §24, ADR-004 §7).

Genis unit-test katmani DEGIL; contract boundary'nin kritik davranislari:
- canonical fixture kabulu
- desteklenmeyen schema version reddi
- bilinmeyen enum (jobType) reddi
- eksik zorunlu alan reddi
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.contracts.errors import ContractViolation, ErrorCode
from app.contracts.validation import validate_command, validate_semantic_consistency
from app.messaging.topology import RK_DOC_REQUESTED, RK_VIDEO_REQUESTED

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"


def _load(rel: str) -> dict:
    return json.loads((_EXAMPLES / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fixture",
    [
        "document-extraction/minimum-request.json",
        "document-extraction/full-request.json",
        "video-analysis/minimum-request.json",
        "video-analysis/full-request.json",
        "job/cancel-request.json",
    ],
)
def test_canonical_fixtures_accepted(fixture: str) -> None:
    envelope = validate_command(_load(fixture))
    assert envelope.eventType in {"ai.job.requested.v1", "ai.job.cancel.requested.v1"}


def test_unsupported_schema_version_rejected() -> None:
    req = _load("document-extraction/minimum-request.json")
    req["schemaVersion"] = "2.0.0"
    with pytest.raises(ContractViolation) as exc:
        validate_command(req)
    assert exc.value.code is ErrorCode.UNSUPPORTED_SCHEMA_VERSION


def test_unknown_job_type_rejected() -> None:
    req = _load("document-extraction/minimum-request.json")
    req["jobType"] = "AUDIO_ANALYSIS"
    with pytest.raises(ContractViolation):
        validate_command(req)


def test_missing_required_field_rejected() -> None:
    req = _load("document-extraction/minimum-request.json")
    del req["payload"]["input"]["sha256"]
    with pytest.raises(ContractViolation) as exc:
        validate_command(req)
    assert exc.value.code is ErrorCode.MISSING_REQUIRED_FIELD


def test_unknown_optional_field_tolerated() -> None:
    # Bilinmeyen optional alan envelope kokunde tolere edilir (ADR-002 §14).
    req = _load("document-extraction/full-request.json")
    req["futureOptionalField"] = {"note": "ignored by older consumers"}
    envelope = validate_command(req)
    assert envelope.jobType == "DOCUMENT_EXTRACTION"


# --- validate_semantic_consistency (Berke review #11) ---


def test_semantic_consistency_accepts_canonical_fixture() -> None:
    req = _load("document-extraction/full-request.json")
    validate_semantic_consistency(req, routing_key=RK_DOC_REQUESTED)  # firlatmamali


def test_semantic_consistency_rejects_unexpected_producer_service() -> None:
    req = _load("document-extraction/full-request.json")
    req["producer"]["service"] = "some-other-service"
    with pytest.raises(ContractViolation) as exc:
        validate_semantic_consistency(req, routing_key=RK_DOC_REQUESTED)
    assert exc.value.code is ErrorCode.MISSING_REQUIRED_FIELD


def test_semantic_consistency_rejects_routing_key_job_type_mismatch() -> None:
    req = _load("document-extraction/full-request.json")  # jobType=DOCUMENT_EXTRACTION
    with pytest.raises(ContractViolation):
        validate_semantic_consistency(req, routing_key=RK_VIDEO_REQUESTED)


def test_semantic_consistency_rejects_subject_id_document_id_mismatch() -> None:
    req = _load("document-extraction/full-request.json")
    req["subjectId"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"  # payload.input.documentId'den farkli
    with pytest.raises(ContractViolation):
        validate_semantic_consistency(req, routing_key=RK_DOC_REQUESTED)
