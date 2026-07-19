"""LLM ciktisini canonical result objesine cevirir (ADR-002 SS8 sonuc contract'i).

Model-native cevap burada tuketilip kaybolur; Spring yalnizca bu fonksiyonun
urettigi canonical yapiyi gorur (ADR-002 SS8.1, SS10). Gecersiz/eksik LLM
alanlari sessizce kabul edilmez; guvenli varsayilanlara duser ve bir warning
uretir (ADR-002 SS13).

Gizlilik notu (ADR-001 SS16): vergi kimlik numaralari bu asamada HER ZAMAN
maskelenir (masked=true, value=null). Ayri, ozel bir PII maskeleme alt sistemi
kurulmadan ham deger canonical ciktiya yazilmaz.
"""
from __future__ import annotations

import re
from datetime import date

from app.pipeline.document_extraction import legal_rag

_LEGAL_BASIS_MIN_SCORE = 0.55  # gercek veriyle kalibre edildi: ilgili kurallar 0.62-0.73, hukuki
# kelime hazinesi tasiyan ama alakasiz metin 0.535 skorluyor -- bu esik onu eler.

# Semadaki (result-payload-1.0.0.schema.json) legalBasis.source kapali enum'uyla
# birebir ayni olmali -- legal_corpus/raw/*.md dosya adlarindan turetiliyor.
_LEGAL_BASIS_SOURCES = {
    "tbk-6098", "kvkk-6698", "odeme-hizmetleri-6493", "aml-5549",
    "odeme-hizmetleri-yonetmelik", "odeme-hizmetleri-tebligi",
}


def _clamp_confidence(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v))


def _clamp_page(value) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, v)


def _source_references(page) -> list[dict]:
    return [{"page": _clamp_page(page)}]


def _map_party(item: dict, index: int) -> dict:
    role = item.get("role") if item.get("role") in {"BUYER", "SELLER", "OTHER", "UNKNOWN"} else "UNKNOWN"
    return {
        "partyReference": f"party-{index + 1}",
        "role": role,
        "legalName": {
            "value": (item.get("legalName") or "UNKNOWN").strip() or "UNKNOWN",
            "confidence": _clamp_confidence(item.get("legalNameConfidence")),
        },
        # Vergi kimlik numarasi bilincli olarak her zaman maskelenir (bkz. modul docstring'i).
        "taxIdentifier": {
            "value": None,
            "masked": True,
            "confidence": _clamp_confidence(item.get("taxIdentifierConfidence")),
        },
        "sourceReferences": _source_references(item.get("page")),
    }


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s.,]+", " ", value).strip().lower()


def _dedupe_parties(parties: list[dict], warnings: list[dict]) -> list[dict]:
    """Ayni rol + normalize edilmis ayni isimdeki taraflari tekillestirir.

    Bulgu (gercek sozlesme testi, 15 Temmuz 2026): ayni sirket belgede
    birden fazla yerde (baslik + imza blogu gibi) hafif farkli
    bicimlendirmeyle (bosluk/nokta farki) gectiginde LLM bunu iki ayri
    taraf olarak cikarabiliyordu. Bu fonksiyon SADECE tam normalize-esit
    isimleri birlestirir; farkli gorunen isimleri riskli tahminle
    birlestirmez. Birincil savunma llm.py SYSTEM_PROMPT'undaki acik
    talimattir; bu ikincil bir guvenlik agidir.
    """
    merged: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for party in parties:
        key = (party["role"], _normalize_name(party["legalName"]["value"]))
        if key not in merged:
            merged[key] = party
            order.append(key)
            continue
        existing = merged[key]
        if party["legalName"]["confidence"] > existing["legalName"]["confidence"]:
            existing["legalName"] = party["legalName"]
        existing_pages = {ref["page"] for ref in existing["sourceReferences"]}
        for ref in party["sourceReferences"]:
            if ref["page"] not in existing_pages:
                existing["sourceReferences"].append(ref)
                existing_pages.add(ref["page"])

    deduped = [merged[key] for key in order]
    if len(deduped) < len(parties):
        warnings.append(
            {
                "code": "DUPLICATE_PARTY_MERGED",
                "message": "The same legal entity was mentioned multiple times and merged into a single party.",
                "severity": "INFO",
                "path": "$.result.parties",
                "details": {
                    "field": "parties",
                    "reason": "duplicate legal entity mentions",
                    "expected": f"{len(parties)} raw mentions",
                    "observed": f"{len(deduped)} unique parties",
                },
            }
        )
    for i, party in enumerate(deduped):
        party["partyReference"] = f"party-{i + 1}"
    return deduped


