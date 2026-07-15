"""Dosya turu tespiti (ADR-002 §3.1 "Dosya turu tespiti").

Beyan edilen mediaType'a KORU KORUNE guvenilmez; icerigin kendisine bakilir.
Desteklenmeyen tur -> UNSUPPORTED_MEDIA_TYPE (non-retryable).
"""
from __future__ import annotations

from pathlib import Path

from app.contracts.errors import ErrorCode, PipelineFailure

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Tespit edilen tur -> canonical textExtractionMethod (result schema enum'u).
EXTRACTION_METHOD = {
    PDF: "DIGITAL_PDF",
    DOCX: "DOCX",
}

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", PDF),
    (b"PK\x03\x04", DOCX),  # OOXML bir ZIP konteyneridir
)


def detect_media_type(path: Path, *, declared: str) -> str:
    """Icerigin magic byte'larindan gercek medya turunu tespit eder.

    Firlatir: PipelineFailure(UNSUPPORTED_MEDIA_TYPE) — tur desteklenmiyorsa
    veya beyan edilen tur icerikle celisiyorsa.
    """
    with path.open("rb") as handle:
        header = handle.read(8)

    detected = next((media for magic, media in _MAGIC if header.startswith(magic)), None)

    if detected is None:
        raise PipelineFailure(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "source content is not a supported document format",
            details={"field": "input.mediaType", "reason": "unsupported format"},
        )

    if declared != detected:
        # Beyan ile icerik celisiyor: sessizce icerige gore devam etmek yerine reddet.
        raise PipelineFailure(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "declared media type does not match the detected content format",
            details={"field": "input.mediaType", "reason": "declared/detected mismatch"},
        )

    return detected
