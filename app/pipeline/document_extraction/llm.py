"""GPT-5.4 ile yapilandirilmis veri cikarimi (ADR-002 SS3.1 "LLM extraction").

Model-native cevap burada tuketilir; canonical'a donusum mapping.py'de yapilir
(ADR-002 SS8.1 "Modelin native cevabi broker uzerinden gonderilmez").

Struktur, OpenAI Structured Outputs (response_format=json_schema, strict) ile
zorlanir: LLM'in kacak alan uretmesi, oneOf/discriminated-union'i bozmasi gibi
riskler mumkun oldugunca API seviyesinde onlenir.
"""
from __future__ import annotations

import json

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
)

from app.config import Settings
from app.contracts.errors import ErrorCode, PipelineFailure

SYSTEM_PROMPT = """You are a contract analysis engine for M4Trust, a B2B deal platform.
Extract structured, factual information from the given contract text. Do not invent
information that is not present in the text. Preserve original-language wording for
legalName, title, and description fields (do not translate). Every extracted item must
reference the page number (marked as [PAGE n] in the source text) where it was found.
Confidence must reflect genuine extraction certainty (0.0-1.0), not always 1.0.
This output is advisory only; a downstream business system independently validates it."""

_RULE_VALUE_TYPES = ["TEXT", "MONEY", "PERCENTAGE", "DURATION_DAYS", "DATE", "BOOLEAN", "QUANTITY"]

_LLM_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "detectedLanguage": {"type": "string", "description": "ISO 639-1 code, e.g. tr, en"},
        "parties": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "role": {"type": "string", "enum": ["BUYER", "SELLER", "OTHER", "UNKNOWN"]},
                    "legalName": {"type": "string"},
                    "legalNameConfidence": {"type": "number"},
                    "taxIdentifier": {"type": ["string", "null"]},
                    "taxIdentifierConfidence": {"type": "number"},
                    "page": {"type": "integer"},
                },
                "required": ["role", "legalName", "legalNameConfidence", "taxIdentifier", "taxIdentifierConfidence", "page"],
            },
        },
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {"type": "string", "enum": ["PAYMENT", "DELIVERY", "QUALITY", "PENALTY", "TERMINATION", "DISPUTE", "OTHER", "UNKNOWN"]},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "valueType": {"type": "string", "enum": _RULE_VALUE_TYPES},
                    "textValue": {"type": ["string", "null"]},
                    "amountMinor": {"type": ["integer", "null"], "description": "Money amount in minor units (e.g. cents/kurus)."},
                    "currency": {"type": ["string", "null"], "description": "ISO 4217, e.g. TRY, EUR, USD."},
                    "basisPoints": {"type": ["integer", "null"], "description": "Percentage in basis points (100 = 1%)."},
                    "durationDays": {"type": ["integer", "null"]},
                    "dateValue": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                    "booleanValue": {"type": ["boolean", "null"]},
                    "quantityValue": {"type": ["number", "null"]},
                    "quantityUnit": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "page": {"type": "integer"},
                },
                "required": [
                    "category", "title", "description", "valueType", "textValue", "amountMinor", "currency",
                    "basisPoints", "durationDays", "dateValue", "booleanValue", "quantityValue", "quantityUnit",
                    "confidence", "page",
                ],
            },
        },
        "deliveryRequirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "evidenceType": {"type": "string", "enum": ["DELIVERY_NOTE", "INVOICE", "VIDEO", "PHOTO", "SIGNED_DOCUMENT", "OTHER", "UNKNOWN"]},
                    "required": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "page": {"type": "integer"},
                },
                "required": ["evidenceType", "required", "confidence", "page"],
            },
        },
        "requiresManualReview": {"type": "boolean"},
        "reviewReasons": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["detectedLanguage", "parties", "rules", "deliveryRequirements", "requiresManualReview", "reviewReasons"],
}


def extract_structured_data(text: str, settings: Settings) -> dict:
    """Sozlesme metnini GPT-5.4'e gonderir, yapilandirilmis JSON dondurur.

    Firlatir: PipelineFailure — provider hatalarinda uygun stable kod ile.
    """
    if not settings.openai_api_key:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "LLM provider is not configured",
            details={"dependency": "openai", "reason": "missing configuration"},
        )

    truncated = text[: settings.openai_max_source_chars]
    client = OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)

    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": truncated},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "m4trust_document_extraction", "strict": True, "schema": _LLM_OUTPUT_SCHEMA},
            },
        )
    except AuthenticationError as exc:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "LLM provider rejected the request credentials",
            details={"dependency": "openai", "reason": "authentication error"},
        ) from exc
    except APITimeoutError as exc:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_TIMEOUT,
            "LLM provider did not respond before the timeout",
            details={"dependency": "openai", "reason": "timeout"},
        ) from exc
    except (APIConnectionError, APIStatusError) as exc:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "LLM provider is temporarily unavailable",
            details={"dependency": "openai", "reason": "connection or server error"},
        ) from exc

    content = response.choices[0].message.content
    if not content:
        raise PipelineFailure(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "LLM provider returned an empty response",
            details={"dependency": "openai", "reason": "empty response"},
        )

    return json.loads(content)
