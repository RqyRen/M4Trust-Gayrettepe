# Changelog

## Unreleased

- Aligned the video-analysis `result` object with the documented extensible transport-boundary policy (`additionalProperties: true`; symmetric with document-extraction) and added the corresponding future-optional result-metadata validator check.
- Corrected the service name in the ADR-002 §21.3 capabilities example to the canonical `m4trust-ai-service` (documentation only; no wire change).
- Added the Slice 3 legal-entity-scoped Deal create, paginated list, detail, editable-basic-field update, and cancel public API design.
- Frozen the complete `DealStatus` and `DealLifecycleProjection` enum sets, separate summary/detail projections, required-nullable detail descriptions, optimistic `version`, and UTC timestamps.
- Added explicit `expectedVersion` update conflicts (`DEAL_STALE_VERSION`), invalid-state conflicts (`DEAL_STATE_CONFLICT`), backend-derived `canUpdate`/`canCancel` availability, and the centralized context-resolution split between hidden legal entities (`LEGAL_ENTITY_NOT_FOUND`) and hidden/non-participant Deals (`DEAL_NOT_FOUND`).
- Fixed Deal pagination defaults and bounds, the optional status filter, and the single allowlisted `createdAt`/`title` sort contract.
- Added the Slice 2 legal entity creation, membership listing, detail, and member-list public API design.
- Kept register/login responses on the existing `PublicUser` wire format and added the required non-null `memberships` bootstrap array only to `/auth/me`.
- Frozen legal entity roles to `ADMIN` and `MEMBER`, and the minimum create fields to bounded trimmed `legalName` and `registrationNumber` strings.
- Documented the `X-M4Trust-Legal-Entity-Id` context header, server-side membership verification, stable list DTOs, and 401/403/404/422 Problem Details behavior.

## 1.1.0 - 2026-07-15

- Added the initial OpenAPI 3.1 design contract for the public Core API with same-origin server metadata and no Slice 0 endpoints.
- Added reusable, closed `ProblemDetail` and `FieldError` component schemas for the public error contract.
- Extended lightweight validation to lock the empty public path set and required error components while retaining AI-internal path checks.
- Distinguished public Core API ownership from the AI-internal operational API in contract documentation.

## 1.0.2 - 2026-07-15

- Documented optional `service` and `serviceVersion` fields on the capabilities response (additive, already tolerated by `additionalProperties`).
- Aligned canonical fixture `producer.service` values with the ADR-007 service names (`m4trust-core-api`).
- Documented in the README that the envelope `transactionId` identifies the owning Deal aggregate (ADR-003) and that a `dealId` rename is a v2 candidate.
- No schema shape, event name, or routing key changes; wire compatibility is unchanged.

## 1.0.1 - 2026-07-13

- Hardened same-major compatibility with explicit extensible transport boundaries and strict semantic objects.
- Locked all concrete v1 event schemas to `schemaVersion` `1.0.0`.
- Added the shared UTC `Z` timestamp schema and negative offset validation.
- Bound error categories, codes, and retry flags into consistent closed combinations.
- Replaced filename-only schema IDs with globally unique canonical IDs under `https://schemas.m4trust.internal/`.
- Extended validation for duplicate IDs, expected-invalid cases, canonical references, and future optional fields.
- Added GitHub Actions contract validation for relevant changes.

## 1.0.0 - 2026-07-13

- Added the shared M4Trust event envelope with UUID identity, semantic schema versions, UTC timestamps, producer metadata, and constrained job types.
- Added document-extraction and video-analysis requested, completed, and failed event schemas.
- Added best-effort job cancellation request schema and initial cancellation reasons.
- Added canonical shared schemas for download references, producers, source references, warnings, errors, attempts, and technical metadata.
- Added canonical document extraction result structures, including the closed structured-value union with minor-unit money and basis-point percentages.
- Added canonical video analysis observations, anomalies, time ranges, and advisory summary.
- Added AsyncAPI RabbitMQ topology and internal operational OpenAPI documents.
- Added canonical fixtures and the lightweight Draft 2020-12 validation script.
