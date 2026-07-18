"""DOCUMENT_EXTRACTION pipeline (ADR-002 SS3.1).

ADR'deki adimlar ve mevcut durum:
  Dosya indirme            -> GERCEK (presigned URL, storage/object_store)
  Hash dogrulama           -> GERCEK (SHA-256; uyusmazlik retry edilmez)
  Dosya turu tespiti       -> GERCEK (magic byte)
  PDF/DOCX metin cikarimi  -> GERCEK (pypdf / python-docx, text_extraction.py)
  OCR                      -> GERCEK (Tesseract, tur+eng; sayfa dijital metin
                               esiginin altindaysa otomatik devreye girer,
                               HYBRID/OCR olarak isaretlenir, OCR_USED warning)
  Metin normalizasyonu     -> KAPSAM DISI (ilk surumde yapilmiyor)
  Hassas veri analizi      -> GERCEK: vergi no/TC kimlik/IBAN/e-posta/telefon
                               LLM'e GITMEDEN ONCE maskelenir (pii_masking.py,
                               bulgu 17 Temmuz 2026); vergi kimligi ayrica
                               canonical ciktida da her zaman maskelenir (mapping.py)
  Maskeleme                -> GERCEK (provider-oncesi + canonical, yukaridaki gibi)
  RAG                      -> KAPSAM DISI (ilk surumde retrieval kullanilmiyor)
  LLM extraction           -> GERCEK (GPT-5.4, llm.py)
  Canonical schema donusumu-> GERCEK (mapping.py)
  Teknik schema validation -> GERCEK (yayindan once dogrulanir)

Model-native cevap (GPT ham JSON'u) yalniz bu modul icinde tuketilir; Spring'e
asla ulasmaz (ADR-002 SS8.1, SS10). Model/provider degisikligi (ADR-002 SS26)
bu dosyanin disina sizmadan yapilabilir.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.contracts.validation import validate_outgoing
from app.pipeline.document_extraction import llm, mapping
from app.pipeline.document_extraction.media import detect_media_type
from app.pipeline.document_extraction.pii_masking import PRIVACY_VERSION, mask_pii
from app.pipeline.document_extraction.text_extraction import ExtractedDocument, extract_text
from app.storage.object_store import fetch_source

PIPELINE_VERSION = "doc-pipeline-1.1.0"
PROMPT_VERSION = "contract-extraction-1.0.0"


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncation_warning(full_text: str, settings) -> dict | None:
    """Prompt sinirini asan belgelerde uyari uretir (ADR-002 SS13).

    Kanit (Adim 8 kalite testi, 15 Temmuz 2026): 70.108 karakterlik gercekci
    bir sozlesmede, cikarilan metin (72.865 karakter) 60.000 karakter
    sinirinda kesiliyor ve belgenin SONUNDAKI benzersiz bir madde (750.000 TL
    cezai sart) GPT'ye HIC gonderilmeden kayboluyordu, hicbir warning
    uretilmeden. Bu fonksiyon o sessiz kaybi engeller.
    """
    limit = settings.openai_max_source_chars
    if len(full_text) <= limit:
        return None
    return {
        "code": "PARTIAL_TEXT_EXTRACTION",
        "message": "Document text exceeded the processing limit; content beyond the limit was not sent to the model.",
        "severity": "WARNING",
        "path": "$.result",
        "details": {
            "field": "input.document",
            "reason": "source text exceeded configured character limit",
            "expected": f"<= {limit} characters",
            "observed": f"{len(full_text)} characters",
        },
    }


def _ocr_warnings(extracted: ExtractedDocument) -> list[dict]:
    """OCR fallback kullanildiysa canonical warning uretir (ADR-002 SS13)."""
    if not extracted.ocr_pages:
        return []
    return [
        {
            "code": "OCR_USED",
            "message": "OCR was used to extract text from one or more pages with no digital text layer.",
            "severity": "INFO",
            "path": "$.result.document",
            "details": {
                "field": "document.textExtractionMethod",
                "reason": "digital text layer was missing or too short",
                "expected": "DIGITAL_PDF",
                "observed": f"OCR used on page(s): {', '.join(str(p) for p in extracted.ocr_pages)}",
            },
        }
    ]


def run(request: dict) -> dict:
    """Request envelope'undan canonical `ai.job.completed.v1` event'i uretir.

    Firlatir: PipelineFailure — indirme/hash/tur/parse/LLM hatalarinda
    (retry runner ele alir).
    """
    started = time.monotonic()
    settings = get_settings()
    source_input = request["payload"]["input"]
    expected_sha256 = source_input["sha256"].lower()

    with fetch_source(source_input) as path:
        detected_media_type = detect_media_type(path, declared=source_input["mediaType"])
        extracted = extract_text(path, detected_media_type=detected_media_type)
        full_text = extracted.full_text()
        # Provider'a (OpenAI) gitmeden once maskele (bulgu, 17 Temmuz 2026):
        # vergi no/TC kimlik/IBAN/e-posta/telefon LLM'e hic ulasmaz. Canonical
        # ciktiyi etkilemez -- mapping.py vergi kimligini zaten daima maskeliyordu.
        masked_text = mask_pii(full_text)
        truncation_warning = _truncation_warning(masked_text, settings)
        llm_output = llm.extract_structured_data(masked_text, settings)

    document = {
        "detectedMediaType": detected_media_type,
        "detectedLanguage": (llm_output.get("detectedLanguage") or "und")[:8],
        "pageCount": extracted.page_count,
        "textExtractionMethod": extracted.method,
        "contentSha256": expected_sha256,
    }
    result, mapping_warnings = mapping.map_to_canonical_result(llm_output, document=document)
    warnings = mapping_warnings + _ocr_warnings(extracted)

    if truncation_warning is not None:
        warnings.append(truncation_warning)
        # Pasif warning yetmez: kaybolan icerik gercek is kurali tasiyor olabilir
        # (kanitlandi - bkz. _truncation_warning docstring'i). Aktif olarak
        # manual review'a zorla.
        result["summary"]["requiresManualReview"] = True
        result["summary"]["reviewReasons"] = [
            "Document text exceeded the processing limit; some content was not analyzed.",
            *result["summary"]["reviewReasons"],
        ]

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
        "producer": {"service": "m4trust-ai-worker", "version": settings.service_version},
        "payload": {
            "result": result,
            "technicalMetadata": {
                "pipelineVersion": PIPELINE_VERSION,
                "modelProvider": "openai",
                "modelFamily": "gpt-5.4",
                "modelVersion": settings.openai_model,
                "promptVersion": PROMPT_VERSION,
                "retrievalVersion": None,  # RAG bu surumde kullanilmiyor
                "parserVersion": "pypdf+python-docx",
                "privacyVersion": PRIVACY_VERSION,
                "durationMs": duration_ms,
            },
            "warnings": warnings,
        },
    }

    # Teknik schema validation: schema-invalid event broker'a cikmaz (ADR-002 SS11).
    validate_outgoing(event)
    return event
