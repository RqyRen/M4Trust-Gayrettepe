# M4Trust shared AI contracts

This directory is the initial shared Spring–AI contract foundation for ADR-001 and ADR-002. It defines versioned JSON Schemas, canonical examples, the asynchronous topology, and operational internal API metadata. Spring is the business authority. FastAPI workers consume the request contracts and return canonical M4Trust results; they do not return model-native or provider-specific payloads.

Raw document and video bytes are never carried in RabbitMQ messages. Requests contain a short-lived download reference with a URL and RFC 3339 UTC expiry. Delivery is at-least-once, so `jobId`, `eventId`, `correlationId`, `causationId`, and `idempotencyKey` are explicit and consumers must be idempotent.

## Ownership and review

The M4Trust core/platform team owns the shared envelope, topology, and cross-job contracts. The owning job team proposes job-specific changes. Changes require review from the core/platform owner and the affected Spring and FastAPI owners. Contract changes are reviewed as API changes, including compatibility, fixture updates, and a successful validation run.

## Directory structure

```text
contracts/
  asyncapi/       RabbitMQ topic-exchange topology and message references
  openapi/        Internal operational endpoints only
  schemas/        Draft 2020-12 shared and job-specific schemas
  examples/       Canonical fixtures used by validation and documentation
  scripts/        Lightweight local validation tooling
```

`requirements.txt` contains only the small `jsonschema` and `PyYAML` dependencies needed by the validator. No Spring Boot, FastAPI, broker, registry, code-generation, Docker, Maven, Gradle, or npm runtime integration is part of this foundation.

## Versioning rules

Event names carry the major version: `ai.job.requested.v1`, `ai.job.completed.v1`, `ai.job.failed.v1`, and `ai.job.cancel.requested.v1`. Payload and schema filenames carry semantic versions such as `1.0.0`. The generic envelope accepts semantic-version syntax, but every concrete `*-1.0.0` event schema locks `schemaVersion` to the exact value `1.0.0`; consumers must select schemas by event name and exact schema version.

Closed enum values reject unknown values. Version 1 explicitly defines `UNKNOWN` only where the domain needs an unknown classification. Warning `code` is an open string for forward-compatible advisory additions; warning `severity` remains the closed set `INFO` or `WARNING`.

### Extensibility policy

Selected transport boundaries accept additive optional fields within the same major version (`additionalProperties: true`): the shared event envelope, request-payload roots, request `processing` metadata, result-payload roots, result `result` objects, result summaries, technical metadata, and operational capability/contract metadata response objects. The canonical future-compatible fixture exercises an unknown `payload.result.futureOptionalMetadata` field.

Semantic objects remain strict (`additionalProperties: false`): producer identity, download references, source references, processing attempts, warning details, error details, time ranges, and every discriminated structured-value variant including MONEY, PERCENTAGE, DURATION, DATE, BOOLEAN, and QUANTITY. These objects are closed because an unknown field could change or obscure their meaning. Unknown enum values remain invalid; an explicit `UNKNOWN` value is used only where the domain defines it.

Consumers must ignore unknown optional properties at the selected extensible boundaries, while producers must not repurpose existing fields or change their meaning. This deliberate split preserves additive compatibility without making money, errors, identifiers, or discriminated values ambiguous.

### Canonical schema IDs

Every schema has a globally unique stable `$id` in the non-hosted namespace `https://schemas.m4trust.internal/`. The deterministic convention is:

```text
https://schemas.m4trust.internal/ai/<area>/<schema-name>/1.0.0
```

For example: `https://schemas.m4trust.internal/ai/document-extraction/result-payload/1.0.0`. These URLs are canonical identifiers and do not need to be publicly hosted. The validator rejects missing, malformed, or duplicate IDs and resolves the canonical `$ref` values from the local schema store.

All wire timestamps use RFC 3339 UTC with a trailing uppercase `Z`; `date-time` alone is insufficient because it also permits offsets. Spring and FastAPI runtime serializers must normalize timestamps to UTC and emit `Z`.

Error `category`, `code`, and `retryRecommended` are bound together in a closed discriminated policy. Retryable technical codes require `RETRYABLE_TECHNICAL` and `true`; non-retryable technical and invalid-input codes require their respective category and `false`.

Money uses integer `amountMinor` plus an ISO-style three-letter currency code. Percentages use integer basis points. `requiresManualReview`, video `advisoryOutcome`, warnings, and technical metadata are advisory or descriptive only. Spring owns final business decisions; video output cannot approve delivery, release payment, or resolve disputes.

## Validate locally

From the repository root:

```powershell
python -m pip install -r contracts/requirements.txt
python contracts/scripts/validate_contracts.py
```

The validator checks every JSON Schema with Draft 2020-12 schema checks, resolves local canonical `$ref` references, checks unique IDs and exact event versions, validates every canonical JSON fixture, validates the AsyncAPI/OpenAPI YAML references, and runs expected-invalid checks for schema versions, non-UTC timestamps, error combinations, strict structured values, plus an expected-valid future optional field. It prints JSON paths for failures and exits non-zero on failure.

The same command runs in [GitHub Actions](../.github/workflows/contracts-validation.yml) for pushes to `main` and pull requests targeting `main` when contract or workflow files change.

## Compatibility and consumers

Spring should select a schema by the event name and declared semantic `schemaVersion`, validate before publishing, and remain the authority for job lifecycle and business outcomes. FastAPI should validate the request, download the referenced source, perform internal processing, and publish only the canonical completed or failed event. Generated transport models, if used later, must remain adapters at the messaging boundary; they must not become domain entities because transport compatibility and business identity/lifecycle have different ownership and change rates.

To add a backward-compatible field, add it only at a documented extensible boundary, keep it optional, document its meaning, update the relevant schema and canonical fixture, keep existing required fields and enum meanings unchanged, and verify old consumers can ignore it. If a new value is required for an existing closed enum, prefer a new schema/event major version unless the enum already defines `UNKNOWN` and the semantics truly fit it.

To introduce a new major version, create a new versioned schema set and event names (for example `...v2`), add parallel AsyncAPI references and fixtures, document the migration and coexistence period, and update capabilities. Do not silently change the meaning of a v1 field or routing key.

## JSON Schema semantic limits

JSON Schema validates shape and local constraints. Runtime semantic validation is still required for:

- `endOffset > startOffset` for source references;
- `endMs > startMs` for video time ranges;
- `attemptNumber <= maxAttempts`;
- deadline freshness and job lifecycle/cancellation races;
- UTC serialization, timestamp freshness, and expiry authorization;
- SHA-256 matching the downloaded content;
- URL expiry and authorization; and
- cross-field business consistency, including the fact that advisory AI output cannot authorize a business decision.

## Operational documents

`asyncapi/m4trust-ai-v1.yaml` documents the topic exchanges `m4trust.ai.commands`, `m4trust.ai.events`, and `m4trust.ai.dead-letter`; the agreed queues and routing keys are recorded in both AsyncAPI bindings and the `x-m4trust-topology` summary. `openapi/ai-internal-v1.yaml` exposes only liveness, readiness, capabilities, and contract metadata. It intentionally has no synchronous inference endpoint.
