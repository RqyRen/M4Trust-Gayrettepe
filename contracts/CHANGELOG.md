# Changelog

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