def _is_valid_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _map_structured_value(item: dict, warnings: list[dict]) -> dict:
    value_type = item.get("valueType")

    if value_type == "MONEY" and isinstance(item.get("amountMinor"), int) and item.get("currency"):
        return {"type": "MONEY", "amountMinor": item["amountMinor"], "currency": str(item["currency"]).upper()[:3]}

    if value_type == "PERCENTAGE" and isinstance(item.get("basisPoints"), int) and 0 <= item["basisPoints"] <= 10000:
        return {"type": "PERCENTAGE", "basisPoints": item["basisPoints"]}

    if value_type == "DURATION_DAYS" and isinstance(item.get("durationDays"), int) and item["durationDays"] >= 0:
        return {"type": "DURATION", "valueSeconds": item["durationDays"] * 86400}

    if value_type == "DATE" and _is_valid_date(item.get("dateValue")):
        return {"type": "DATE", "value": item["dateValue"]}

    if value_type == "BOOLEAN" and isinstance(item.get("booleanValue"), bool):
        return {"type": "BOOLEAN", "value": item["booleanValue"]}

    if value_type == "QUANTITY" and isinstance(item.get("quantityValue"), (int, float)) and item.get("quantityUnit"):
        return {"type": "QUANTITY", "value": float(item["quantityValue"]), "unit": str(item["quantityUnit"])}

    if value_type == "TEXT" and item.get("textValue"):
        return {"type": "TEXT", "value": str(item["textValue"])}

    # Beklenmeyen/eksik degisken kombinasyonu: guvenli TEXT fallback + warning (ADR-002 SS13).
    # `details` kapali bir yapidir (yalniz field/reason/expected/observed); ADR common warning schema.
    warnings.append(
        {
            "code": "STRUCTURED_VALUE_FALLBACK",
            "message": "Rule value did not match its declared type; fell back to TEXT.",
            "severity": "WARNING",
            "path": "$.result.rules",
            "details": {
                "field": "structuredValue",
                "reason": "declared value type fields were missing or invalid",
                "expected": str(value_type) if value_type else "unknown",
                "observed": "TEXT fallback",
            },
        }
    )
    return {"type": "TEXT", "value": str(item.get("description") or item.get("title") or "")}


def _legal_basis_for_rule(title: str, description: str) -> dict | None:
    """Kural metnine (title+description) en ilgili kanun maddesini bulur.

    Legal RAG bir izlenebilirlik artiricidir, mapping'in onkosulu degildir:
    retrieval basarisiz olursa (embedding yoksa, model yuklenemezse) ya da
    yeterince ilgili bir madde bulunamazsa (skor esigin altinda, veya madde
    numarasi olmayan bir baslik-bazli chunk) None doner -- rule yine de
    basariyla eslenir, sadece legalBasis alani hic eklenmez.
    """
    query = f"{title} {description}".strip()
    if not query:
        return None
    try:
        results = legal_rag.retrieve(query, top_k=1)
    except Exception:
        return None
    if not results:
        return None
    top = results[0]
    if top["score"] < _LEGAL_BASIS_MIN_SCORE or "madde_no" not in top:
        return None
    if top["source"] not in _LEGAL_BASIS_SOURCES:
        return None
    return {"source": top["source"], "articleNo": top["madde_no"]}


def _map_rule(item: dict, index: int, warnings: list[dict]) -> dict:
    category = item.get("category") if item.get("category") in {
        "PAYMENT", "DELIVERY", "QUALITY", "PENALTY", "TERMINATION", "DISPUTE", "OTHER", "UNKNOWN"
    } else "UNKNOWN"
    title = (item.get("title") or "Untitled rule").strip() or "Untitled rule"
    description = (item.get("description") or "").strip() or "No description extracted."
    rule = {
        "ruleReference": f"rule-{index + 1}",
        "category": category,
        "title": title,
        "description": description,
        "structuredValue": _map_structured_value(item, warnings),
        "confidence": _clamp_confidence(item.get("confidence")),
        "sourceReferences": _source_references(item.get("page")),
    }
    legal_basis = _legal_basis_for_rule(title, description)
    if legal_basis is not None:
        rule["legalBasis"] = legal_basis
    return rule


def _map_delivery_requirement(item: dict, index: int) -> dict:
    evidence_type = item.get("evidenceType") if item.get("evidenceType") in {
        "DELIVERY_NOTE", "INVOICE", "VIDEO", "PHOTO", "SIGNED_DOCUMENT", "OTHER", "UNKNOWN"
    } else "UNKNOWN"
    return {
        "requirementReference": f"delivery-{index + 1}",
        "evidenceType": evidence_type,
        "required": bool(item.get("required", False)),
        "confidence": _clamp_confidence(item.get("confidence")),
        "sourceReferences": _source_references(item.get("page")),
    }


def map_to_canonical_result(llm_output: dict, *, document: dict) -> tuple[dict, list[dict]]:
    """LLM ciktisini canonical `result` objesine cevirir.

    `document` cagiran taraftan gelir (gercek indirme/hash/tur tespitinden
    uretilmis degerler) — LLM'e guvenilmez.

    Doner: (result, warnings)
    """
    warnings: list[dict] = []

    parties = [_map_party(p, i) for i, p in enumerate(llm_output.get("parties") or [])]
    parties = _dedupe_parties(parties, warnings)
    rules = [_map_rule(r, i, warnings) for i, r in enumerate(llm_output.get("rules") or [])]
    delivery_requirements = [
        _map_delivery_requirement(d, i) for i, d in enumerate(llm_output.get("deliveryRequirements") or [])
    ]

    result = {
        "document": document,
        "parties": parties,
        "rules": rules,
        "deliveryRequirements": delivery_requirements,
        "summary": {
            "requiresManualReview": bool(llm_output.get("requiresManualReview", False)),
            "reviewReasons": [str(r) for r in (llm_output.get("reviewReasons") or [])],
        },
    }
    return result, warnings
