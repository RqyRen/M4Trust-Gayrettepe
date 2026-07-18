"""RabbitMQ TLS/production güvenliği testleri (Berke review #6).

Gercek TLS handshake + CA dogrulamasi bu dosyada test EDILMEZ -- paylasimli
docker-compose RabbitMQ'su TLS'siz calisir (local dev norm). O kisim manuel
bir canli-kanit scripti ile ozel, TLS-etkin bir RabbitMQ container'ina karsi
ayrica dogrulandi (bkz. oturum notlari). Burada, her ortamda deterministik
calisan iki sey test edilir: production fail-fast kurallari (Settings) ve
`_ssl_options()`'in dogru pika/ssl nesnelerini urettigi (mock'lanmis).
"""
from __future__ import annotations

import ssl

import pika
import pytest

from app.config import Settings
from app.messaging.consumer import _ssl_options


def _settings(**overrides) -> Settings:
    base = {
        "object_storage_allowed_hosts": "objects.m4trust.internal",
        "rabbitmq_user": "ai-worker-prod",
        "rabbitmq_use_tls": True,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --- Production fail-fast (Settings kurulurken) ---

def test_production_rejects_guest_rabbitmq_user() -> None:
    with pytest.raises(Exception):
        _settings(app_env="production", rabbitmq_user="guest")


def test_production_rejects_missing_tls() -> None:
    with pytest.raises(Exception):
        _settings(app_env="production", rabbitmq_use_tls=False)


def test_production_accepts_non_guest_user_with_tls_enabled() -> None:
    _settings(app_env="production")  # firlatmamali


def test_outside_production_guest_and_no_tls_are_still_allowed() -> None:
    # Local dev'in bugunku (degismeyen) davranisi: guest/TLS'siz calismaya devam eder.
    Settings(_env_file=None, app_env="local", rabbitmq_user="guest", rabbitmq_use_tls=False)  # firlatmamali


# --- _ssl_options() (pika/ssl entegrasyonu, mock'lanmis) ---

def test_ssl_options_is_none_when_tls_disabled() -> None:
    settings = Settings(_env_file=None, app_env="local", rabbitmq_use_tls=False)
    assert _ssl_options(settings) is None


def test_ssl_options_uses_system_trust_store_when_ca_path_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}
    original = ssl.create_default_context

    def _spy(*, cafile=None):
        captured["cafile"] = cafile
        return original(cafile=cafile)

    monkeypatch.setattr(ssl, "create_default_context", _spy)
    settings = Settings(_env_file=None, app_env="local", rabbitmq_use_tls=True, rabbitmq_ca_cert_path="")

    result = _ssl_options(settings)

    assert isinstance(result, pika.SSLOptions)
    assert captured["cafile"] is None  # sistem trust store


def test_ssl_options_passes_explicit_ca_cert_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text(_SELF_SIGNED_CA_PEM)
    captured = {}
    original = ssl.create_default_context

    def _spy(*, cafile=None):
        captured["cafile"] = cafile
        return original(cafile=cafile)

    monkeypatch.setattr(ssl, "create_default_context", _spy)
    settings = Settings(_env_file=None, app_env="local", rabbitmq_use_tls=True, rabbitmq_ca_cert_path=str(ca_file))

    _ssl_options(settings)

    assert captured["cafile"] == str(ca_file)


# Sadece `ssl.create_default_context(cafile=...)`'in gecerli bir PEM
# bekledigini dogrulamak icin minimal, gercek bir self-signed CA (test-only,
# hicbir yerde kullanilmaz, `openssl req -x509 -new` ile uretildi).
_SELF_SIGNED_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBfjCCASOgAwIBAgIUNUUW5eemvmGa6hC/D5Gft5NM9B8wCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJdGVzdC1vbmx5MB4XDTI2MDcxODEyMjgyNloXDTM2MDcxNTEy
MjgyNlowFDESMBAGA1UEAwwJdGVzdC1vbmx5MFkwEwYHKoZIzj0CAQYIKoZIzj0D
AQcDQgAE/jukzbtlm50DXrhHjJ6zJmb1T/SNIx8Ml0WrDXZlonl4KPA5RLePpnAF
rwNHkO8WWlWdKS/d+iP6lSf1Oe6YsaNTMFEwHQYDVR0OBBYEFEaQxh1opIxH6RlE
JXPwKUU/QIEuMB8GA1UdIwQYMBaAFEaQxh1opIxH6RlEJXPwKUU/QIEuMA8GA1Ud
EwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDSQAwRgIhAJdBK2ovtiODjV9YZyJOY3Gg
fC9Jfn4exSJCWsJKFpy/AiEA+Jmkw3jQtciBgYzISWcyJIJ8H6w7UrQkgHGn4biO
i7c=
-----END CERTIFICATE-----
"""
