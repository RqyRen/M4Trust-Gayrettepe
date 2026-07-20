# M4Trust contracts

This directory contains the reviewed public Spring–frontend design contract and the shared Spring–AI contract foundation. The public contract is designed slice by slice before implementation or frontend generation. The AI contracts define versioned JSON Schemas, canonical examples, the asynchronous topology, and operational internal API metadata. Spring remains the business authority. FastAPI workers consume request contracts and return canonical M4Trust results; they do not return model-native or provider-specific payloads.

The envelope field `transactionId` identifies the owning `Deal` aggregate (ADR-003). The v1 name is retained for wire compatibility; renaming it to `dealId` is a v2 candidate, and Spring maps it at the integration boundary. Canonical `producer.service` values follow the ADR-007 service names (`m4trust-core-api`, `m4trust-ai-worker`).

Raw document and video bytes are never carried in RabbitMQ messages. Requests contain a short-lived download reference with a URL and RFC 3339 UTC expiry. Delivery is at-least-once, so `jobId`, `eventId`, `correlationId`, `causationId`, and `idempotencyKey` are explicit and consumers must be idempotent.

## Ownership and review

The M4Trust core/platform team owns the public Core API design contract, shared envelope, topology, and cross-job contracts. Public API proposals require review from the Core API and affected frontend owners before implementation or client generation. The owning AI job team proposes job-specific changes, reviewed by the core/platform owner and affected Spring and FastAPI owners. Contract changes are reviewed as API changes, including compatibility and a successful validation run; fixtures are updated when an executable example surface changes.

## Directory structure

