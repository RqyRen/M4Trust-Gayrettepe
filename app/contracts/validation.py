"""Incoming command validation (ADR-002 §7, §14, §17.1; ADR-003 §18.2).

ai-worker RabbitMQ'dan gelen command event'lerini business islemden ONCE
contract acisindan dogrular. Contract violation business rejection degildir.
"""
from __future__ import annotations

from app.config import get_settings
from app.contracts.envelope import EventEnvelope
from app.contracts.errors import ContractViolation, ErrorCode
from app.contracts.schema_store import validator_for_id
from app.messaging.topology import RK_CANCEL_REQUESTED, RK_DOC_REQUESTED, RK_VIDEO_REQUESTED

# Desteklenen command event türleri (ADR-002 §4).
_REQUESTED = "ai.job.requested.v1"
_CANCEL_REQUESTED = "ai.job.cancel.requested.v1"

# Berke review #11: JSON Schema yalniz sekli dogrular, alanlar arasi tutarlilik
# (cross-field) runtime'da ayrica kontrol edilmeli. Asagidakiler ADR-002/ADR-007'de
# tanimli, gercekten ihlal edilebilecek tutarliliklardir -- listenin geri kalani
# (attemptNumber<=maxAttempts: tamamen internal, Spring'den hic gelmiyor; source
# reference offset tutarliligi: offset alanlari opsiyonel ve hic uretilmiyor;
# video time range: kendi urettigimiz cikti, yapisal olarak garanti) bu sistemde
# uygulanabilir degil.
_EXPECTED_PRODUCER_SERVICE = "m4trust-core-api"
_REQUEST_ROUTING_KEY_FOR_JOB_TYPE = {
    "DOCUMENT_EXTRACTION": RK_DOC_REQUESTED,
    "VIDEO_ANALYSIS": RK_VIDEO_REQUESTED,
}

# (eventType, jobType) -> concrete schema $id (ADR-002 §22 deterministik konvansiyon).
_SCHEMA_IDS: dict[tuple[str, str | None], str] = {
    (_REQUESTED, "DOCUMENT_EXTRACTION"): "https://schemas.m4trust.internal/ai/document-extraction/requested-event/1.0.0",
    (_REQUESTED, "VIDEO_ANALYSIS"): "https://schemas.m4trust.internal/ai/video-analysis/requested-event/1.0.0",
    (_CANCEL_REQUESTED, None): "https://schemas.m4trust.internal/ai/job/cancel-requested-event/1.0.0",
}


# Giden result event'leri (ADR-002 §5.3) — FastAPI kendi ciktisini dogrular (ADR-003 §18.1).
_COMPLETED = "ai.job.completed.v1"
_FAILED = "ai.job.failed.v1"

_RESULT_SCHEMA_IDS: dict[tuple[str, str], str] = {
    (_COMPLETED, "DOCUMENT_EXTRACTION"): "https://schemas.m4trust.internal/ai/document-extraction/completed-event/1.0.0",
    (_COMPLETED, "VIDEO_ANALYSIS"): "https://schemas.m4trust.internal/ai/video-analysis/completed-event/1.0.0",
    (_FAILED, "DOCUMENT_EXTRACTION"): "https://schemas.m4trust.internal/ai/document-extraction/failed-event/1.0.0",
    (_FAILED, "VIDEO_ANALYSIS"): "https://schemas.m4trust.internal/ai/video-analysis/failed-event/1.0.0",
}


def validate_outgoing(event: dict) -> None:
    """Yayinlanmadan once uretilen result event'ini canonical schema ile dogrular.

    ADR-002 §11: completed event yalniz canonical ve schema-valid sonuc icin
    yayinlanir. Schema-invalid bir event broker'a HIC cikmamalidir.
    """
    key = (event.get("eventType"), event.get("jobType"))
    schema_id = _RESULT_SCHEMA_IDS.get(key)
    if schema_id is None:
        raise ContractViolation(
            ErrorCode.MISSING_REQUIRED_FIELD,
            f"unknown outgoing event: eventType={key[0]!r} jobType={key[1]!r}",
        )

    errors = sorted(validator_for_id(schema_id).iter_errors(event), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.validator}" for e in errors[:5])
        raise ContractViolation(
            ErrorCode.MISSING_REQUIRED_FIELD,
            f"outgoing event failed schema validation: {detail}",
        )


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


def validate_semantic_consistency(request: dict, *, routing_key: str) -> None:
    """`ai.job.requested.v1` icin sekil-otesi (cross-field) tutarlilik kontrolleri.

    validate_command() JSON Schema acisindan gecerli ama tutarsiz bir mesaji
    (ornegin document-extraction routing key'inden gelmis ama jobType'i
    VIDEO_ANALYSIS diyen bir mesaji) engellemez. Bu fonksiyon o bosluklari
    kapatir; hepsi ContractViolation firlatir (business rejection degil).
    """
    producer_service = request["producer"]["service"]
    if producer_service != _EXPECTED_PRODUCER_SERVICE:
        raise ContractViolation(
            ErrorCode.MISSING_REQUIRED_FIELD,
            f"unexpected producer.service: {producer_service!r}",
        )

    job_type = request["jobType"]
    expected_routing_key = _REQUEST_ROUTING_KEY_FOR_JOB_TYPE.get(job_type)
    if expected_routing_key is not None and routing_key != expected_routing_key:
        raise ContractViolation(
            ErrorCode.MISSING_REQUIRED_FIELD,
            f"routing key {routing_key!r} does not match jobType {job_type!r}",
        )

    input_payload = request.get("payload", {}).get("input", {})
    input_id = input_payload.get("documentId") or input_payload.get("videoId")
    if input_id is not None and request["subjectId"] != input_id:
        raise ContractViolation(
            ErrorCode.MISSING_REQUIRED_FIELD,
            "subjectId does not match payload.input document/video identifier",
        )


def validate_cancel_semantic_consistency(request: dict, *, routing_key: str) -> None:
    """`ai.job.cancel.requested.v1` icin sekil-otesi tutarlilik kontrolu.

    validate_semantic_consistency() BURADA YENIDEN KULLANILAMAZ: o fonksiyon
    jobType'i REQUEST routing key'ine esler (orn. DOCUMENT_EXTRACTION ->
    ai.document-extraction.requested.v1), ama cancel mesaji her zaman
    ai.job.cancel.requested.v1 routing key'inden gelir -- esleme asla
    tutmaz, gecerli HER cancel dead-letter'a duser (bagimsiz denetim,
    21 Temmuz 2026). Bu yuzden ayri, kucuk bir kontrol.
    """
    producer_service = request["producer"]["service"]
    if producer_service != _EXPECTED_PRODUCER_SERVICE:
        raise ContractViolation(
            ErrorCode.MISSING_REQUIRED_FIELD,
            f"unexpected producer.service: {producer_service!r}",
        )
    if routing_key != RK_CANCEL_REQUESTED:
        raise ContractViolation(
            ErrorCode.MISSING_REQUIRED_FIELD,
            f"unexpected routing key for cancel command: {routing_key!r}",
        )
