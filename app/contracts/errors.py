"""Stable error contract (ADR-002 §12).

Error code'lar stable contract parcasidir. FastAPI teknik hatalari bu stable
kodlara cevirir; business rejection kodu URETMEZ (ADR-001 §14, ADR-002 §12.3).
"""
from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    RETRYABLE_TECHNICAL = "RETRYABLE_TECHNICAL"
    NON_RETRYABLE_TECHNICAL = "NON_RETRYABLE_TECHNICAL"
    INVALID_INPUT = "INVALID_INPUT"


class ErrorCode(str, Enum):
    # Retryable technical
    MODEL_PROVIDER_TIMEOUT = "MODEL_PROVIDER_TIMEOUT"
    MODEL_PROVIDER_UNAVAILABLE = "MODEL_PROVIDER_UNAVAILABLE"
    OBJECT_STORAGE_TEMPORARILY_UNAVAILABLE = "OBJECT_STORAGE_TEMPORARILY_UNAVAILABLE"
    RETRIEVAL_SERVICE_UNAVAILABLE = "RETRIEVAL_SERVICE_UNAVAILABLE"
    INTERNAL_DEPENDENCY_TIMEOUT = "INTERNAL_DEPENDENCY_TIMEOUT"

    # Non-retryable technical
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    ENCRYPTED_DOCUMENT_UNSUPPORTED = "ENCRYPTED_DOCUMENT_UNSUPPORTED"
    CORRUPTED_FILE = "CORRUPTED_FILE"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"

    # Invalid input
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_DEADLINE = "INVALID_DEADLINE"
    INVALID_DOWNLOAD_REFERENCE = "INVALID_DOWNLOAD_REFERENCE"
    INVALID_PROCESSING_PROFILE = "INVALID_PROCESSING_PROFILE"
    INVALID_EXPECTED_OBJECT = "INVALID_EXPECTED_OBJECT"


# Kategori <-> retryRecommended baglantisi (ADR-002 §12, contracts error schema).
_RETRYABLE_CODES = {
    ErrorCode.MODEL_PROVIDER_TIMEOUT,
    ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
    ErrorCode.OBJECT_STORAGE_TEMPORARILY_UNAVAILABLE,
    ErrorCode.RETRIEVAL_SERVICE_UNAVAILABLE,
    ErrorCode.INTERNAL_DEPENDENCY_TIMEOUT,
}
_NON_RETRYABLE_CODES = {
    ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
    ErrorCode.FILE_TOO_LARGE,
    ErrorCode.ENCRYPTED_DOCUMENT_UNSUPPORTED,
    ErrorCode.CORRUPTED_FILE,
    ErrorCode.CONTENT_HASH_MISMATCH,
}
_INVALID_INPUT_CODES = {
    ErrorCode.MISSING_REQUIRED_FIELD,
    ErrorCode.INVALID_DEADLINE,
    ErrorCode.INVALID_DOWNLOAD_REFERENCE,
    ErrorCode.INVALID_PROCESSING_PROFILE,
    ErrorCode.INVALID_EXPECTED_OBJECT,
}


def category_for(code: ErrorCode) -> ErrorCategory:
    if code in _RETRYABLE_CODES:
        return ErrorCategory.RETRYABLE_TECHNICAL
    if code in _NON_RETRYABLE_CODES:
        return ErrorCategory.NON_RETRYABLE_TECHNICAL
    return ErrorCategory.INVALID_INPUT


def retry_recommended_for(code: ErrorCode) -> bool:
    return code in _RETRYABLE_CODES


class PipelineFailure(Exception):
    """Pipeline teknik hatasi (ADR-002 §12).

    `details` contract'ta sinirli bir objedir; sadece field/reason/dependency/
    retryAfterMs/limit anahtarlarina izin verilir. Ham stack trace, provider
    mesaji veya PII TASINMAZ (ADR-002 §12.3).
    """

    def __init__(self, code: ErrorCode, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.category = category_for(code)
        self.message = message
        self.details = details
        # Retry runner tarafindan doldurulur.
        self.attempt_number = 1


class ContractViolation(Exception):
    """Gelen mesaj contract'a uymuyor.

    Contract violation bir business rejection DEGILDIR (ADR-003 §18.2). Uygun
    stable error code tasir; message icine ham veri / PII konmaz.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.category = category_for(code)
        self.message = message
