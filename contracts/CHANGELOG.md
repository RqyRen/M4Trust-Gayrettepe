# Changelog

## Unreleased

- Added the Slice 14A additive dispute and casework foundation contract (ADR-013 §2.1-§2.8):
  buyer/seller entity ADMIN-only, idempotent `POST /deals/{dealId}/disputes` with closed
  `DisputeReasonCode`, trimmed plaintext `subject` (1–200) and `statement` (1–4000),
  `expectedDealVersion`, and `expectedFulfillmentVersion`, returning `201 Created` with
  `DisputeDetail`, immutable `DisputeOpeningSnapshot`, and a `Location` header;
  buyer/seller ADMIN/MEMBER `GET /deals/{dealId}/disputes` and
  `GET /deals/{dealId}/disputes/{disputeId}`; append-only paginated comments;
  counterparty ADMIN `POST .../acknowledge` (`OPEN -> UNDER_REVIEW`); and opening-entity
  ADMIN `POST .../withdraw` (`OPEN | UNDER_REVIEW -> WITHDRAWN`). Every mutation requires
  CSRF and `Idempotency-Key`; acknowledge, withdraw, and comment require case
  `expectedVersion`. Closed `DisputeStatus`
  (`OPEN`/`UNDER_REVIEW`/`RESOLVED`/`WITHDRAWN`, with `RESOLVED` unreachable in 14A),
  backend-derived `DisputeAvailableActions`, safe evidence and pinned advisory video
  snapshot entries without storage identity or canonical AI payloads, and stable Problem
  Details codes `CASEWORK_NOT_FOUND_OR_HIDDEN`, `DISPUTE_NOT_FOUND_OR_HIDDEN`,
  `DISPUTE_OPEN_FORBIDDEN`, `DISPUTE_COMMENT_FORBIDDEN`, `DISPUTE_ACKNOWLEDGE_FORBIDDEN`,
  `DISPUTE_WITHDRAW_FORBIDDEN`, `DEAL_STALE_VERSION`, `FULFILLMENT_STALE_VERSION`,
  `DISPUTE_STALE_VERSION`, `DEAL_STATE_CONFLICT`, `FULFILLMENT_STATE_CONFLICT`,
  `DISPUTE_ACTIVE_CASE_EXISTS`, `DISPUTE_STATE_CONFLICT`, and `IDEMPOTENCY_KEY_REUSED`.
  Optional null-tolerant `DealDetail.casework` (`DealCaseworkSummary`) and
  `canOpenDispute` on `DealAvailableActions` are additive actor-aware members. AI
  schemas, fixtures, AsyncAPI, and the AI-internal OpenAPI remain unchanged.

- Added the Slice 13 additive per-evidence video analysis contract (ADR-012 §2.1-§2.7):
  participant-readable `GET /deals/{dealId}/fulfillment/evidence/{evidenceSubmissionId}/video-analysis`
  and buyer entity ADMIN-only, idempotent `POST` on the same path with closed
  `RequestVideoAnalysisRequest.expectedEvidenceVersion`, returning `202 Accepted`
  with `VideoAnalysisDetail` and a `Location` header pointing back at the GET
  resource. The operation never waits for RabbitMQ, storage download, or AI
  processing. Closed `VideoAnalysisStatus`
  (`NOT_REQUESTED`/`QUEUED`/`RESULT_AVAILABLE`/`FAILED`), safe advisory
  `VideoAnalysisResult` (duration, observations, anomalies, summary, warnings),
  backend-derived `VideoAnalysisAvailableActions.canRequest`, and stable Problem
  Details codes `VIDEO_ANALYSIS_REQUEST_FORBIDDEN`,
  `VIDEO_ANALYSIS_EVIDENCE_NOT_ELIGIBLE`, `EVIDENCE_STALE_VERSION`,
  `VIDEO_ANALYSIS_ACTIVE_JOB_EXISTS`, `VIDEO_ANALYSIS_ALREADY_COMPLETED`, and
  `IDEMPOTENCY_KEY_REUSED`. The public result omits technical metadata, storage
  identifiers/URLs, event payloads, and provider/model details. AI schemas,
  fixtures, AsyncAPI, and the AI-internal OpenAPI remain unchanged.

