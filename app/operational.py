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
    """ADR-002 §21.3 formatinda capability listesi."""
    versions = list(settings.supported_schema_versions)
    return {
        "service": settings.service_name,
        "serviceVersion": settings.service_version,
        "capabilities": [
            {
                "jobType": job_type,
                "requestSchemaVersions": versions,
                "resultSchemaVersions": versions,
            }
            for job_type in _JOB_TYPES
        ],
    }


def build_contracts() -> dict:
    """ADR-002 §21.4 formatinda contract checksum metadata'si."""
    contracts = []
    for name, path in _CONTRACT_FILES.items():
        entry = {"name": name, "version": "1.0.0"}
        if path.is_file():
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            entry["sha256"] = None
        contracts.append(entry)
    return {"contracts": contracts}
