"""Canonical JSON Schema store + validator factory.

Semalar canonical `$id` URL'leriyle ($ref) birbirine baglidir; bu URL'ler
public host edilmez. Modern `referencing.Registry` API'si kullanilir.

NOT (bulgu, 15 Temmuz 2026): Onceki implementasyon deprecated
`jsonschema.RefResolver` kullaniyordu. Bir instance'ta AYNI array alaninda
(`sourceReferences`) BIRDEN FAZLA eleman oldugunda (ornegin bir party birden
fazla sayfada geciyorsa), RefResolver'in scope stack'i bozuluyor ve sonraki
kardes alanin ($ref: "#/$defs/legalName") cozumlemesi
`_RefResolutionError: Unresolvable JSON pointer` ile BASARISIZ oluyordu -
yani gecerli, schema-uyumlu bir event yanlislikla reddedilebiliyordu. Bu
kutuphane-seviyesi bug, `referencing.Registry`'ye gecilerek giderildi.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
_SCHEMA_ROOT = _CONTRACTS_DIR / "schemas"


@lru_cache
def _load_registry() -> tuple[Registry, dict[str, dict[str, Any]]]:
    """Butun schema'lari yukleyip bir Registry olusturur.

    Doner: (registry, id_to_schema)
    """
    id_to_schema: dict[str, dict[str, Any]] = {}
    resources: list[tuple[str, Resource]] = []
    for path in sorted(_SCHEMA_ROOT.rglob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema_id = schema.get("$id")
        if not isinstance(schema_id, str):
            continue
        id_to_schema[schema_id] = schema
        resources.append((schema_id, Resource.from_contents(schema)))

    registry = Registry().with_resources(resources)
    return registry, id_to_schema


def validator_for_id(schema_id: str) -> Draft202012Validator:
    """Canonical `$id` ile kayitli bir schema icin validator dondurur."""
    registry, id_to_schema = _load_registry()
    if schema_id not in id_to_schema:
        raise KeyError(f"unknown schema id: {schema_id}")
    schema = id_to_schema[schema_id]
    return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
