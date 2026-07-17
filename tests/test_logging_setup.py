"""Structured JSON logging testleri (ADR-007 §32-33)."""
from __future__ import annotations

import json
import logging

from app.common.logging_setup import JsonFormatter


def _make_record(*, msg: str, extra: dict | None = None, exc_info=None) -> logging.LogRecord:
    record = logging.LogRecord(
        name="ai-worker", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=exc_info,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_base_fields_are_present_and_json_parseable() -> None:
    formatter = JsonFormatter(service="m4trust-ai-worker", environment="local", version="0.1.0")
    record = _make_record(msg="job succeeded")

    payload = json.loads(formatter.format(record))

    assert payload["service"] == "m4trust-ai-worker"
    assert payload["environment"] == "local"
    assert payload["version"] == "0.1.0"
    assert payload["level"] == "INFO"
    assert payload["message"] == "job succeeded"
    assert payload["timestamp"].endswith("Z")


def test_known_context_fields_are_surfaced_as_top_level_keys() -> None:
    formatter = JsonFormatter(service="m4trust-ai-worker", environment="local", version="0.1.0")
    record = _make_record(msg="job failed", extra={"jobId": "job-1", "jobType": "DOCUMENT_EXTRACTION"})

    payload = json.loads(formatter.format(record))

    assert payload["jobId"] == "job-1"
    assert payload["jobType"] == "DOCUMENT_EXTRACTION"


def test_unknown_extra_fields_are_not_leaked() -> None:
    # Guvenlik: allowlist disindaki (ör. yanlislikla eklenmis) alanlar cikmaz.
    formatter = JsonFormatter(service="m4trust-ai-worker", environment="local", version="0.1.0")
    record = _make_record(msg="job failed", extra={"rawDocumentText": "should never appear"})

    payload = json.loads(formatter.format(record))

    assert "rawDocumentText" not in payload
    assert "should never appear" not in json.dumps(payload)
