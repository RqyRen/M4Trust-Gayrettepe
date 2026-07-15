"""DOCUMENT_EXTRACTION pipeline testleri (ADR-002 §3.1, §11; ADR-003 §18.1).

Gercek HTTP kaynagi uzerinden: indirme -> hash -> tur tespiti -> canonical
sonuc -> teknik schema validation.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.contracts.errors import ContractViolation, ErrorCode, PipelineFailure
from app.contracts.validation import validate_outgoing
from app.pipeline.document_extraction.media import DOCX, PDF, detect_media_type
from app.pipeline.document_extraction.pipeline import run
from app.pipeline.registry import pipeline_for

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"

_PDF = b"%PDF-1.7\n" + b"contract body " * 100
_DOCX = b"PK\x03\x04" + b"ooxml body " * 100
_JUNK = b"just plain text, not a document"

_BODIES = {"/pdf": _PDF, "/docx": _DOCX, "/junk": _JUNK}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = _BODIES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


@pytest.fixture(scope="module")
def base_url() -> str:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _request(base_url: str, path: str, body: bytes, media_type: str = "application/pdf") -> dict:
    req = json.loads((_EXAMPLES / "document-extraction" / "full-request.json").read_text(encoding="utf-8"))
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    req["payload"]["input"].update(
        {
            "mediaType": media_type,
            "sizeBytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "download": {"url": f"{base_url}{path}", "expiresAt": expires},
        }
    )
    return req


# --- Pipeline uctan uca (gercek indirme + hash + tur) ---

def test_pdf_produces_schema_valid_completed_event(base_url: str) -> None:
    request = _request(base_url, "/pdf", _PDF)
    event = run(request)

    assert event["eventType"] == "ai.job.completed.v1"
    assert event["jobType"] == "DOCUMENT_EXTRACTION"
    assert event["causationId"] == request["eventId"]
    # run() icinde validate_outgoing cagrildi; burada tekrar teyit.
    validate_outgoing(event)


def test_detected_values_come_from_real_content(base_url: str) -> None:
    request = _request(base_url, "/pdf", _PDF)
    event = run(request)
    document = event["payload"]["result"]["document"]

    # Fixture'dan degil, gercek icerikten tespit edildi.
    assert document["detectedMediaType"] == PDF
    assert document["textExtractionMethod"] == "DIGITAL_PDF"
    assert document["contentSha256"] == hashlib.sha256(_PDF).hexdigest()


def test_docx_uses_docx_extraction_method(base_url: str) -> None:
    request = _request(base_url, "/docx", _DOCX, media_type=DOCX)
    event = run(request)
    document = event["payload"]["result"]["document"]
    assert document["detectedMediaType"] == DOCX
    assert document["textExtractionMethod"] == "DOCX"


def test_hash_mismatch_fails_before_extraction(base_url: str) -> None:
    request = _request(base_url, "/pdf", _PDF)
    request["payload"]["input"]["sha256"] = "a" * 64
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.CONTENT_HASH_MISMATCH


def test_unsupported_content_rejected(base_url: str) -> None:
    request = _request(base_url, "/junk", _JUNK)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_declared_media_type_mismatch_rejected(base_url: str) -> None:
    # Icerik PDF ama DOCX beyan edilmis.
    request = _request(base_url, "/pdf", _PDF, media_type=DOCX)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA_TYPE


# --- Tur tespiti birim davranisi ---

def test_detect_media_type_reads_magic_bytes(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(_PDF)
    assert detect_media_type(pdf, declared=PDF) == PDF

    docx = tmp_path / "a.docx"
    docx.write_bytes(_DOCX)
    assert detect_media_type(docx, declared=DOCX) == DOCX


# --- Giden event dogrulamasi (ADR-002 §11) ---

def test_schema_invalid_outgoing_event_is_rejected(base_url: str) -> None:
    request = _request(base_url, "/pdf", _PDF)
    event = run(request)
    event["payload"]["result"]["document"]["pageCount"] = 0  # schema: minimum 1
    with pytest.raises(ContractViolation):
        validate_outgoing(event)


def test_unknown_outgoing_event_is_rejected() -> None:
    with pytest.raises(ContractViolation):
        validate_outgoing({"eventType": "ai.job.completed.v1", "jobType": "AUDIO_ANALYSIS"})


# --- Registry ---

def test_registry_routes_document_extraction_to_real_pipeline() -> None:
    assert pipeline_for("DOCUMENT_EXTRACTION") is run
