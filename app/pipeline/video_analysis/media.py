"""Video/foto format tespiti (ADR-002 §3.2 "Format kontrolu").

Beyan edilen mediaType'a KORU KORUNE guvenilmez; icerigin kendisine bakilir.
Desteklenmeyen tur -> UNSUPPORTED_MEDIA_TYPE (non-retryable).
"""
from __future__ import annotations

from pathlib import Path

from app.contracts.errors import ErrorCode, PipelineFailure

MP4 = "video/mp4"
WEBM = "video/webm"
IMAGE_JPEG = "image/jpeg"
IMAGE_PNG = "image/png"

IMAGE_TYPES = (IMAGE_JPEG, IMAGE_PNG)

_HEADER_SIZE = 12


def _is_mp4(header: bytes) -> bool:
    # ISO BMFF/MP4: [4 byte box size][4 byte "ftyp"][...]
    return len(header) >= 8 and header[4:8] == b"ftyp"


def _is_webm(header: bytes) -> bool:
    # WebM/Matroska EBML magic: 0x1A45DFA3
    return header.startswith(b"\x1a\x45\xdf\xa3")


def _is_jpeg(header: bytes) -> bool:
    return header[:3] == b"\xff\xd8\xff"


def _is_png(header: bytes) -> bool:
    return header[:8] == b"\x89PNG\r\n\x1a\n"


_DETECTORS: tuple[tuple[object, str], ...] = (
    (_is_mp4, MP4),
    (_is_webm, WEBM),
    (_is_jpeg, IMAGE_JPEG),
    (_is_png, IMAGE_PNG),
)


def detect_media_type(path: Path, *, declared: str) -> str:
    """Icerigin magic byte'larindan gercek video formatini tespit eder.

    Firlatir: PipelineFailure(UNSUPPORTED_MEDIA_TYPE) — format desteklenmiyorsa
    veya beyan edilen tur icerikle celisiyorsa.
    """
    with path.open("rb") as handle:
        header = handle.read(_HEADER_SIZE)

    detected = next((media for check, media in _DETECTORS if check(header)), None)

    if detected is None:
        raise PipelineFailure(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "source content is not a supported video format",
            details={"field": "input.mediaType", "reason": "unsupported format"},
        )

    if declared != detected:
        raise PipelineFailure(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "declared media type does not match the detected content format",
            details={"field": "input.mediaType", "reason": "declared/detected mismatch"},
        )

    return detected
