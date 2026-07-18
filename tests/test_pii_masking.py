"""PII maskeleme testleri (ADR-001 §16, KVKK) — bulgu, 17 Temmuz 2026.

LLM'e gitmeden once hassas kimlik/finansal alanlarin maskelendigini dogrular.
Sirket/kisi adlarinin (canonical ciktida gerekli) maskelenmemesi de test edilir.
"""
from __future__ import annotations

from app.pipeline.document_extraction.pii_masking import mask_pii


def test_turkish_tax_id_is_masked() -> None:
    text = "Satici Vergi No: 1234567890 olarak kayıtlıdır."
    masked = mask_pii(text)
    assert "1234567890" not in masked
    assert "[MASKED_TAX_ID]" in masked


def test_tc_kimlik_no_is_masked() -> None:
    text = "Yetkili kişinin TC Kimlik No: 12345678901"
    masked = mask_pii(text)
    assert "12345678901" not in masked
    assert "[MASKED_TC_KIMLIK]" in masked


def test_email_is_masked() -> None:
    text = "İletişim: satinalma@acme.com adresinden yapılabilir."
    masked = mask_pii(text)
    assert "satinalma@acme.com" not in masked
    assert "[MASKED_EMAIL]" in masked


def test_turkish_mobile_phone_is_masked() -> None:
    text = "Telefon: 0532 123 45 67"
    masked = mask_pii(text)
    assert "532 123 45 67" not in masked
    assert "[MASKED_PHONE]" in masked


def test_iban_is_masked() -> None:
    text = "Ödeme IBAN: TR330006100519786457841326 hesabına yapılacaktır."
    masked = mask_pii(text)
    assert "TR330006100519786457841326" not in masked
    assert "[MASKED_IBAN]" in masked


def test_company_and_person_names_are_not_masked() -> None:
    # Tuzel kisi adlari canonical ciktida gerekli; maskelenmemeli.
    text = "ACME Corp ile Beta Lojistik A.S. arasinda imzalanmistir."
    masked = mask_pii(text)
    assert masked == text


def test_regular_contract_amounts_are_not_falsely_masked() -> None:
    # Ayracli/para birimli tutarlar 10/11 haneli ciplak dizilerle karismamali.
    text = "Sözleşme bedeli 1.250.000 EUR olup 30 gün içinde ödenecektir."
    masked = mask_pii(text)
    assert masked == text
