"""Serbest-metin kisi adi maskeleme testleri (ADR-001 §16, 20 Temmuz 2026).

Gercek NER modelini (transformers, ~saniyeler suren agir bir model yuklemesi)
yuklememek icin `_get_ner_pipeline`'i her testte sahte bir fonksiyonla
degistiriyoruz -- gercek modelin dogru calistigi ayrica manuel/gercek-altyapi
kanitiyla (bkz. session notlari) dogrulandi, bu dosya sadece modulun KENDI
mantigini (token uretimi, tekrar eden isim, geri-donusum, hata toleransi)
test eder.
"""
from __future__ import annotations

import pytest

from app.pipeline.document_extraction import name_masking


def _fake_ner(entities: list[dict]):
    return lambda text: entities


def test_person_name_is_replaced_with_reversible_token(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Bu sozlesme Ahmet Yilmaz tarafindan imzalanmistir."
    start = text.index("Ahmet Yilmaz")
    end = start + len("Ahmet Yilmaz")
    monkeypatch.setattr(
        name_masking,
        "_get_ner_pipeline",
        lambda: _fake_ner([{"entity_group": "PER", "score": 0.95, "start": start, "end": end}]),
    )

    masked, name_map = name_masking.mask_person_names(text)

    assert "Ahmet Yilmaz" not in masked
    assert "[MASKED_PERSON_1]" in masked
    assert name_map == {"[MASKED_PERSON_1]": "Ahmet Yilmaz"}


def test_repeated_name_reuses_the_same_token(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Ahmet Yilmaz imzaladi. Ahmet Yilmaz yetkilidir."
    first = text.index("Ahmet Yilmaz")
    second = text.index("Ahmet Yilmaz", first + 1)
    entities = [
        {"entity_group": "PER", "score": 0.9, "start": first, "end": first + len("Ahmet Yilmaz")},
        {"entity_group": "PER", "score": 0.9, "start": second, "end": second + len("Ahmet Yilmaz")},
    ]
    monkeypatch.setattr(name_masking, "_get_ner_pipeline", lambda: _fake_ner(entities))

    masked, name_map = name_masking.mask_person_names(text)

    assert masked.count("[MASKED_PERSON_1]") == 2
    assert "[MASKED_PERSON_2]" not in masked
    assert name_map == {"[MASKED_PERSON_1]": "Ahmet Yilmaz"}


def test_low_confidence_entities_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Belirsiz bir isim: Zeynep Kaya."
    start = text.index("Zeynep Kaya")
    end = start + len("Zeynep Kaya")
    monkeypatch.setattr(
        name_masking,
        "_get_ner_pipeline",
        lambda: _fake_ner([{"entity_group": "PER", "score": 0.2, "start": start, "end": end}]),
    )

    masked, name_map = name_masking.mask_person_names(text)

    assert masked == text
    assert name_map == {}


def test_non_person_entities_are_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "ACME Corp Istanbul'da kuruludur."
    org_start = text.index("ACME Corp")
    loc_start = text.index("Istanbul")
    monkeypatch.setattr(
        name_masking,
        "_get_ner_pipeline",
        lambda: _fake_ner(
            [
                {"entity_group": "ORG", "score": 0.99, "start": org_start, "end": org_start + len("ACME Corp")},
                {"entity_group": "LOC", "score": 0.99, "start": loc_start, "end": loc_start + len("Istanbul")},
            ]
        ),
    )

    masked, name_map = name_masking.mask_person_names(text)

    assert masked == text
    assert name_map == {}


def test_ner_failure_falls_back_to_unmodified_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken_pipeline():
        raise OSError("model download failed")

    monkeypatch.setattr(name_masking, "_get_ner_pipeline", _broken_pipeline)
    text = "Ahmet Yilmaz tarafindan imzalanmistir."

    masked, name_map = name_masking.mask_person_names(text)

    assert masked == text
    assert name_map == {}


def test_empty_text_is_not_sent_to_ner(monkeypatch: pytest.MonkeyPatch) -> None:
    def _should_not_be_called():
        raise AssertionError("NER should not be invoked for empty text")

    monkeypatch.setattr(name_masking, "_get_ner_pipeline", _should_not_be_called)

    masked, name_map = name_masking.mask_person_names("   ")

    assert masked == "   "
    assert name_map == {}


def test_restore_person_names_replaces_token_in_nested_structure() -> None:
    name_map = {"[MASKED_PERSON_1]": "Ahmet Yilmaz"}
    value = {
        "parties": [
            {"role": "SELLER", "legalName": "[MASKED_PERSON_1]"},
            {"role": "BUYER", "legalName": "ACME Corp"},
        ],
        "notes": ["Signed by [MASKED_PERSON_1] on behalf of the seller."],
        "confidence": 0.9,
        "requiresManualReview": False,
    }

    restored = name_masking.restore_person_names(value, name_map)

    assert restored["parties"][0]["legalName"] == "Ahmet Yilmaz"
    assert restored["parties"][1]["legalName"] == "ACME Corp"
    assert restored["notes"][0] == "Signed by Ahmet Yilmaz on behalf of the seller."
    assert restored["confidence"] == 0.9
    assert restored["requiresManualReview"] is False


def test_restore_person_names_with_empty_map_returns_value_unchanged() -> None:
    value = {"legalName": "[MASKED_PERSON_1]"}
    assert name_masking.restore_person_names(value, {}) is value
