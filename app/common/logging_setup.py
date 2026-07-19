"""Structured JSON logging (ADR-007 §32-33).

Her log satiri stdout'a tek satirlik JSON olarak yazilir; container filesystem'ine
kalici log dosyasi yazilmaz. Sabit alanlar: timestamp, level, service, environment,
version, message. Cagiran taraf `extra={...}` ile ADR-007 §32'de listelenen
context alanlarini (eventId, correlationId, jobId, ...) ekleyebilir.

ADR-007 §33 yasakli veri listesi (parola, session id, credential, ham dokuman/
video icerigi, provider key) formatter tarafindan zorlanamaz -- cagiran taraf
bu alanlari hicbir zaman log mesajina veya extra'ya koymamalidir (mevcut
call site'lar zaten yalniz jobId/code/attempt gibi guvenli alanlar tasir).
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# ADR-007 §32: message flow loglarinda gerektiginde bulunabilecek alanlar.
_CONTEXT_FIELDS = (
    "eventId",
    "correlationId",
    "causationId",
    "jobId",
    "jobType",
    "tenantId",
    "transactionId",
    "subjectId",
    "schemaVersion",
    "pipelineVersion",
    "attemptNumber",
    # Berke review #12 (contract violation / operasyonel sayaclar, app/common/metrics.py):
    "metric",
    "metricValue",
)


class JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str, environment: str, version: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment
        self._version = version

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "service": self._service,
            "environment": self._environment,
            "version": self._version,
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(*, service: str, environment: str, version: str, level: str = "INFO") -> None:
    """Root logger'i stdout'a tek-satir JSON basacak sekilde kurar."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service=service, environment=environment, version=version))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