- Added the Slice 12 additive fulfillment and evidence contract (ADR-011 §2.1-§2.6):
  seller ADMIN/MEMBER `POST /deals/{dealId}/fulfillment` to atomically create the
  Deal's single fulfillment record and primary milestone bound to the current
  RATIFIED package, with required `Idempotency-Key` and `expectedVersion`;
  participant-readable `GET /deals/{dealId}/fulfillment`; seller ADMIN/MEMBER
  `POST /deals/{dealId}/fulfillment/evidence/upload-intents` returning a
  short-lived direct-PUT target; `POST
  /deals/{dealId}/fulfillment/evidence/{evidenceSubmissionId}/finalize` that
  verifies storage size/SHA-256/object version outside the DB transaction and
  atomically marks the submission SUBMITTED, sets it as current evidence, and
  moves FulfillmentStatus to REVIEW_REQUIRED; participant
  `POST .../download-link` minting a short-lived pinned GET URL; and buyer
  ADMIN-only `.../accept` and `.../reject` actions with required
  `expectedVersion`/`expectedEvidenceVersion` that check the target under lock.
  Accept completes only the milestone and fulfillment (COMPLETED); the Deal
  remains ACTIVE and no payment release, settlement, payout, refund, provider
  call, or AI job is produced. Reject preserves immutable REJECTED history and
  returns status to EVIDENCE_REQUIRED.
- Added closed `FulfillmentStatus`
  (`NOT_STARTED`/`IN_PROGRESS`/`EVIDENCE_REQUIRED`/`REVIEW_REQUIRED`/`COMPLETED`/
  `CANCELLED`, with `CANCELLED` forward-compatible but unreachable in Slice 12),
  `EvidenceSubmissionStatus` (`PENDING_UPLOAD`/`SUBMITTED`/`ACCEPTED`/`REJECTED`),
  `EvidenceType` (`DELIVERY_NOTE`/`INVOICE`/`VIDEO`/`PHOTO`/`SIGNED_DOCUMENT`/
  `OTHER`), and `EvidenceMediaType` (PDF, DOCX, JPEG, PNG, MP4) enums.
- Added optional null-tolerant `DealDetail.fulfillment` (`DealFulfillmentSummary`)
  and optional `canStartFulfillment`/`canUploadEvidence`/`canAcceptEvidence`/
  `canRejectEvidence` members on `DealAvailableActions` without changing existing
  required Deal response fields or their meanings. Locked stable
  `FULFILLMENT_START_FORBIDDEN`, `EVIDENCE_UPLOAD_FORBIDDEN`,
  `EVIDENCE_REVIEW_FORBIDDEN`, `FULFILLMENT_ALREADY_EXISTS`,
  `EVIDENCE_ALREADY_SUBMITTED`, stale-version, state-conflict, non-disclosing
  404, verification-failure, and idempotency Problem Details expectations in the
  exact public-contract validator. AI schemas, fixtures, AsyncAPI, and the
  AI-internal OpenAPI remain unchanged.

- Added the Slice 11 additive provider-independent sandbox funding contract
  (ADR-010 §2.2-§2.5): buyer-ADMIN, idempotent `POST /deals/{dealId}/funding-plan`
  whose request carries only the Deal `expectedVersion` (amount/currency are always
  server-copied from the RATIFIED package, never client-supplied), returning
  synchronous `201 Created` with `FundingPlanDetail` and a `Location` at the same
  singleton path; a second plan create for the same Deal returns `409`.
- Added participant-readable `GET /deals/{dealId}/funding-plan` (plan + single
  FundingUnit + current operation + `FundingStatus`, `404` until a plan exists),
  `Idempotency-Key`-required `POST /funding-units/{fundingUnitId}/payment-operations`
  returning `202 Accepted` with the `CREATED` `PaymentOperation` projection and a
  `Location` at `/payment-operations/{paymentOperationId}`, `GET
  /payment-operations/{paymentOperationId}` for polling, and `POST
  /payment-operations/{paymentOperationId}/reconcile`, which accepts only an
  `UNCONFIRMED` operation and returns `202` with the same operation `Location`.
  Initiate and reconcile never call the provider in-request; only a durable
  dispatch/audit/idempotency record is committed before the response.
- Added closed `FundingStatus` (`NOT_CONFIGURED`/`PLANNED`/`PENDING`/
  `PARTIALLY_FUNDED`/`FUNDED`, with `PARTIALLY_FUNDED` documented unreachable in V1),
  `FundingUnitStatus` (`PLANNED`/`PENDING`/`FUNDED`/`FAILED`), and
  `PaymentOperationStatus` (`CREATED`/`SUCCEEDED`/`DECLINED`/`UNCONFIRMED`) enums.
  `PaymentOperation` exposes only a safe non-raw projection: status, an explicit
  `reconciliationRequired` indication, and an opaque non-PII `providerReference`;
  UNCONFIRMED is never treated as failure. Money fields use integer minor units and
  uppercase ISO 4217 currency, matching Slice 10 `RatificationCommercialTerms`.
- Added optional null-tolerant `DealDetail.funding` (`DealFundingSummary`) and
  optional `canCreateFundingPlan`/`canInitiateFunding`/`canReconcilePaymentOperation`
  members on `DealAvailableActions` without changing existing required Deal response
  fields or their meanings. Locked stable `FUNDING_MUTATION_FORBIDDEN`,
  `FUNDING_PLAN_ALREADY_EXISTS`, `FUNDING_UNIT_ALREADY_FUNDED`,
  `PAYMENT_OPERATION_IN_FLIGHT`, stale-version, non-disclosing 404, and idempotency
  Problem Details expectations, plus the `202`/`201` + `Location` response contracts,
  in the exact public-contract validator. AI schemas, fixtures, AsyncAPI, and the
  AI-internal OpenAPI remain unchanged.

