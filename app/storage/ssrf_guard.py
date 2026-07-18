"""Presigned URL indirmelerinde SSRF korumasi (Berke review #4).

Worker, RabbitMQ mesajindaki `download.url`'i sorgusuz sualsiz indiriyordu.
Broker'a (yetkisiz veya hatali) bir mesaj yazabilen biri, bu URL'i internal
servislere (cloud metadata endpoint, Redis, RabbitMQ management UI, vb.)
istek attirmak icin kullanabilirdi.

Production'da (`APP_ENV=production`):
  - `OBJECT_STORAGE_ALLOWED_HOSTS` bos OLAMAZ (Settings kurulurken fail-fast
    -- bkz. app/config.py `_require_object_storage_allowlist_in_production`).
  - Yalniz `https` kabul edilir.
  - Hostname'in cozunen HICBIR IP'si loopback/link-local/private/reserved
    OLAMAZ (DNS rebinding'e karsi savunma -- allowlist'teki bir hostname
    bile saldirganin kontrolundeki bir DNS kaydiyla internal bir IP'ye
    yonlendirilebilir; bu yuzden hostname allowlist tek basina yeterli
    degildir).

Production disinda (local/staging gelistirme):
  - `OBJECT_STORAGE_ALLOWED_HOSTS` yapilandirilmamissa (bos) kontrol tamamen
    atlanir -- yerel Docker MinIO ve test sunuculari kasitli olarak
    private/loopback IP'de calisir, bu beklenen bir durumdur.
  - Yapilandirilmissa hostname allowlist'i yine de uygulanir (opsiyonel
    sertlestirme, staging'de de acilabilir), ama http'ye ve private IP'ye
    izin verilir.

Her iki modda da: allowlist yapilandirilmissa `object_store.py` her redirect
hedefini AYNI fonksiyondan gecirir -- "redirect'i takip et ama dogrulamayi
atla" acigini kapatir.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.config import Settings
from app.contracts.errors import ErrorCode, PipelineFailure

_ALLOWED_SCHEMES = {"http", "https"}


def _is_production(settings: Settings) -> bool:
    return settings.app_env.strip().lower() == "production"


def _allowed_hosts(settings: Settings) -> set[str]:
    return {h.strip().lower() for h in settings.object_storage_allowed_hosts.split(",") if h.strip()}


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_download_url(url: str, settings: Settings) -> None:
    """`url`'in indirilmeye izinli olup olmadigini dogrular.

    Basarisizlikta stable `INVALID_DOWNLOAD_REFERENCE` ile PipelineFailure
    firlatir (non-retryable -- yeniden denemek referansi duzeltmez). URL/host
    hata mesajina veya details'e YAZILMAZ (ADR-002 §12.3).
    """
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()

    if not hostname or scheme not in _ALLOWED_SCHEMES:
        raise PipelineFailure(
            ErrorCode.INVALID_DOWNLOAD_REFERENCE,
            "download reference URL is malformed or uses an unsupported scheme",
            details={"field": "download.url", "reason": "unsupported scheme"},
        )

    production = _is_production(settings)
    if production and scheme != "https":
        raise PipelineFailure(
            ErrorCode.INVALID_DOWNLOAD_REFERENCE,
            "download reference must use https in production",
            details={"field": "download.url", "reason": "insecure scheme"},
        )

    allowed = _allowed_hosts(settings)
    if not allowed:
        # Production disinda ulasilabilir; production'da Settings kurulurken
        # zaten fail-fast olur (bkz. app/config.py), bu satira hic gelinmez.
        return

    if hostname not in allowed:
        raise PipelineFailure(
            ErrorCode.INVALID_DOWNLOAD_REFERENCE,
            "download reference host is not on the configured allowlist",
            details={"field": "download.url", "reason": "host not allowed"},
        )

    if not production:
        return

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise PipelineFailure(
            ErrorCode.INVALID_DOWNLOAD_REFERENCE,
            "download reference host could not be resolved",
            details={"field": "download.url", "reason": "dns resolution failed"},
        ) from None

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_disallowed_ip(ip):
            raise PipelineFailure(
                ErrorCode.INVALID_DOWNLOAD_REFERENCE,
                "download reference host resolves to a private or reserved IP address",
                details={"field": "download.url", "reason": "private ip address"},
            )
