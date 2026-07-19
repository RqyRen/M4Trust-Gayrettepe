"""Kaynak indirme + hash dogrulama testleri (ADR-001 §6, ADR-002 §7.1, §29).

Gercek HTTP sunucusu uzerinden calisir; hash dogrulama ve gecici dosya
temizligi kritik invariant'lardir.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.config import get_settings
from app.contracts.errors import ErrorCode, PipelineFailure
from app.storage.object_store import fetch_source

_CONTENT = b"M4Trust canonical contract body " * 64
_SHA256 = hashlib.sha256(_CONTENT).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Content-Length", str(len(_CONTENT)))
            self.end_headers()
            self.wfile.write(_CONTENT)
        elif self.path == "/tampered":
            body = b"tampered payload"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/denied":
            self.send_response(403)
            self.end_headers()
        elif self.path == "/boom":
            self.send_response(503)
            self.end_headers()
        elif self.path == "/redirect-same-host":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
        elif self.path == "/redirect-to-metadata-endpoint":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
        elif self.path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:  # test ciktisini kirletme
        return


@pytest.fixture(scope="module")
def base_url() -> str:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _input(url: str, sha256: str = _SHA256, expires_at: str | None = None) -> dict:
    return {
        "documentId": "66666666-6666-4666-8666-666666666666",
        "fileName": "contract.pdf",
        "mediaType": "application/pdf",
        "sizeBytes": len(_CONTENT),
        "sha256": sha256,
        "download": {"url": url, "expiresAt": expires_at or _future()},
    }


def test_download_succeeds_and_hash_matches(base_url: str) -> None:
    with fetch_source(_input(f"{base_url}/ok")) as path:
        assert path.exists()
        assert path.read_bytes() == _CONTENT
        temp_path = path
    # Context'ten cikinca gecici kopya silinir (ADR-002 §29).
    assert not temp_path.exists()


def test_hash_mismatch_is_non_retryable(base_url: str) -> None:
    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/tampered")) as _:
            pass
    assert exc.value.code is ErrorCode.CONTENT_HASH_MISMATCH
    assert exc.value.category.value == "NON_RETRYABLE_TECHNICAL"  # retry edilmez


def test_declared_size_mismatch_is_rejected_before_hash_check(base_url: str) -> None:
    """Berke review #11: input.sizeBytes hic dogrulanmiyordu. Icerik/hash aslinda
    dogru olsa bile (asagida /ok, gercek hash ile), declared sizeBytes yanlissa
    reddedilmeli -- ayri, daha net bir tanisal sinyal (details.reason)."""
    bad_input = _input(f"{base_url}/ok")
    bad_input["sizeBytes"] = len(_CONTENT) + 1
    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(bad_input) as _:
            pass
    assert exc.value.code is ErrorCode.CONTENT_HASH_MISMATCH
    assert exc.value.details["reason"] == "size mismatch"


def test_temp_file_removed_even_on_failure(base_url: str) -> None:
    before = set(Path(__import__("tempfile").gettempdir()).glob("m4trust-src-*"))
    with pytest.raises(PipelineFailure):
        with fetch_source(_input(f"{base_url}/tampered")) as _:
            pass
    after = set(Path(__import__("tempfile").gettempdir()).glob("m4trust-src-*"))
    assert after <= before  # yeni artik dosya birakmadi


def test_expired_reference_rejected_without_download(base_url: str) -> None:
    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/ok", expires_at=_past())) as _:
            pass
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_oversize_source_rejected(base_url: str) -> None:
    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/ok"), max_bytes=16) as _:
            pass
    assert exc.value.code is ErrorCode.FILE_TOO_LARGE


def test_rejected_reference_is_invalid_download_reference(base_url: str) -> None:
    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/denied")) as _:
            pass
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_storage_server_error_is_retryable(base_url: str) -> None:
    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/boom")) as _:
            pass
    assert exc.value.code is ErrorCode.OBJECT_STORAGE_TEMPORARILY_UNAVAILABLE
    assert exc.value.category.value == "RETRYABLE_TECHNICAL"


def test_failure_details_do_not_leak_url(base_url: str) -> None:
    # URL / credential hata mesajina veya details'e sizmamali (ADR-002 §12.3).
    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/denied")) as _:
            pass
    assert base_url not in exc.value.message
    assert base_url not in str(exc.value.details)


# --- SSRF korumasi (Berke review #4) ---


def test_redirect_to_same_host_still_succeeds(base_url: str) -> None:
    # Backward-compat: allowlist yapilandirilmamissa (test ortami varsayilani)
    # ayni host'a redirect eskisi gibi calismaya devam eder.
    with fetch_source(_input(f"{base_url}/redirect-same-host")) as path:
        assert path.read_bytes() == _CONTENT


def test_redirect_loop_is_rejected_without_hanging(base_url: str) -> None:
    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/redirect-loop")) as _:
            pass
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_redirect_to_disallowed_host_is_rejected_when_allowlist_configured(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bulgu (18 Temmuz 2026, Berke review #4): allowlist'teki bir host bile
    saldirganin kontrolundeki bir yanita 302 ile baska bir hosta (ornegin
    cloud metadata endpoint'i) yonlendirebilirdi -- eskiden httpx bunu
    sorgusuz takip ediyordu. Allowlist acikca yapilandirildiginda, her
    redirect hedefi de AYNI kontrolden gecmeli.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_allowed_hosts", "127.0.0.1")

    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/redirect-to-metadata-endpoint")) as _:
            pass
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_disallowed_initial_host_is_rejected_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_allowed_hosts", "objects.m4trust.internal")

    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input("http://169.254.169.254/latest/meta-data/")) as _:
            pass
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_production_rejects_plain_http_even_for_allowed_host(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "object_storage_allowed_hosts", "127.0.0.1")
    monkeypatch.setattr(settings, "app_env", "production")

    with pytest.raises(PipelineFailure) as exc:
        with fetch_source(_input(f"{base_url}/ok")) as _:
            pass
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE
