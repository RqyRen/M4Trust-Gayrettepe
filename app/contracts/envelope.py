"""Ortak event envelope transport modeli (ADR-002 §6).

Bu bir TRANSPORT DTO'sudur; messaging boundary'de kalir, domain entity olarak
kullanilmaz (ADR-002 §23). Envelope koku ileriye donuk additive alanlara acik
oldugu icin (`additionalProperties: true`) model de extra alanlara izin verir.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Producer(BaseModel):
    model_config = ConfigDict(extra="forbid")  # producer identity strict (contract)
    service: str
    version: str


class EventEnvelope(BaseModel):
    # Bilinmeyen optional alanlari tolere et (ADR-002 §14 consumer davranisi).
    model_config = ConfigDict(extra="allow")

    eventId: str
    eventType: str
    schemaVersion: str
    occurredAt: str
    correlationId: str
    causationId: str | None
    jobId: str
    jobType: str
    tenantId: str
    transactionId: str
    subjectId: str
    idempotencyKey: str
    producer: Producer
    payload: dict
