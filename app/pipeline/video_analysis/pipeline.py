"""VIDEO_ANALYSIS pipeline (ADR-002 SS3.2).

ADR'deki adimlar ve mevcut durum:
  Video/foto indirme        -> GERCEK (presigned URL, storage/object_store)
  Hash dogrulama            -> GERCEK (SHA-256; uyusmazlik retry edilmez)
  Format kontrolu           -> GERCEK (magic byte: MP4/WebM/JPEG/PNG)
  Frame veya segment analizi-> GERCEK (video: OpenCV sabit araliklarla ornekleme;
                                foto: tek kare, t=0)
  Nesne veya olay tespiti   -> GERCEK (Roboflow logistics-sz9jr modeli)
  Confidence uretimi        -> GERCEK (Roboflow'un kendi confidence degeri)
  Advisory anomaly uretimi  -> GERCEK (Roboflow detecting-a-damaged-parcel modeli)
  Canonical schema donusumu -> GERCEK (aggregation.py)
  Teknik schema validation  -> GERCEK (yayindan once dogrulanir)

Video sonucu SADECE advisory niteliktedir (ADR-002 SS10.1, ADR-003 SS22):
odeme, teslimat veya dispute karari vermez. Model-native Roboflow cevabi
yalniz bu modul icinde tuketilir; Spring'e asla ulasmaz (ADR-002 SS8.1, SS10).
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from app.config import get_settings
from app.contracts.validation import validate_outgoing
from app.pipeline.deadline import check_deadline
from app.pipeline.video_analysis import aggregation, roboflow_client
from app.pipeline.video_analysis.frames import sample_frames, sample_image
from app.pipeline.video_analysis.media import IMAGE_TYPES, detect_media_type
from app.storage.object_store import fetch_source

PIPELINE_VERSION = "video-pipeline-1.2.0"


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(request: dict, *, check_cancelled: Callable[[], None] = lambda: None) -> dict:
    """Request envelope'undan canonical `ai.job.completed.v1` event'i uretir.

    `check_cancelled` pahali adimlardan ve her frame'in Roboflow cagrisindan
    ONCE cagrilir (Berke review #1: best-effort cooperative cancellation) --
    iptal edilmisse `JobCancelled` firlatir ve kalan frame'ler icin provider
    cagrisi yapilmaz. Varsayilan no-op.

    Ayni checkpoint'lerde `payload.deadlineAt` de kontrol edilir (Berke
    review #5): suresi gecmis bir job icin pahali indirme/Roboflow cagrisi
    yapilmaz, stable `INVALID_DEADLINE` failure uretilir.

    Firlatir: PipelineFailure — indirme/hash/format/frame/provider/deadline
    hatalarinda (retry runner ele alir). JobCancelled — cancellation
    checkpoint'inde.
    """
    started = time.monotonic()
    settings = get_settings()
    source_input = request["payload"]["input"]
    expected_objects = request["payload"]["processing"].get("expectedObjects", [])
    deadline_at = request["payload"]["deadlineAt"]

    check_cancelled()
    check_deadline(deadline_at)
    with fetch_source(source_input) as path:
        detected_media_type = detect_media_type(path, declared=source_input["mediaType"])
        frames = sample_image(path) if detected_media_type in IMAGE_TYPES else sample_frames(path, settings)

        logistics_per_frame = []
        damage_per_frame = []
        for f in frames:
            # Uzun videolarda ONE frame'lik gecikme yerine her frame'de kontrol
            # eder -- iptal/deadline ortasinda gorulurse kalan frame'ler icin
            # (maliyetli) Roboflow cagrisi hic yapilmaz.
            check_cancelled()
            check_deadline(deadline_at)
            logistics_per_frame.append(roboflow_client.detect_objects(f.jpeg, settings))
            damage_per_frame.append(roboflow_client.detect_damage(f.jpeg, settings))

    result = aggregation.aggregate_results(
        frames=frames,
        logistics_predictions_per_frame=logistics_per_frame,
        damage_predictions_per_frame=damage_per_frame,
        expected_objects=expected_objects,
        min_confidence=settings.roboflow_min_confidence,
    )

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
        "producer": {"service": "m4trust-ai-worker", "version": settings.service_version},
        "payload": {
            "result": result,
            "technicalMetadata": {
                "pipelineVersion": PIPELINE_VERSION,
                "modelProvider": "roboflow",
                "modelFamily": f"{settings.roboflow_logistics_model_id}+{settings.roboflow_damage_model_id}",
                "modelVersion": None,
                "promptVersion": None,
                "retrievalVersion": None,
                "parserVersion": "opencv-frame-sampler",
                "privacyVersion": None,
                "durationMs": duration_ms,
            },
            "warnings": [],
        },
    }

    # Teknik schema validation: schema-invalid event broker'a cikmaz (ADR-002 SS11).
    validate_outgoing(event)
    return event
