"""Fake pipeline ciktisinin contract'a uygunlugu (ADR-002 §11, ADR-004 §14).

Model mocklanir; uretilen completed event canonical completed-event schema'sina
UYMALIDIR. Boylece messaging siniri gercek olsa da yayinlanan event schema-valid
olur.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.contracts.schema_store import validator_for_id
from app.pipeline.fake import build_completed_event

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"

_COMPLETED_SCHEMA_ID = {
    "DOCUMENT_EXTRACTION": "https://schemas.m4trust.internal/ai/document-extraction/completed-event/1.0.0",
    "VIDEO_ANALYSIS": "https://schemas.m4trust.internal/ai/video-analysis/completed-event/1.0.0",
}


def _load_request(rel: str) -> dict:
    return json.loads((_EXAMPLES / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "request_fixture",
    [
        "document-extraction/full-request.json",
        "video-analysis/full-request.json",
    ],
)
def test_completed_event_is_schema_valid(request_fixture: str) -> None:
    request = _load_request(request_fixture)
    event = build_completed_event(request)

    # Envelope korelasyonu request'ten tasindi mi?
    assert event["eventType"] == "ai.job.completed.v1"
    assert event["jobId"] == request["jobId"]
    assert event["correlationId"] == request["correlationId"]
    assert event["causationId"] == request["eventId"]

    validator = validator_for_id(_COMPLETED_SCHEMA_ID[request["jobType"]])
    errors = list(validator.iter_errors(event))
    assert errors == [], "; ".join(e.message for e in errors)


def test_occurred_at_is_utc_z() -> None:
    event = build_completed_event(_load_request("document-extraction/full-request.json"))
    assert event["occurredAt"].endswith("Z")  # ADR-002 §6.1 UTC + Z
