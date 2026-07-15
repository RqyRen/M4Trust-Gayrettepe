"""DOCUMENT_EXTRACTION pipeline testleri (ADR-002 §3.1, §11, §13; ADR-001 §16).

Indirme, hash, tur tespiti, GERCEK PDF/DOCX metin cikarimi, canonical mapping
ve teknik schema validation gercek calisir. LLM cagrisi (ADR-004 §17
"mock-first") monkeypatch ile sahtelenir; gercek GPT-5.4 entegrasyonu ayri bir
canli script ile (pytest disinda) dogrulanir.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import docx
import pytest
from fpdf import FPDF
from pypdf import PdfWriter

from app.contracts.errors import ContractViolation, ErrorCode, PipelineFailure
from app.contracts.validation import validate_outgoing
from app.pipeline.document_extraction import llm as llm_module
from app.pipeline.document_extraction.media import DOCX, PDF, detect_media_type
from app.pipeline.document_extraction.pipeline import run
from app.pipeline.registry import pipeline_for

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"


def _real_pdf_bytes(text: str = "ACME Corp agrees to pay 1000 EUR within 30 days.") -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text=text)
    return bytes(pdf.output())


def _real_docx_bytes(text: str = "Delivery of sealed boxes is required with a signed delivery note.") -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _encrypted_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


_PDF_BYTES = _real_pdf_bytes()
_DOCX_BYTES = _real_docx_bytes()
_ENCRYPTED_PDF_BYTES = _encrypted_pdf_bytes()
_JUNK = b"just plain text, not a document"
_MALFORMED_PDF = b"%PDF-1.7\n" + b"not a real pdf body, no xref table " * 20

_BODIES = {
    "/pdf": _PDF_BYTES,
    "/docx": _DOCX_BYTES,
    "/junk": _JUNK,
    "/malformed-pdf": _MALFORMED_PDF,
    "/encrypted": _ENCRYPTED_PDF_BYTES,
}


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


def _request(base_url: str, path: str, body: bytes, media_type: str = PDF) -> dict:
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


_CANNED_LLM_OUTPUT = {
    "detectedLanguage": "en",
    "parties": [
        {
            "role": "SELLER",
            "legalName": "ACME Corp",
            "legalNameConfidence": 0.95,
            "taxIdentifier": "1234567890",  # mapping.py bunu daima maskelemeli
            "taxIdentifierConfidence": 0.8,
            "page": 1,
        }
    ],
    "rules": [
        {
            "category": "PAYMENT",
            "title": "Payment amount",
            "description": "Buyer pays 1000 EUR within 30 days.",
            "valueType": "MONEY",
            "textValue": None,
            "amountMinor": 100000,
            "currency": "eur",
            "basisPoints": None,
            "durationDays": None,
            "dateValue": None,
            "booleanValue": None,
            "quantityValue": None,
            "quantityUnit": None,
            "confidence": 0.9,
            "page": 1,
        }
    ],
    "deliveryRequirements": [
        {"evidenceType": "DELIVERY_NOTE", "required": True, "confidence": 0.85, "page": 1}
    ],
    "requiresManualReview": False,
    "reviewReasons": [],
}


def _mock_llm(monkeypatch: pytest.MonkeyPatch, output: dict = _CANNED_LLM_OUTPUT) -> None:
    monkeypatch.setattr(llm_module, "extract_structured_data", lambda text, settings: output)


# --- Pipeline uctan uca (gercek indirme + hash + tur + GERCEK metin cikarimi, LLM mock) ---

def test_pdf_pipeline_produces_schema_valid_completed_event(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_llm(monkeypatch)
    request = _request(base_url, "/pdf", _PDF_BYTES)
    event = run(request)

    assert event["eventType"] == "ai.job.completed.v1"
    assert event["jobType"] == "DOCUMENT_EXTRACTION"
    assert event["causationId"] == request["eventId"]
    validate_outgoing(event)

    document = event["payload"]["result"]["document"]
    assert document["detectedMediaType"] == PDF
    assert document["textExtractionMethod"] == "DIGITAL_PDF"
    assert document["pageCount"] == 1
    assert document["contentSha256"] == hashlib.sha256(_PDF_BYTES).hexdigest()


def test_docx_pipeline_produces_schema_valid_completed_event(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_llm(monkeypatch)
    request = _request(base_url, "/docx", _DOCX_BYTES, media_type=DOCX)
    event = run(request)

    validate_outgoing(event)
    document = event["payload"]["result"]["document"]
    assert document["detectedMediaType"] == DOCX
    assert document["textExtractionMethod"] == "DOCX"


def test_tax_identifier_is_always_masked(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # ADR-001 §16: ozel bir maskeleme alt sistemi olmadan ham deger asla cikmaz.
    _mock_llm(monkeypatch)
    request = _request(base_url, "/pdf", _PDF_BYTES)
    event = run(request)
    party = event["payload"]["result"]["parties"][0]
    assert party["taxIdentifier"]["masked"] is True
    assert party["taxIdentifier"]["value"] is None


def test_money_rule_mapped_correctly(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_llm(monkeypatch)
    request = _request(base_url, "/pdf", _PDF_BYTES)
    event = run(request)
    rule = event["payload"]["result"]["rules"][0]
    assert rule["structuredValue"] == {"type": "MONEY", "amountMinor": 100000, "currency": "EUR"}


def test_structured_value_fallback_produces_warning(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    broken_output = json.loads(json.dumps(_CANNED_LLM_OUTPUT))
    broken_output["rules"][0]["amountMinor"] = None  # MONEY beyan edildi ama tutar yok
    broken_output["rules"][0]["currency"] = None
    _mock_llm(monkeypatch, broken_output)

    request = _request(base_url, "/pdf", _PDF_BYTES)
    event = run(request)  # yine schema-valid olmali (validate_outgoing pipeline icinde calisir)

    rule = event["payload"]["result"]["rules"][0]
    assert rule["structuredValue"]["type"] == "TEXT"
    warnings = event["payload"]["warnings"]
    assert any(w["code"] == "STRUCTURED_VALUE_FALLBACK" for w in warnings)


# --- Hata yollari ---

def test_hash_mismatch_fails_before_extraction(base_url: str) -> None:
    request = _request(base_url, "/pdf", _PDF_BYTES)
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
    request = _request(base_url, "/pdf", _PDF_BYTES, media_type=DOCX)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_malformed_pdf_content_is_corrupted_file(base_url: str) -> None:
    # Dogru magic byte (%PDF-) ama gecersiz ic yapi: UNSUPPORTED_MEDIA_TYPE degil, CORRUPTED_FILE.
    request = _request(base_url, "/malformed-pdf", _MALFORMED_PDF)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.CORRUPTED_FILE


def test_encrypted_pdf_is_rejected(base_url: str) -> None:
    request = _request(base_url, "/encrypted", _ENCRYPTED_PDF_BYTES)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.ENCRYPTED_DOCUMENT_UNSUPPORTED


# --- Giden event dogrulamasi (ADR-002 §11) ---

def test_unknown_outgoing_event_is_rejected() -> None:
    with pytest.raises(ContractViolation):
        validate_outgoing({"eventType": "ai.job.completed.v1", "jobType": "AUDIO_ANALYSIS"})


# --- Metin cikarimi birim testleri ---

def test_detect_media_type_reads_magic_bytes(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(_PDF_BYTES)
    assert detect_media_type(pdf, declared=PDF) == PDF

    docx_path = tmp_path / "a.docx"
    docx_path.write_bytes(_DOCX_BYTES)
    assert detect_media_type(docx_path, declared=DOCX) == DOCX


# --- Registry ---

def test_registry_routes_document_extraction_to_real_pipeline() -> None:
    assert pipeline_for("DOCUMENT_EXTRACTION") is run
