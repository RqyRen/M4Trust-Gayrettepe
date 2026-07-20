"""Operational metadata: capabilities and contract checksums.

Bu modul ADR-002 §21.3 (capabilities) ve §21.4 (contracts) endpoint'lerinin
icerigini uretir. Runtime contract discovery icin degil, operasyonel
dogrulama icindir; Spring her job oncesi bu endpoint'leri cagirmaz.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.config import Settings

# Repo kokundeki paylasilan contract deposu (Spring ile senkron kopya).
_CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"

# Desteklenen public job turleri (ADR-002 §3).
_JOB_TYPES = ("DOCUMENT_EXTRACTION", "VIDEO_ANALYSIS")

# Berke review #10 (17 Temmuz 2026): capabilities endpoint'i hangi processing
# adimlarinin GERCEKTEN calistigini belirtmiyordu -- ornegin retrievalProfile
# request'te kabul ediliyordu ama RAG hic calismiyordu (bu artik dogru degil,
# PR #31/#32 ile RAG/legalBasis gercekten calisiyor). `features` alani
# additionalProperties: true kapsaminda (contracts/openapi/ai-internal-v1.yaml)
# -- schema degisikligi/Berke onayi gerektirmeyen, saf additive bir alan.
_DOCUMENT_EXTRACTION_FEATURES = {
    "privacyProfiles": ["DEFAULT"],
    "piiMasking": (
        "Structured PII (tax identifier, national ID, IBAN, email, phone) is masked before "
        "the LLM call. Free-text person names are also masked before the LLM call using a "
        "local NER model (best-effort, not guaranteed complete); names the model uses as a "
        "contract party's legalName are reversibly restored in the output, other detected "
        "names are not. Company/legal-entity names are never masked."
    ),
    "textNormalization": False,
    "retrievalProfiles": ["M4TRUST_LEGAL_DEFAULT"],
    "legalGrounding": (
        "Retrieves relevant Turkish legislation articles (Turkish Code of Obligations, KVKK, "
        "Payment Services Law 6493 plus its implementing regulation/communique, AML Law 5549) "
        "as advisory context during rule classification. Individual rules may include an "
        "optional legalBasis reference; absent when retrieval found no sufficiently relevant match."
    ),
}
_VIDEO_ANALYSIS_FEATURES = {
    "objectCounting": True,
    "damageDetection": True,
}
_FEATURES_BY_JOB_TYPE = {
    "DOCUMENT_EXTRACTION": _DOCUMENT_EXTRACTION_FEATURES,
    "VIDEO_ANALYSIS": _VIDEO_ANALYSIS_FEATURES,
}

# Public contract adi -> schema dosyasi (ADR-002 §21.4, §22).
_CONTRACT_FILES: dict[str, Path] = {
    "m4trust.document-extraction-request": _CONTRACTS_DIR
    / "schemas/document-extraction/request-payload-1.0.0.schema.json",
    "m4trust.document-extraction-result": _CONTRACTS_DIR
    / "schemas/document-extraction/result-payload-1.0.0.schema.json",
    "m4trust.video-analysis-request": _CONTRACTS_DIR
    / "schemas/video-analysis/request-payload-1.0.0.schema.json",
    "m4trust.video-analysis-result": _CONTRACTS_DIR
    / "schemas/video-analysis/result-payload-1.0.0.schema.json",
}


def build_capabilities(settings: Settings) -> dict:
    """ADR-002 §21.3 formatinda capability listesi.

    `gitCommitSha`/`buildTime`/`deploymentEnvironment` ADR-007 §28 (release
    kimligi) icin eklendi -- Capabilities semasi `additionalProperties: true`
    oldugundan (contracts/openapi/ai-internal-v1.yaml) saf additive bir alan,
    schema degisikligi/Berke onayi gerektirmez (PR #34'teki `features` alaniyla
    ayni gerekce).
    """
    versions = list(settings.supported_schema_versions)
    return {
        "service": settings.service_name,
        "serviceVersion": settings.service_version,
        "gitCommitSha": settings.git_commit_sha or None,
        "buildTime": settings.build_time or None,
        "deploymentEnvironment": settings.app_env,
        "capabilities": [
            {
                "jobType": job_type,
                "requestSchemaVersions": versions,
                "resultSchemaVersions": versions,
                "features": _FEATURES_BY_JOB_TYPE[job_type],
            }
            for job_type in _JOB_TYPES
        ],
    }


def build_contracts() -> list[dict]:
    """ADR-002 §21.4 formatinda contract checksum metadata'si (duz array)."""
    contracts = []
    for name, path in _CONTRACT_FILES.items():
        entry = {"name": name, "version": "1.0.0"}
        if path.is_file():
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            entry["sha256"] = None
        contracts.append(entry)
    return contracts
