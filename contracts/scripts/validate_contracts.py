#!/usr/bin/env python3
"""Lightweight validation for the M4Trust contract foundation."""

from __future__ import annotations

import copy
import json
import re
import sys
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message="jsonschema.RefResolver is deprecated", category=DeprecationWarning)

try:
    import yaml
    from jsonschema import Draft202012Validator, FormatChecker, RefResolver
except ImportError as exc:  # pragma: no cover - exercised by the CLI error path
    print("Missing dependency: install contracts/requirements.txt (jsonschema, PyYAML).", file=sys.stderr)
    raise SystemExit(2) from exc


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"
SCHEMA_ID_NAMESPACE = "https://schemas.m4trust.internal/"

FIXTURE_SCHEMAS = {
    "examples/document-extraction/minimum-request.json": "schemas/document-extraction/requested-event-1.0.0.schema.json",
    "examples/document-extraction/full-request.json": "schemas/document-extraction/requested-event-1.0.0.schema.json",
    "examples/document-extraction/success-result.json": "schemas/document-extraction/completed-event-1.0.0.schema.json",
    "examples/document-extraction/warning-result.json": "schemas/document-extraction/completed-event-1.0.0.schema.json",
    "examples/document-extraction/retryable-failure.json": "schemas/document-extraction/failed-event-1.0.0.schema.json",
    "examples/document-extraction/non-retryable-failure.json": "schemas/document-extraction/failed-event-1.0.0.schema.json",
    "examples/video-analysis/minimum-request.json": "schemas/video-analysis/requested-event-1.0.0.schema.json",
    "examples/video-analysis/full-request.json": "schemas/video-analysis/requested-event-1.0.0.schema.json",
    "examples/video-analysis/success-result.json": "schemas/video-analysis/completed-event-1.0.0.schema.json",
    "examples/video-analysis/warning-result.json": "schemas/video-analysis/completed-event-1.0.0.schema.json",
    "examples/video-analysis/retryable-failure.json": "schemas/video-analysis/failed-event-1.0.0.schema.json",
    "examples/video-analysis/non-retryable-failure.json": "schemas/video-analysis/failed-event-1.0.0.schema.json",
    "examples/job/cancel-request.json": "schemas/job/cancel-requested-event-1.0.0.schema.json",
}

CONCRETE_EVENT_SCHEMAS = [
    "schemas/document-extraction/requested-event-1.0.0.schema.json",
    "schemas/document-extraction/completed-event-1.0.0.schema.json",
    "schemas/document-extraction/failed-event-1.0.0.schema.json",
    "schemas/video-analysis/requested-event-1.0.0.schema.json",
    "schemas/video-analysis/completed-event-1.0.0.schema.json",
    "schemas/video-analysis/failed-event-1.0.0.schema.json",
    "schemas/job/cancel-requested-event-1.0.0.schema.json",
]

EXPECTED_CHANNELS = {
    "ai.document-extraction.requested.v1",
    "ai.video-analysis.requested.v1",
    "ai.job.cancel.requested.v1",
    "ai.document-extraction.completed.v1",
    "ai.document-extraction.failed.v1",
    "ai.video-analysis.completed.v1",
    "ai.video-analysis.failed.v1",
}
EXPECTED_OPENAPI_PATHS = {"/health/live", "/health/ready", "/internal/v1/capabilities", "/internal/v1/contracts"}


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    return value


def file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def json_path(path: Any) -> str:
    parts = [str(part) for part in path]
    return ".".join(parts) if parts else "<root>"


def find_duplicate_ids(schema_paths: list[Path], schemas: dict[Path, dict[str, Any]]) -> dict[str, list[str]]:
    locations: dict[str, list[str]] = {}
    for path in schema_paths:
        schema_id = schemas[path].get("$id")
        if isinstance(schema_id, str):
            try:
                display_path = str(path.relative_to(ROOT))
            except ValueError:
                display_path = str(path)
            locations.setdefault(schema_id, []).append(display_path)
    return {schema_id: paths for schema_id, paths in locations.items() if len(paths) > 1}


