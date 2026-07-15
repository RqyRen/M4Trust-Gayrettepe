"""DOCUMENT_EXTRACTION pipeline (ADR-002 §3.1).

ADR'deki adimlar ve mevcut durum:
  Dosya indirme            -> GERCEK (presigned URL, storage/object_store)
  Hash dogrulama           -> GERCEK (SHA-256; uyusmazlik retry edilmez)
  Dosya turu tespiti       -> GERCEK (magic byte)
  PDF/DOCX metin cikarimi  -> MOCK (Adim 8: gercek parser)
  OCR                      -> MOCK (Adim 8)
  Metin normalizasyonu     -> MOCK (Adim 8)
  Hassas veri analizi      -> MOCK (Adim 8)
  Maskeleme                -> MOCK (Adim 8)
  RAG                      -> MOCK (Adim 8)
  LLM extraction           -> MOCK (Adim 8)
  Canonical schema donusumu-> GERCEK (canonical payload uretilir)
  Teknik schema validation -> GERCEK (yayindan once dogrulanir)

Mock asamalar deterministik fixture uretir; gercek LLM/OCR/RAG CALISTIRILMAZ
(ADR-004 §12). Gercek model entegrasyonu bu modulun mock asamalarinin yerini
alacak; canonical payload ve contract DEGISMEYECEK (ADR-002 §26).
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.contracts.validation import validate_outgoing
from app.pipeline.document_extraction.media import EXTRACTION_METHOD, detect_media_type
from app.storage.object_store import fetch_source

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "contracts" / "examples" / "document-extraction" / "success-result.json"
)

PIPELINE_VERSION = "doc-pipeline-1.0.0"


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mock_extraction(path: Path, detected_media_type: str, content_sha256: str) -> dict:
    """MOCK: gercek parser/OCR/RAG/LLM yerine deterministik canonical sonuc.

    Adim 8'de bu fonksiyonun yerini gercek pipeline alacak. Ancak DONUS TIPI
    ayni kalacak: canonical `result` objesi.
    """
    fixture_result = json.loads(_FIXTURE.read_text(encoding="utf-8"))["payload"]["result"]

    # Gercek olarak tespit edilen degerleri canonical sonuca yaz.
    document = dict(fixture_result["document"])
    document["detectedMediaType"] = detected_media_type
    document["textExtractionMethod"] = EXTRACTION_METHOD[detected_media_type]
    document["contentSha256"] = content_sha256

    result = dict(fixture_result)
    result["document"] = document
    return result


def run(request: dict) -> dict:
    """Request envelope'undan canonical `ai.job.completed.v1` event'i uretir.

    Firlatir: PipelineFailure — indirme/hash/tur hatalarinda (retry runner ele alir).
    """
    started = time.monotonic()
    source_input = request["payload"]["input"]
    expected_sha256 = source_input["sha256"].lower()

    # Indirme + hash dogrulama + tur tespiti; context'ten cikinca gecici kopya silinir.
    with fetch_source(source_input) as path:
        detected_media_type = detect_media_type(path, declared=source_input["mediaType"])
        result = _mock_extraction(path, detected_media_type, expected_sha256)

    duration_ms = int((time.monotonic() - started) * 1000)

    event = {
        "eventId": str(uuid.uuid4()),
        "eventType": "ai.job.completed.v1",
        "schemaVersion": "1.0.0",
        "occurredAt": _utc_now_z(),
        "correlationId": request["correlationId"],
        "causationId": request["eventId"],
        "jobId": request["jobId"],
        "jobType": "DOCUMENT_EXTRACTION",
        "tenantId": request["tenantId"],
        "transactionId": request["transactionId"],
        "subjectId": request["subjectId"],
        "idempotencyKey": f"result:{request['jobId']}",
        "producer": {"service": "m4trust-ai-worker", "version": get_settings().service_version},
        "payload": {
            "result": result,
            "technicalMetadata": {
                "pipelineVersion": PIPELINE_VERSION,
                "modelProvider": None,   # mock asamada gercek provider yok
                "modelFamily": None,
                "modelVersion": None,
                "promptVersion": None,
                "retrievalVersion": None,
                "parserVersion": None,
                "privacyVersion": None,
                "durationMs": duration_ms,
            },
            "warnings": [],
        },
    }

    # Teknik schema validation: schema-invalid event broker'a cikmaz (ADR-002 §11).
    validate_outgoing(event)
    return event
