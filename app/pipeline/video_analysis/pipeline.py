"""VIDEO_ANALYSIS pipeline (ADR-002 §3.2).

ADR'deki adimlar ve mevcut durum:
  Video indirme            -> GERCEK (presigned URL, storage/object_store)
  Hash dogrulama           -> GERCEK (SHA-256; uyusmazlik retry edilmez)
  Format kontrolu          -> GERCEK (magic byte)
  Frame/segment analizi    -> MOCK (Adim 8: gercek video modeli)
  Nesne/olay tespiti       -> MOCK (Adim 8)
  Confidence uretimi       -> MOCK (Adim 8)
  Advisory anomaly uretimi -> MOCK (Adim 8)
  Canonical schema donusumu-> GERCEK
  Teknik schema validation -> GERCEK (yayindan once dogrulanir)

Video sonucu SADECE advisory niteliktedir (ADR-002 §10.1, ADR-003 §22):
odeme, teslimat veya dispute karari vermez. Mock asamalar deterministik
fixture uretir; gercek model CALISTIRILMAZ (ADR-004 §12).
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.contracts.validation import validate_outgoing
from app.pipeline.video_analysis.media import detect_media_type
from app.storage.object_store import fetch_source

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "contracts" / "examples" / "video-analysis" / "success-result.json"
)

PIPELINE_VERSION = "video-pipeline-1.0.0"


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mock_analysis() -> dict:
    """MOCK: gercek video modeli yerine deterministik canonical sonuc.

    Adim 8'de bu fonksiyonun yerini gercek pipeline alacak; DONUS TIPI
    (canonical `result` objesi) ayni kalacak.
    """
    fixture_result = json.loads(_FIXTURE.read_text(encoding="utf-8"))["payload"]["result"]
    return dict(fixture_result)


def run(request: dict) -> dict:
    """Request envelope'undan canonical `ai.job.completed.v1` event'i uretir.

    Firlatir: PipelineFailure — indirme/hash/format hatalarinda (retry runner ele alir).
    """
    started = time.monotonic()
    source_input = request["payload"]["input"]

    # Indirme + hash dogrulama + format kontrolu; context'ten cikinca gecici kopya silinir.
    with fetch_source(source_input) as path:
        detect_media_type(path, declared=source_input["mediaType"])
        result = _mock_analysis()

    duration_ms = int((time.monotonic() - started) * 1000)

    event = {
        "eventId": str(uuid.uuid4()),
        "eventType": "ai.job.completed.v1",
        "schemaVersion": "1.0.0",
        "occurredAt": _utc_now_z(),
        "correlationId": request["correlationId"],
        "causationId": request["eventId"],
        "jobId": request["jobId"],
        "jobType": "VIDEO_ANALYSIS",
        "tenantId": request["tenantId"],
        "transactionId": request["transactionId"],
        "subjectId": request["subjectId"],
        "idempotencyKey": f"result:{request['jobId']}",
        "producer": {"service": "m4trust-ai-worker", "version": get_settings().service_version},
        "payload": {
            "result": result,
            "technicalMetadata": {
                "pipelineVersion": PIPELINE_VERSION,
                "modelProvider": None,  # mock asamada gercek provider yok
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
