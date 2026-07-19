"""Kisa omurlu presigned URL ile kaynak indirme + SHA-256 dogrulama.

ADR sinirlari:
- Raw icerik broker mesajinda TASINMAZ; sadece kisa omurlu download referansi
  gelir ve icerik oradan cekilir (ADR-001 §6, ADR-002 §7.1, §29).
- FastAPI'nin object storage'a genel/sinirsiz bucket erisimi YOKTUR; yalnizca
  request'te gelen presigned URL kullanilir (ADR-001 §6).
- Indirilen icerigin SHA-256 degeri dogrulanir; uyusmazlik RETRY EDILMEZ
  (ADR-002 §7.1) -> CONTENT_HASH_MISMATCH.
- Job bitince local gecici kopya SILINIR (ADR-001 §6, ADR-002 §29).
- URL, credential veya raw icerik LOGLANMAZ (ADR-001 §16, ADR-007 §33).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import get_settings
from app.contracts.errors import ErrorCode, PipelineFailure
from app.storage.ssrf_guard import validate_download_url

_MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


def _parse_utc(value: str) -> datetime:
    # Contract RFC 3339 UTC + trailing Z garanti eder (utc-timestamp schema).
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _ensure_not_expired(expires_at: str, *, now: datetime | None = None) -> None:
    """Presigned URL suresi dolmussa indirmeyi hic denemez.

    NOT (ADR-002 §7.1): ADR suresi dolmus URL'in "teknik olarak retryable kabul
    edilebilecegini" soyler (izin verir, zorunlu kilmaz). Ayni job icinde retry
    ayni URL'i kullanacagi icin suresi dolmus bir referans yeniden denemeyle
    duzelmez; yeni presigned URL'i yalnizca Spring uretebilir. Bu yuzden
    INVALID_DOWNLOAD_REFERENCE (non-retryable) secildi: Spring'e "referans
    gecersiz, yeni job ac" sinyali verir.
    """
    now = now or datetime.now(timezone.utc)
    try:
        expiry = _parse_utc(expires_at)
    except ValueError:
        raise PipelineFailure(
            ErrorCode.INVALID_DOWNLOAD_REFERENCE,
            "download reference expiry is not a valid RFC 3339 UTC timestamp",
            details={"field": "download.expiresAt", "reason": "invalid format"},
        ) from None

    if expiry <= now:
        raise PipelineFailure(
            ErrorCode.INVALID_DOWNLOAD_REFERENCE,
            "download reference expired before processing started",
            details={"field": "download.expiresAt", "reason": "expired"},
        )


def _download_to(path: Path, url: str, *, max_bytes: int) -> tuple[str, int]:
    """Streaming indirme; (sha256_hex, boyut) dondurur. URL loglanmaz.

    Redirect'ler MANUEL takip edilir (bulgu, 18 Temmuz 2026 - Berke review
    #4): httpx'in kendi `follow_redirects=True`'su her hedefi sorgusuz kabul
    ediyordu. Her hop (ilk URL dahil) `validate_download_url` ile ayni
    kontrolden gecer -- "allowlist'teki bir host'a git, oradan internal bir
    IP'ye yonlendirilirsen sessizce takip et" acigini kapatir.
    """
    settings = get_settings()
    digest = hashlib.sha256()
    total = 0
    current_url = url

    for _ in range(_MAX_REDIRECTS + 1):
        validate_download_url(current_url, settings)
        try:
            with httpx.stream(
                "GET", current_url, timeout=settings.download_timeout_seconds, follow_redirects=False
            ) as response:
                if response.status_code in _REDIRECT_STATUS_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise PipelineFailure(
                            ErrorCode.INVALID_DOWNLOAD_REFERENCE,
                            "object storage returned a redirect without a location",
                            details={"field": "download.url", "reason": "missing redirect location"},
                        )
                    current_url = str(response.url.join(location))
                    continue

                if response.status_code >= 500:
                    raise PipelineFailure(
                        ErrorCode.OBJECT_STORAGE_TEMPORARILY_UNAVAILABLE,
                        "object storage returned a server error",
                        details={"dependency": "object-storage", "reason": "server error"},
                    )
                if response.status_code >= 400:
                    # 403/404: referans gecersiz veya yetki yok -> retry cozmez.
                    raise PipelineFailure(
                        ErrorCode.INVALID_DOWNLOAD_REFERENCE,
                        "download reference was rejected by object storage",
                        details={"field": "download.url", "reason": "rejected"},
                    )

                with path.open("wb") as handle:
                    for chunk in response.iter_bytes(settings.download_chunk_bytes):
                        total += len(chunk)
                        if total > max_bytes:
                            raise PipelineFailure(
                                ErrorCode.FILE_TOO_LARGE,
                                "source exceeds the configured maximum size",
                                details={"reason": "max size exceeded", "limit": max_bytes},
                            )
                        digest.update(chunk)
                        handle.write(chunk)
                return digest.hexdigest(), total
        except httpx.TimeoutException:
            raise PipelineFailure(
                ErrorCode.OBJECT_STORAGE_TEMPORARILY_UNAVAILABLE,
                "object storage did not respond before the timeout",
                details={"dependency": "object-storage", "reason": "timeout"},
            ) from None
        except httpx.HTTPError:
            # Ham provider hatasi/URL disari sizdirilmaz (ADR-002 §12.3).
            raise PipelineFailure(
                ErrorCode.OBJECT_STORAGE_TEMPORARILY_UNAVAILABLE,
                "object storage connection failed",
                details={"dependency": "object-storage", "reason": "connection error"},
            ) from None

    raise PipelineFailure(
        ErrorCode.INVALID_DOWNLOAD_REFERENCE,
        "download reference exceeded the maximum number of redirects",
        details={"field": "download.url", "reason": "too many redirects"},
    )


@contextmanager
def fetch_source(source_input: dict, *, max_bytes: int | None = None) -> Iterator[Path]:
    """Request payload'indaki `input`'tan kaynagi indirir ve hash'ini dogrular.

    Context'ten cikilinca local gecici kopya her durumda silinir (ADR-002 §29).

    Firlatir: PipelineFailure — INVALID_DOWNLOAD_REFERENCE / FILE_TOO_LARGE /
    OBJECT_STORAGE_TEMPORARILY_UNAVAILABLE / CONTENT_HASH_MISMATCH
    """
    settings = get_settings()
    limit = max_bytes if max_bytes is not None else settings.download_max_bytes

    download = source_input["download"]
    expected_sha256 = source_input["sha256"].lower()
    expected_size = source_input["sizeBytes"]
    _ensure_not_expired(download["expiresAt"])

    handle, temp_name = tempfile.mkstemp(prefix="m4trust-src-")
    os.close(handle)
    temp_path = Path(temp_name)

    try:
        actual_sha256, size = _download_to(temp_path, download["url"], max_bytes=limit)

        # Berke review #11: input.sizeBytes hic dogrulanmiyordu. SHA-256 zaten
        # tam icerik esitligini garanti eder (boyut da dolayli dogrulanir), ama
        # ayri bir kontrol daha net bir tanisal sinyal verir -- "boyut tutmuyor"
        # ile "hash tutmuyor" ayni ErrorCode'u (CONTENT_HASH_MISMATCH) paylasir,
        # sadece details.reason farklidir (yeni bir contract error code'u
        # gerektirmemek icin bilincli tercih).
        if size != expected_size:
            raise PipelineFailure(
                ErrorCode.CONTENT_HASH_MISMATCH,
                "downloaded content size does not match the declared sizeBytes",
                details={"field": "input.sizeBytes", "reason": "size mismatch"},
            )

        if actual_sha256 != expected_sha256:
            # Hash uyusmazligi retry EDILMEZ (ADR-002 §7.1).
            raise PipelineFailure(
                ErrorCode.CONTENT_HASH_MISMATCH,
                "downloaded content hash does not match the requested sha256",
                details={"field": "input.sha256", "reason": "hash mismatch"},
            )

        yield temp_path
    finally:
        # Job sonunda local gecici kopya silinir (basari/hata fark etmez).
        temp_path.unlink(missing_ok=True)
