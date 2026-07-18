"""LLM'e gitmeden once PII maskeleme (ADR-001 SS16 - KVKK/gizlilik).

Bulgu (17 Temmuz 2026): ham sozlesme metni (vergi no, TC kimlik no, IBAN, e-posta,
telefon dahil) provider'a (OpenAI) maskesiz gonderiliyordu; maskeleme ancak
modelin cevabindan SONRA, canonical mapping asamasinda (mapping.py) yapiliyordu.
Yani sizinti bizim ciktimizda degil, provider'a giden ham veride olusuyordu.

Bu modul metni LLM'e gitmeden ONCE maskeler. Canonical ciktiyi ETKILEMEZ:
mapping.py zaten vergi kimligini modelin cevabindan bagimsiz olarak her zaman
maskeliyordu (bkz. modul docstring'i), yani modelin bu alanlari hic gormemesi
kaybedilen bir bilgi degildir.

Sirket/kisi adlari MASKELENMEZ -- bunlar canonical ciktida (`legalName`) gerekli
ve KVKK kapsaminda tuzel kisi bilgisi kisisel veri sayilmaz. Kisi adi tespiti
(gercek kisiler icin) guvenilir regex ile yapilamayacagi icin bu surumde
kapsam disidir; gerekirse ayri bir NER tabanli cozum degerlendirilir.

Best-effort'tur: regex tabanli tespit kesin degildir (asiri maskeleme -- ör.
alakasiz bir 10 haneli referans numarasinin yanlislikla maskelenmesi -- guvenli
bir yan hatadir; az maskeleme ise gercek riski dogurur, bu yuzden tercih
asiri maskeleme yonunde yapilmistir).
"""
from __future__ import annotations

import re

_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}\s?(?:[A-Z0-9]\s?){10,30}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b(?:\+90[\s.-]?)?0?5\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}\b")
# TC kimlik no: 11 hane, basi sifir olamaz. Vergi no: 10 hane. `\b` sayesinde
# bir 11 haneli dizinin ic taraflari 10 haneli kaliba yanlislikla uymaz.
_TC_KIMLIK_RE = re.compile(r"\b[1-9]\d{10}\b")
_VERGI_NO_RE = re.compile(r"\b\d{10}\b")

_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_IBAN_RE, "[MASKED_IBAN]"),
    (_EMAIL_RE, "[MASKED_EMAIL]"),
    (_PHONE_RE, "[MASKED_PHONE]"),
    (_TC_KIMLIK_RE, "[MASKED_TC_KIMLIK]"),
    (_VERGI_NO_RE, "[MASKED_TAX_ID]"),
)

PRIVACY_VERSION = "provider-input-masking-1.0.0"


def mask_pii(text: str) -> str:
    """LLM'e gonderilmeden once hassas kimlik/finansal alanlari maskeler."""
    for pattern, placeholder in _MASKS:
        text = pattern.sub(placeholder, text)
    return text
