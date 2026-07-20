"""Serbest-metin kisi adi maskeleme -- geri-donusturulebilir token ile (ADR-001 SS16).

`pii_masking.py` yalniz YAPISAL alanlari (vergi no, TC kimlik, IBAN vb.) maskeler;
serbest-metinde gecen gercek kisi adlari (ornegin sozlesmeyi imzalayan bir sahis
tarafin adi) o modulun kapsami disindaydi -- guvenilir regex ile tespit
edilemeyecegi icin.

Bu modul yerel bir NER (Named Entity Recognition) modeliyle (savasy/bert-base-
turkish-ner-cased, transformers uzerinden -- ekstra API maliyeti YOK, BGE-M3 ile
ayni "yerel, ucretsiz model" deseni) tespit edilen kisi adlarini LLM'e gitmeden
once TERSINE-CEVRILEBILIR bir token ile degistirir.

Neden geri-donusturulebilir token (basit "[MASKED_PERSON]" degil): sozlesmenin
tarafi bazen gercek bir sahis olabiliyor (sirket degil) -- o durumda
`parties[].legalName` alanina TAM OLARAK o kisinin adi cikarilmasi gerekiyor.
Tum isimleri tek bir sabit placeholder'la maskelersek LLM o tarafin adini hic
cikaramaz. Bunun yerine:
  1. Her benzersiz isim kendi tekil token'ini alir ([MASKED_PERSON_1], _2, ...)
     ve bir eslesme (`name_map`: token -> orijinal ad) dondurulur.
  2. LLM SADECE token'li metni gorur (ADR-001 SS16 karsilanir -- ham PII ucuncu
     taraf modele hic gitmez).
  3. LLM'in JSON ciktisinda (`legalName` gibi) bu token'i AYNEN kopyaladigi her
     yerde, `restore_person_names()` ile gercek isim geri konur -- taraf-adi
     cikarimi bozulmaz.
  4. LLM'in ciktisinda hic KULLANMADIGI (yani cikarima konu olmayan, tesadufi/
     yan bahsi gecen) isimler otomatik olarak token halinde kalir gizli --
     hicbir yerde gorunmez, cunku ciktida hic referans edilmiyorlar. Ayrica bir
     "bu isim taraf mi degil mi" siniflandirmasi yapmaya GEREK YOK.

Sirket/tuzel kisi adlari maskelenMEZ (KVKK kapsaminda kisisel veri sayilmaz,
canonical ciktida gerekli) -- ama model bir sirket adini yanlislikla PERSON
etiketlerse bile (yanlis pozitif), token geri-donusturulebilir oldugu icin
cikarim SONUCUNU bozmaz, sadece bosuna bir token/geri-donusum islemi olur.

Best-effort'tur (pii_masking.py'nin ayni felsefesi): NER modeli yuklenemezse
veya calisirken hata verirse, metin DEGISTIRILMEDEN dondurulur -- bu ozellik
hicbir zaman extraction'i kirmamalidir (legal_rag.py'deki retrieval-basarisizligi-
kirmiyor deseniyle ayni).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MODEL_NAME = "savasy/bert-base-turkish-ner-cased"
_MIN_SCORE = 0.5
_PERSON_ENTITY_GROUP = "PER"

_ner_pipeline = None


def _get_ner_pipeline():
    global _ner_pipeline
    if _ner_pipeline is None:
        from transformers import pipeline as hf_pipeline

        _ner_pipeline = hf_pipeline("ner", model=_MODEL_NAME, aggregation_strategy="simple")
    return _ner_pipeline


def mask_person_names(text: str) -> tuple[str, dict[str, str]]:
    """Metindeki kisi adlarini token'larla degistirir; (masked_text, name_map) dondurur.

    `name_map`: token -> orijinal ad. Bos donerse (NER basarisiz veya hic isim
    bulunamadiysa) metin degistirilmeden geri gelir.
    """
    if not text or not text.strip():
        return text, {}

    try:
        ner = _get_ner_pipeline()
        entities = ner(text)
    except Exception:
        logger.warning("person name NER failed, continuing without name masking", exc_info=True)
        return text, {}

    person_spans = [
        e
        for e in entities
        if e.get("entity_group") == _PERSON_ENTITY_GROUP and e.get("score", 0.0) >= _MIN_SCORE
    ]
    if not person_spans:
        return text, {}

    name_map: dict[str, str] = {}
    token_by_normalized_name: dict[str, str] = {}
    replacements: list[tuple[int, int, str]] = []

    for entity in person_spans:
        start, end = int(entity["start"]), int(entity["end"])
        raw_name = text[start:end].strip()
        if not raw_name:
            continue
        normalized = " ".join(raw_name.lower().split())
        token = token_by_normalized_name.get(normalized)
        if token is None:
            token = f"[MASKED_PERSON_{len(token_by_normalized_name) + 1}]"
            token_by_normalized_name[normalized] = token
            name_map[token] = raw_name
        replacements.append((start, end, token))

    if not replacements:
        return text, {}

    masked = text
    for start, end, token in sorted(replacements, key=lambda r: r[0], reverse=True):
        masked = masked[:start] + token + masked[end:]
    return masked, name_map


def restore_person_names(value, name_map: dict[str, str]):
    """LLM ciktisindaki (dict/list/str, ic ice) token'lari gercek isimlerle degistirir.

    `name_map` bos ise `value` degistirilmeden dondurulur (yaygin durum -- cogu
    dokumanda maskelenecek isim bulunmaz).
    """
    if not name_map:
        return value
    if isinstance(value, str):
        for token, original in name_map.items():
            if token in value:
                value = value.replace(token, original)
        return value
    if isinstance(value, dict):
        return {key: restore_person_names(item, name_map) for key, item in value.items()}
    if isinstance(value, list):
        return [restore_person_names(item, name_map) for item in value]
    return value