def load_schemas(schema_paths: list[Path]) -> tuple[dict[Path, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    schemas: dict[Path, dict[str, Any]] = {}
    store: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for path in schema_paths:
        try:
            schema = read_json(path)
            schemas[path] = schema
            store[file_uri(path)] = schema
            schema_id = schema.get("$id")
            if isinstance(schema_id, str):
                store[schema_id] = schema
            else:
                failures.append(f"FAIL schema {path.relative_to(ROOT)}: missing string $id")
        except Exception as exc:  # noqa: BLE001 - this is a CLI validator
            failures.append(f"FAIL schema {path.relative_to(ROOT)}: {exc}")
    return schemas, store, failures


def validator_for(schema_path: Path, schema: dict[str, Any], store: dict[str, dict[str, Any]]) -> Draft202012Validator:
    resolver = RefResolver(base_uri=file_uri(schema_path), referrer=schema, store=store)
    return Draft202012Validator(schema, resolver=resolver, format_checker=FormatChecker())


def errors_for(schema_path: Path, schema: dict[str, Any], instance: dict[str, Any], store: dict[str, dict[str, Any]]) -> list[Any]:
    validator = validator_for(schema_path, schema, store)
    return sorted(validator.iter_errors(instance), key=lambda error: list(error.path))


def format_errors(errors: list[Any]) -> str:
    return "; ".join(f"{json_path(error.path)}: {error.message}" for error in errors)


def expect_invalid(label: str, schema_path: Path, instance: dict[str, Any], store: dict[str, dict[str, Any]], failures: list[str]) -> None:
    errors = errors_for(schema_path, read_json(schema_path), instance, store)
    if not errors:
        failures.append(f"FAIL expected-invalid {label}: instance unexpectedly passed")
    else:
        print(f"PASS expected-invalid {label}: {format_errors(errors)}")


def expect_valid(label: str, schema_path: Path, instance: dict[str, Any], store: dict[str, dict[str, Any]], failures: list[str]) -> None:
    errors = errors_for(schema_path, read_json(schema_path), instance, store)
    if errors:
        failures.append(f"FAIL expected-valid {label}: {format_errors(errors)}")
    else:
        print(f"PASS expected-valid {label}")


def event_properties(schema: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for branch in schema.get("allOf", []):
        if isinstance(branch, dict):
            properties.update(branch.get("properties", {}))
    return properties


def validate_contract_documents(failures: list[str]) -> None:
    asyncapi_path = ROOT / "asyncapi/m4trust-ai-v1.yaml"
    openapi_path = ROOT / "openapi/ai-internal-v1.yaml"
    try:
        asyncapi = yaml.safe_load(asyncapi_path.read_text(encoding="utf-8"))
        openapi = yaml.safe_load(openapi_path.read_text(encoding="utf-8"))
        if set(asyncapi.get("channels", {})) != EXPECTED_CHANNELS:
            failures.append("FAIL AsyncAPI channels: accepted routing-key set changed")
        if set(openapi.get("paths", {})) != EXPECTED_OPENAPI_PATHS:
            failures.append("FAIL OpenAPI paths: operational endpoint set changed")
        for message in asyncapi.get("components", {}).get("messages", {}).values():
            reference = message.get("payload", {}).get("$ref")
            if isinstance(reference, str) and reference.startswith("../"):
                target = (asyncapi_path.parent / reference).resolve()
                if not target.exists():
                    failures.append(f"FAIL AsyncAPI reference {reference}: file does not exist")
        if not failures:
            print("PASS AsyncAPI/OpenAPI YAML, paths, channels, and external references")
    except Exception as exc:  # noqa: BLE001 - this is a CLI validator
        failures.append(f"FAIL API contract documents: {exc}")


def validate() -> int:
    schema_paths = sorted(SCHEMA_ROOT.rglob("*.schema.json"))
    failures: list[str] = []
    schemas, store, load_failures = load_schemas(schema_paths)
    failures.extend(load_failures)

    duplicate_ids = find_duplicate_ids(schema_paths, schemas)
    if duplicate_ids:
        for schema_id, paths in duplicate_ids.items():
            failures.append(f"FAIL duplicate schema $id {schema_id}: {', '.join(paths)}")
    else:
        print("PASS duplicate schema IDs: none")

    id_pattern = re.compile(r"^https://schemas\.m4trust\.internal/ai(?:/[a-z0-9-]+)+/1\.0\.0$")
    for path in schema_paths:
        schema = schemas.get(path)
        if schema is None:
            continue
        try:
            Draft202012Validator.check_schema(schema)
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str) or not schema_id.startswith(SCHEMA_ID_NAMESPACE) or not id_pattern.match(schema_id):
                failures.append(f"FAIL schema {path.relative_to(ROOT)}: $id must use the canonical versioned namespace")
            else:
                print(f"PASS schema {path.relative_to(ROOT)}")
        except Exception as exc:  # noqa: BLE001 - this is a CLI validator
            failures.append(f"FAIL schema {path.relative_to(ROOT)}: {exc}")

    for schema_name in CONCRETE_EVENT_SCHEMAS:
        path = ROOT / schema_name
        schema = schemas.get(path)
        if schema is None:
            failures.append(f"FAIL event schema {schema_name}: schema was not loaded")
            continue
        properties = event_properties(schema)
        required = set()
        for branch in schema.get("allOf", []):
            if isinstance(branch, dict):
                required.update(branch.get("required", []))
        for field in ("eventType", "jobType", "schemaVersion"):
            if field not in properties or field not in required:
                failures.append(f"FAIL event schema {schema_name}: {field} is not constrained and required")
        if properties.get("eventType", {}).get("const") is None:
            failures.append(f"FAIL event schema {schema_name}: eventType must use const")
        if properties.get("schemaVersion", {}).get("const") != "1.0.0":
            failures.append(f"FAIL event schema {schema_name}: schemaVersion must be const 1.0.0")
    if not failures:
        print("PASS concrete event schemaVersion/eventType/jobType constraints")

    validate_contract_documents(failures)

    for fixture_name, schema_name in FIXTURE_SCHEMAS.items():
        fixture_path = ROOT / fixture_name
        schema_path = ROOT / schema_name
        try:
            instance = read_json(fixture_path)
            errors = errors_for(schema_path, schemas[schema_path], instance, store)
            if errors:
                failures.append(f"FAIL fixture {fixture_path.relative_to(ROOT)}: {format_errors(errors)}")
            else:
                print(f"PASS valid fixture {fixture_path.relative_to(ROOT)}")
        except Exception as exc:  # noqa: BLE001 - this is a CLI validator
            failures.append(f"FAIL fixture {fixture_path.relative_to(ROOT)}: {exc}")

    document_request_path = ROOT / "schemas/document-extraction/requested-event-1.0.0.schema.json"
    document_completed_path = ROOT / "schemas/document-extraction/completed-event-1.0.0.schema.json"
    document_failed_path = ROOT / "schemas/document-extraction/failed-event-1.0.0.schema.json"
    request = read_json(ROOT / "examples/document-extraction/minimum-request.json")
    invalid_version = copy.deepcopy(request)
    invalid_version["schemaVersion"] = "1.1.0"
    expect_invalid("schemaVersion 1.1.0 against requested-event-1.0.0", document_request_path, invalid_version, store, failures)

    non_utc = copy.deepcopy(request)
    non_utc["occurredAt"] = "2026-07-13T15:00:00+03:00"
    expect_invalid("non-UTC occurredAt offset", document_request_path, non_utc, store, failures)

    retryable_failure_path = ROOT / "schemas/document-extraction/failed-event-1.0.0.schema.json"
    retryable = read_json(ROOT / "examples/document-extraction/retryable-failure.json")
    invalid_category = copy.deepcopy(retryable)
    invalid_category["payload"]["error"]["category"] = "INVALID_INPUT"
    expect_invalid("INVALID_INPUT with MODEL_PROVIDER_TIMEOUT", retryable_failure_path, invalid_category, store, failures)
    invalid_retry_flag = copy.deepcopy(retryable)
    invalid_retry_flag["payload"]["error"]["retryRecommended"] = False
    expect_invalid("RETRYABLE_TECHNICAL with retryRecommended false", retryable_failure_path, invalid_retry_flag, store, failures)
    invalid_non_retry = read_json(ROOT / "examples/document-extraction/non-retryable-failure.json")
    invalid_non_retry["payload"]["error"]["retryRecommended"] = True
    expect_invalid("NON_RETRYABLE_TECHNICAL with retryRecommended true", retryable_failure_path, invalid_non_retry, store, failures)

    completed = read_json(ROOT / "examples/document-extraction/success-result.json")
    future_optional = copy.deepcopy(completed)
    future_optional["payload"]["result"]["summary"]["futureOptionalMetadata"] = {"value": "ignored by older consumers"}
    expect_valid("future optional document summary metadata", document_completed_path, future_optional, store, failures)

    video_completed_path = ROOT / "schemas/video-analysis/completed-event-1.0.0.schema.json"
    video_completed = read_json(ROOT / "examples/video-analysis/success-result.json")
    video_future_optional = copy.deepcopy(video_completed)
    video_future_optional["payload"]["result"]["summary"]["futureOptionalMetadata"] = {"value": "ignored by older consumers"}
    expect_valid("future optional video summary metadata", video_completed_path, video_future_optional, store, failures)

    strict_value = copy.deepcopy(completed)
    strict_value["payload"]["result"]["rules"][0]["structuredValue"]["futureField"] = "must be rejected"
    expect_invalid("unknown property in closed structured-value variant", document_completed_path, strict_value, store, failures)

    if find_duplicate_ids([Path("one.json"), Path("two.json")], {Path("one.json"): {"$id": "duplicate"}, Path("two.json"): {"$id": "duplicate"}}):
        print("PASS duplicate schema ID detector negative case")
    else:
        failures.append("FAIL duplicate schema ID detector negative case: duplicate was not detected")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        print(f"Validation failed: {len(failures)} problem(s).", file=sys.stderr)
        return 1

    print(f"Validation succeeded: {len(schema_paths)} schemas and {len(FIXTURE_SCHEMAS)} valid fixtures; expected-invalid checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(validate())