```text
contracts/
  asyncapi/       RabbitMQ topic-exchange topology and message references
  openapi/        Public Core API design contract and AI-internal operational API
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

The validator checks every JSON Schema with Draft 2020-12 schema checks, resolves local canonical `$ref` references, checks unique IDs and exact event versions, validates every canonical JSON fixture, validates the AsyncAPI/OpenAPI YAML references and path invariants, requires the public error components, and runs expected-invalid checks for schema versions, non-UTC timestamps, error combinations, strict structured values, plus an expected-valid future optional field. It prints JSON paths for failures and exits non-zero on failure.

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

## OpenAPI documents

`openapi/core-api-v1.yaml` is the reviewed OpenAPI 3.1 design contract for the same-origin public Spring API consumed by the frontend. It contains the reusable Problem Details foundation, Slice 1 authentication, Slice 2 legal entity and membership, Slice 3 Deal create/list/detail/update/cancel, Slice 4 invitation and cross-entity participation, Slice 5 atomic buyer/seller party-management, Slice 6 direct document-upload surfaces, Slice 8 asynchronous current-document analysis, Slice 9 manual review and immutable rule-set surfaces, Slice 10 ratification, and Slice 11 provider-independent sandbox funding, all designed before implementation. Slice 6 creates a JSON upload intent, transfers PDF/DOCX bytes directly between the browser and private object storage, and finalizes only independently verified storage metadata. It exposes retained PENDING_UPLOAD/AVAILABLE/SUPERSEDED history, opaque immutable object versions, and short-lived direct download links; it never exposes a Spring binary proxy or a permanent storage URL. Slice 8 adds an initiator-only, idempotent analysis request action and participant-readable status/result projection for the current AVAILABLE document. Slice 9 exposes the participant-readable current review input, an initiator-only idempotent bulk acceptance action with a target analysis and Deal expectedVersion, plus immutable rule-set history and detail reads. Acceptance atomically creates a RuleSetVersion and changes the analysis to ACCEPTED; it is the only public gateway from advisory AI extraction to business rules, but is neither ratification nor commercial consent. Slice 10 adds initiator-only package creation with explicit exact positive I-JSON-safe `amountMinor` and uppercase ISO 4217 `currency`, participant-readable package detail/history, and buyer/seller ADMIN approve/reject actions with a target package version and Idempotency-Key. MONEY rules are suggestions only: the API never selects commercial terms silently. The second required approval atomically ratifies the package and activates the Deal.

Slice 10 canonicalization is intentionally narrow. `RatificationPackageSnapshot` is a dedicated closed immutable schema and the sole `contentHash` input. Its JSON is RFC 8785 (JCS) canonicalized, UTF-8 encoded, and SHA-256 hashed as lowercase hexadecimal. The hash input excludes package id/version/status/contentHash, approvals, actor-specific visibility, available actions, timestamps, audit, and wrapper metadata. UUID strings are lowercase, currency is uppercase, and `rules` are unique and sorted by `ruleReference` in UTF-8 bytewise ascending order. A future snapshot array needs an explicit ordering rule before it can be added. `DealDetail.ratification` and the three ratification action booleans are additive optional members; absent or unknown action availability remains false/read-only. Existing Deal required fields and meanings are unchanged.

Slice 11 adds the provider-independent sandbox funding foundation from ADR-010 §2.2-§2.5. `POST /deals/{dealId}/funding-plan` is a buyer entity ADMIN-only, idempotent action whose request carries only the Deal `expectedVersion`; `amountMinor` and `currency` are never client-supplied and are always copied server-side from the RATIFIED package. It returns a synchronous `201 Created` with the `FundingPlanDetail` body (the plan plus its single `FundingUnit`) and a `Location` pointing back at the same singleton path; a Deal has at most one FundingPlan and a concurrent or repeated create races under the same database unique invariant, so a second attempt returns `409`. `GET /deals/{dealId}/funding-plan` is the participant-readable plan/unit/current-operation projection and returns `404` until a plan exists. `POST /funding-units/{fundingUnitId}/payment-operations` initiates a payment; `Idempotency-Key` is required, the request carries the FundingUnit `expectedVersion`, and the durable intent/dispatch/audit/idempotency record is committed in one short transaction before the provider is ever called. It returns `202 Accepted` with the `CREATED` `PaymentOperation` projection and a `Location` at `/api/v1/payment-operations/{paymentOperationId}`; the provider result is never awaited in-request. `GET /payment-operations/{paymentOperationId}` is the polling read. `POST /payment-operations/{paymentOperationId}/reconcile` accepts only an `UNCONFIRMED` operation and dispatches query-first reconciliation the same way: it commits a durable dispatch/audit/idempotency record and returns `202` with the same operation `Location`, never calling the provider in-request. A CREATED or UNCONFIRMED operation is a FundingUnit's only in-flight operation; a FUNDED unit or an operation with a different Idempotency-Key while one is in-flight both return `409`. `FundingStatus`, `FundingUnitStatus`, and `PaymentOperationStatus` are closed enums; UNCONFIRMED is explicitly never treated as failure, and `PARTIALLY_FUNDED` is part of the `FundingStatus` axis (ADR-003 §12) but unreachable in V1 because exactly one FundingUnit exists per Deal. `DealDetail.funding` (a null-tolerant `DealFundingSummary`) and the `canCreateFundingPlan`/`canInitiateFunding`/`canReconcilePaymentOperation` booleans on `DealAvailableActions` are additive optional members; absent or unknown action availability remains false/read-only. Existing Deal required fields and meanings are unchanged. `PaymentOperation` exposes only a safe non-raw projection (status, an explicit `reconciliationRequired` indication, and an opaque non-PII `providerReference`); raw provider payloads, card data, and credentials are never part of the public contract.

The contract preserves centralized context resolution and non-disclosure, uses explicit conflicts and idempotent replay, and exposes backend-derived lifecycle and actor-aware action availability instead of asking the frontend to infer them. Management endpoints outside these reviewed public operations are not part of the public contract.

`openapi/ai-internal-v1.yaml` is separate and exposes only AI-service liveness, readiness, capabilities, and contract metadata. It intentionally has no synchronous inference endpoint. `asyncapi/m4trust-ai-v1.yaml` documents the topic exchanges `m4trust.ai.commands`, `m4trust.ai.events`, and `m4trust.ai.dead-letter`; the agreed queues and routing keys are recorded in both AsyncAPI bindings and the `x-m4trust-topology` summary.