- Added the Slice 10 additive ratification contract: initiator package creation with
  exact Deal `expectedVersion`, explicit positive I-JSON-safe `amountMinor` and
  uppercase ISO 4217 `currency`, participant-readable immutable package detail and
  retained history, and buyer/seller ADMIN approve/reject actions with target
  `expectedPackageVersion` and UUID `Idempotency-Key`.
- Added the dedicated closed `RatificationPackageSnapshot` as the sole canonical
  content-hash input. It locks RFC 8785 JCS, UTF-8, lowercase SHA-256 hex, UUID and
  currency casing, and unique UTF-8-bytewise `ruleReference` ordering; mutable,
  actor-aware, audit, timestamp, id/version/status, approval, and wrapper fields are
  explicitly outside the hash boundary.
- Added optional Deal ratification readiness/current-package projection and optional
  actor-aware create/approve/reject action members without changing existing required
  Deal response fields or their meanings. Locked stable readiness, stale-package,
  state, authorization, non-disclosure, and idempotency Problem Details expectations
  in the exact public-contract validator. AI schemas, fixtures, AsyncAPI, and the
  AI-internal OpenAPI remain unchanged.

- Added the Slice 9 public manual-review contract: participant-readable extraction
  review, initiator-only idempotent acceptance with target `analysisId`, bulk
  kept/modified/excluded/added decisions, Deal `expectedVersion`, stable 403/404/
  409/422 outcomes, and atomic `ACCEPTED` analysis semantics.
- Added immutable RuleSetVersion history/detail reads, final-rule and excluded-decision
  history projections, source-analysis/creator/time/previous-version provenance, and
  unique server-assigned `manual-N` references for accepted manually added rules.
- Added optional `canReviewExtraction` and nullable `currentRuleSet` to the closed
  Deal response property allowlists without changing their existing required members;
  omitted or unknown action availability is read-only/false for consumers.
- Extended exact public OpenAPI validation for Slice 9 paths, scoped parameters,
  response/request schemas, `ACCEPTED`, decision discriminators, idempotency,
  immutable version projection, and stable concurrency errors. AI schemas, fixtures,
  AsyncAPI, and the AI-internal OpenAPI are unchanged.

- Added the optional advisory `legalBasis` object (`source` closed enum of Turkish
  legislation identifiers + `articleNo`) to document-extraction `result.rules[]`
  items. Backward-compatible additive change within schema 1.0.0 (ADR-002 §15.3);
  the field may be omitted entirely when legal retrieval is unavailable, carries
  no article text, and must never drive Spring business decisions.

- Added the Slice 6 public Deal document contract: JSON upload intents for direct
  private-storage PUTs, idempotent verified finalize, retained document history,
  short-lived direct download links, and no Spring binary upload proxy.
- Added PDF/DOCX, client SHA-256 and size declarations, pending-upload expiry,
  independently verified available metadata, and opaque immutable object-version
  references for pinned download and later AI access.
- Extended Deal detail with nullable backend-owned currentDocument and actor-aware
  document actions; participants can read/download while initiator-only upload and
  finalize authority remains projection-derived and re-authorized server-side.
- Added stable terminal-state, expiry, verification-mismatch, idempotency-reuse,
  and non-disclosing document Problem Details outcomes plus focused OpenAPI checks.
- Added the Slice 5 atomic Deal parties operation with required `expectedVersion`,
  nullable buyer/seller assignments, participant-bound validation, and stable
  stale-version, state-conflict, and semantic-validation errors.
- Added buyer/seller detail projections, participant `partyRoles`, and the
  actor-aware `canManageParties` action projection without exposing activation
  or ratification operations.
- Extended exact OpenAPI validation for the Slice 5 parties path, request and
  response schemas, DRAFT-only semantics, participant constraints, and the
  explicit absence of an activate endpoint.
- Added the Slice 4 public Deal invitation contract: initiator-scoped create/list/revoke,
  user-scoped incoming/accept/reject, UUID Idempotency-Key create semantics, expectedVersion
  terminal actions, and non-disclosing invitation errors.
- Added actor-aware Deal and invitation action projections, participant-only Deal detail
  projection, and separate initiator/recipient invitation DTOs: the recipient preview is
  limited to Deal id/reference/title and the inviting entity's official legal name, while
  pending recipient email is never disclosed to ordinary participants.
- Extended exact OpenAPI validation for all Slice 4 invitation paths, security/context
  boundaries, request and response schemas, stable error components, idempotency header,
  participant semantics, and recipient-email disclosure boundary.
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
