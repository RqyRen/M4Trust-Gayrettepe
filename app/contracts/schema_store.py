"""Canonical JSON Schema store + validator factory.

Semalar canonical `$id` URL'leriyle ($ref) birbirine baglidir; bu URL'ler
public host edilmez. Butun schema'lar bir store'a yuklenip RefResolver ile
local olarak cozulur (contracts/scripts/validate_contracts.py ile ayni yaklasim).
"""
from __future__ import annotations

import json
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message="jsonschema.RefResolver is deprecated", category=DeprecationWarning)

from jsonschema import Draft202012Validator, FormatChecker, RefResolver

_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
_SCHEMA_ROOT = _CONTRACTS_DIR / "schemas"


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


@lru_cache
def _load_store() -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    """Butun schema'lari yukler.

    Doner: (store, id_to_path) — store hem canonical $id hem file URI -> schema;
    id_to_path canonical $id -> dosya yolu (base_uri icin).
    """
    store: dict[str, dict[str, Any]] = {}
    id_to_path: dict[str, Path] = {}
    for path in sorted(_SCHEMA_ROOT.rglob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        store[_file_uri(path)] = schema
        schema_id = schema.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = schema
            id_to_path[schema_id] = path
    return store, id_to_path


def validator_for_id(schema_id: str) -> Draft202012Validator:
    """Canonical `$id` ile kayitli bir schema icin validator dondurur."""
    store, id_to_path = _load_store()
    if schema_id not in store:
        raise KeyError(f"unknown schema id: {schema_id}")
    schema = store[schema_id]
    resolver = RefResolver(base_uri=_file_uri(id_to_path[schema_id]), referrer=schema, store=store)
    return Draft202012Validator(schema, resolver=resolver, format_checker=FormatChecker())
