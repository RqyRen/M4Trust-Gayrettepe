"""`validate_download_url` testleri (Berke review #4: SSRF korumasi).

Gercek DNS/network'e bagimli olmamasi icin `socket.getaddrinfo` gerekli
yerlerde monkeypatch'lenir (`localhost` haric -- bu her zaman yerel/aninda
cozunur, gercek bir network hop'u degildir).
"""
from __future__ import annotations

import pytest

import app.storage.ssrf_guard as guard
from app.config import Settings
from app.contracts.errors import ErrorCode, PipelineFailure
from app.storage.ssrf_guard import validate_download_url


def _settings(**overrides) -> Settings:
    # RabbitMQ production fail-fast (Berke review #6, app/config.py) tetiklenmesin
    # diye taban degerler -- bu dosyanin konusu SSRF, RabbitMQ degil.
    base = {"rabbitmq_user": "ai-worker-prod", "rabbitmq_use_tls": True}
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --- Production disinda (varsayilan local/staging gelistirme) ---

def test_unconfigured_allowlist_permits_anything_outside_production() -> None:
    settings = _settings(app_env="local", object_storage_allowed_hosts="")
    validate_download_url("http://169.254.169.254/latest/meta-data/", settings)  # firlatmamali


def test_configured_allowlist_rejects_non_allowed_host_outside_production() -> None:
    settings = _settings(app_env="local", object_storage_allowed_hosts="minio.internal")
    with pytest.raises(PipelineFailure) as exc:
        validate_download_url("http://evil.example/x", settings)
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_configured_allowlist_permits_http_and_private_ip_outside_production() -> None:
    # Yerel Docker MinIO / test sunuculari kasitli olarak private/loopback'tir.
    settings = _settings(app_env="local", object_storage_allowed_hosts="127.0.0.1")
    validate_download_url("http://127.0.0.1:9000/bucket/key", settings)  # firlatmamali


def test_unsupported_scheme_is_always_rejected() -> None:
    settings = _settings(app_env="local", object_storage_allowed_hosts="")
    with pytest.raises(PipelineFailure) as exc:
        validate_download_url("file:///etc/passwd", settings)
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_hostname_matching_is_case_insensitive() -> None:
    settings = _settings(app_env="local", object_storage_allowed_hosts="Objects.Example.Com")
    validate_download_url("http://objects.example.com/bucket/key", settings)  # firlatmamali


# --- Production ---

def test_production_requires_https() -> None:
    settings = _settings(app_env="production", object_storage_allowed_hosts="objects.m4trust.internal")
    with pytest.raises(PipelineFailure) as exc:
        validate_download_url("http://objects.m4trust.internal/bucket/key", settings)
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_production_rejects_host_not_on_allowlist() -> None:
    settings = _settings(app_env="production", object_storage_allowed_hosts="objects.m4trust.internal")
    with pytest.raises(PipelineFailure) as exc:
        validate_download_url("https://attacker.example/bucket/key", settings)
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_production_rejects_allowed_host_resolving_to_loopback() -> None:
    # DNS rebinding senaryosu: hostname allowlist'te olsa bile cozunen IP
    # loopback/private ise reddedilmeli -- allowlist tek basina yeterli degil.
    settings = _settings(app_env="production", object_storage_allowed_hosts="localhost")
    with pytest.raises(PipelineFailure) as exc:
        validate_download_url("https://localhost/bucket/key", settings)
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_production_rejects_allowed_host_resolving_to_link_local_metadata_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    # Cloud metadata endpoint'i (169.254.169.254) -- allowlist DNS rebinding
    # ile buraya yonlendirilse bile reddedilmeli.
    monkeypatch.setattr(guard.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))])
    settings = _settings(app_env="production", object_storage_allowed_hosts="objects.m4trust.internal")
    with pytest.raises(PipelineFailure) as exc:
        validate_download_url("https://objects.m4trust.internal/bucket/key", settings)
    assert exc.value.code is ErrorCode.INVALID_DOWNLOAD_REFERENCE


def test_production_accepts_https_allowed_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))])
    settings = _settings(app_env="production", object_storage_allowed_hosts="objects.m4trust.internal")
    validate_download_url("https://objects.m4trust.internal/bucket/key", settings)  # firlatmamali


def test_empty_allowlist_in_production_fails_fast_at_settings_construction() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError, Settings kurulamaz
        _settings(app_env="production", object_storage_allowed_hosts="")


def test_details_never_include_the_raw_host() -> None:
    settings = _settings(app_env="production", object_storage_allowed_hosts="objects.m4trust.internal")
    with pytest.raises(PipelineFailure) as exc:
        validate_download_url("https://attacker.example/secret-path", settings)
    assert "attacker.example" not in exc.value.message
    assert "attacker.example" not in str(exc.value.details)
