---
title: 'Serverless Video Processing Platform'
created: '2026-08-17'
updated: '2026-08-17'
status: final
---

# PRD: Serverless Video Processing Platform

## 0. Document Purpose

This PRD defines the requirements for a serverless video-processing lab running entirely local on **floci** (a free, LocalStack-compatible AWS emulator). It is for the builder (Kygor) and the downstream BMAD workflow (architecture, epics, stories). The prior `aws-lab` artifacts (Spring Boot microservices on ministack) serve as **domain reference only** — this system is re-architected serverless-native: Lambda, Step Functions, DynamoDB, EventBridge, S3, API Gateway v2, all provisioned by Terraform.

## 1. Vision

A local, running serverless video-processing platform on floci that demonstrates core AWS serverless patterns end-to-end: an HTTP upload into S3, event-driven orchestration via Step Functions, transcoding via Lambda, event routing via EventBridge, DynamoDB as the metadata store, and derived query surfaces — all provisioned by Terraform. The lab succeeds when the builder can upload one video, watch it flow through every stage, and understand each AWS service's role and each architectural decision. The goal is **purely personal learning of AWS services** — not production readiness.

Each target AWS service is exercised as a **first-class learning surface**: requirements deliberately route work through Lambda, Step Functions, DynamoDB, EventBridge, S3, and API Gateway so the builder gets hands-on experience with each.

## 2. Target User

### 2.1 Jobs To Be Done

- Learn AWS serverless services by building with them: Lambda, Step Functions, DynamoDB, EventBridge, S3, API Gateway v2, SQS/SNS, IAM, CloudWatch.
- Learn event-driven architecture: events as the glue between stateless functions.
- Learn serverless state management: DynamoDB as source of truth, state machines for orchestration.
- Learn Terraform-managed serverless infrastructure against a local emulator.
- Understand each service's role well enough to reason about real AWS architectures.

### 2.2 Non-Users (v1)

No real end users, no multi-tenancy, no auth. The "user" is the builder driving the platform with HTTP requests (Bruno or curl) through the API Gateway.

### 2.3 Key User Journeys

**UJ-1. Kygor exercises the full pipeline (happy path).**
- **Persona + context:** Kygor, the solo builder, has floci running and all infrastructure applied via Terraform.
- **Entry state:** floci up, `terraform apply` done; no prior state required.
- **Path:** upload a video through the API Gateway → object lands in S3, metadata record created with status `UPLOADED` → `video.uploaded` event → Step Functions orchestration starts → transcode Lambda runs → status becomes `PROCESSED` → `video.processed` event → history consumer records it, search consumer indexes it.
- **Climax:** a status-history query via the gateway shows the terminal event, and a title search via the gateway returns the video.
- **Resolution:** one video touched every service in the stack; final `PROCESSED` record + history entry + search hit, every client call made through the API Gateway.

## 3. Glossary

- **Video** — the core entity: a single upload with a `videoId`, title, status, S3 keys, content metadata, timestamps. Stored in the metadata DynamoDB table.
- **videoId** — the immutable unique identifier, minted exactly once at ingress (upload handler), used across S3 keys, events, and the record.
- **VideoStatus** — the state machine: `UPLOADED → PROCESSING → PROCESSED | FAILED`. `PROCESSED`/`FAILED` are terminal.
- **Event** — a JSON payload (`video.uploaded`, `video.processed`) carrying `eventId` + `schemaVersion`, routed via the event backbone (EventBridge/SQS — architecture decides).
- **eventId** — the deterministic idempotency key of an event, derived from `(videoId, status)` — identical on redelivery/republish.
- **Status history** — the derived, append-only log of terminal events for a video, keyed by unique `eventId`.
- **Derived store** — a store built from events, rebuildable and disposable (search index, status history).
- **floci** — local AWS emulator (LocalStack-compatible) at `localhost:4566`, no auth token; runs Lambda in real Docker containers.

## 4. Features

Each cluster is a pipeline stage that exercises named AWS services.

### 4.1 Upload Ingest — *API Gateway + Lambda + S3 + DynamoDB*

**Description:** the entry point. A client uploads a video over HTTP multipart through the API Gateway; a Lambda handler writes the object to S3, mints the `videoId`, creates the `UPLOADED` metadata record in DynamoDB, and emits `video.uploaded`.

**Functional Requirements:**

#### FR-1: HTTP upload to S3
A client uploads a video file over HTTP multipart through the API Gateway; the object lands in the uploads bucket.
- Bruno/curl POST to the gateway upload path with a file returns HTTP 2xx and the object exists in the uploads bucket.

#### FR-2: videoId minted once at ingress
The upload handler generates the `videoId`; the same id is used across the S3 key, the metadata record, and the emitted event.
- The returned `videoId` is a UUID, reused identically in the S3 key, the metadata record, and the event payload.

#### FR-3: UPLOADED record created
On successful upload, a metadata record is created in DynamoDB with status `UPLOADED` and both timestamps populated.
- After upload, a read of the metadata table returns the record with status `UPLOADED` and created/updated timestamps populated.

#### FR-4: Publish video.uploaded
After the record is created, a `video.uploaded` event is emitted carrying the `videoId` and a deterministic `eventId`.
- The event backbone carries a `video.uploaded` for the new `videoId`, and the processing workflow picks it up (FR-5).

**Out of Scope:** FAILED-video retry path, ingest-leg reconciliation.

### 4.2 Processing Pipeline — *EventBridge + Step Functions + Lambda + S3*

**Description:** the `video.uploaded` event triggers a Step Functions state machine that orchestrates the processing workflow: a Lambda transcodes the video (demo-mode copy fallback), writes the output to the processed bucket, and status advances to `PROCESSED` with exactly one terminal event emitted.

**Functional Requirements:**

#### FR-5: Event-driven orchestration
The `video.uploaded` event triggers a Step Functions state machine that drives the processing workflow.
- Publishing `video.uploaded` starts a state machine execution; the execution is visible in Step Functions history with the `videoId` in its input.

#### FR-6: Transcode and store output
A Lambda function produces a processed object in the processed bucket for the `videoId`. Demo-mode copy fallback is acceptable when ffmpeg is absent; real ffmpeg transcoding is a documented future extension.
- After a successful execution, the processed object exists in the processed bucket under a key tied to the `videoId`.

#### FR-7: Drive status transitions
Status advances `UPLOADED → PROCESSING` at transcode start and `→ PROCESSED` at completion; the transition is acknowledged in the metadata store before the corresponding event is published.
- The metadata record transitions `UPLOADED → PROCESSING → PROCESSED` in order; the terminal transition is acknowledged before `video.processed` appears on the backbone.

#### FR-8: Exactly one terminal event
Exactly one `video.processed` event is emitted per terminal transition; the deterministic `eventId` (from `(videoId, status)`) makes retries idempotent — a republish is a dedupe, never a duplicate, and holds across restarts.
- The published event carries an `eventId` derived from `(videoId, status)`; republishing the same transition reuses the same `eventId` (dedupe, not a second event).

#### FR-9: Idempotent consumption
A redelivered `video.uploaded` for an already-processing/terminal video is a no-op — no re-transcode.
- Redelivering `video.uploaded` for a video already `PROCESSING`/terminal produces no second transcode and no status regression; the redelivery is absorbed as a no-op.

**Out of Scope:** the FAILED producing path (deferred — FAILED exists only as state-machine rules).

### 4.3 Metadata & State Machine — *DynamoDB + Lambda*

**Description:** the single source of truth for the video record. DynamoDB stores the record; a metadata Lambda enforces the status state machine and serves reads/writes to the other functions.

**Functional Requirements:**

#### FR-10: Single source of truth
DynamoDB holds the video record: videoId, title, status, bucket, original key, processed key, content type, size, duration, failure reason, timestamps.
- A record read returns all carried fields for a known `videoId`.

#### FR-11: Enforce the state machine
Only legal transitions are accepted (`UPLOADED → PROCESSING → PROCESSED | FAILED`); terminal statuses are final; illegal transitions are rejected. Same-status re-assertion is idempotent for the transition.
- Legal transitions succeed; a regression or a transition out of a terminal status is rejected; re-asserting the current status succeeds without side effects on the transition.

#### FR-12: Idempotent create
Record creation is idempotent by `videoId` — a retry returns the existing record unchanged.
- Re-issuing create with the same `videoId` returns the original record unchanged. (With a single ingress minter, foreign-id collision is not a reachable state.)

#### FR-13: Not-found semantics
Reads/updates for an unknown `videoId` return a not-found error.
- A read or status update for an unknown `videoId` returns a not-found error, not a silent success.

### 4.4 Status History — *EventBridge + Lambda + DynamoDB*

**Description:** a derived, disposable store that records a video's terminal status history from `video.processed` events, queryable through the gateway.

**Functional Requirements:**

#### FR-14: Record history per unique event
A consumer records a history entry for each consumed `video.processed` event; a duplicate `eventId` appends nothing.
- A consumed `video.processed` yields a queryable history entry; consuming the same `eventId` twice leaves exactly one entry.

#### FR-15: Poison-event handling
Events whose `videoId` the metadata reports unknown are dropped (not stored, not retried); transient metadata-unavailable errors are retried, never dropped.
- An event for a `videoId` metadata reports unknown is dropped and never stored; a transient metadata-unavailable error causes a retry, not a drop.

#### FR-16: Query status history
A client can query a video's recorded status history through the gateway.
- A gateway history query for a `videoId` returns its entries, each carrying status, `eventId`, and timestamp.

### 4.5 Search — *EventBridge + Lambda + DynamoDB*

**Description:** a derived title index of processed videos, rebuildable from the metadata table.

**Functional Requirements:**

#### FR-17: Index processed videos
A consumer indexes a video when its `PROCESSED` event is consumed; `FAILED` videos are never indexed; poison-event handling per FR-15.
- A consumed `PROCESSED` event makes the video searchable by title; a `FAILED` video never appears in the index.

#### FR-18: Search by title substring
A client can search processed videos by title substring through the gateway.
- A gateway search with a title substring returns the matching processed videos.

#### FR-19: Rebuildable index
The index is rebuildable from the metadata table; the rebuild trigger is admin-only (no client-facing surface).
- After clearing the index, a rebuild repopulates it from the metadata table, indexing `PROCESSED` videos only; no gateway route exposes the rebuild.

> **[NOTE FOR PM]** The rebuild trigger mechanism (direct Lambda invoke vs. a non-gateway admin route) is deferred to architecture. The constraint is fixed: no client-facing rebuild surface.

### 4.6 Client Ingress — *API Gateway v2*

**Description:** the single door for all client HTTP.

**Functional Requirements:**

#### FR-20: Single ingress, open lab
All client HTTP (upload, status history, search) goes through one API Gateway; no auth.
- Upload, history, and search all succeed through the gateway URL without credentials.

#### FR-21: Route the three journeys
Gateway routes map 1:1 to the three client journeys (upload, history, search); responses pass through unchanged (status codes and error bodies).
- Each gateway route reaches the correct journey; HTTP status codes and `{"error": ...}` bodies pass through unchanged.

#### FR-22: Reproducible test collection
A reproducible test collection (Bruno or curl scripts) exercises all three journeys through the gateway only — never backend endpoints directly.
- Running the collection against a fresh `terraform apply` reproduces SM-1 end-to-end.

### 4.7 Infrastructure as Code — *Terraform*

**Description:** the entire environment is declarative.

**Functional Requirements:**

#### FR-23: Everything in Terraform
Every resource (buckets, tables, functions, state machine, event bus/rules, gateway routes/integrations/stage, IAM roles) is declared in Terraform and created by `terraform apply` against floci.
- `terraform apply` against a clean floci creates the entire environment; `terraform destroy` + re-apply rebuilds it from the same configuration.

#### FR-24: No aws cli in setup/teardown
Setup/teardown uses Terraform commands only; `aws cli` is permitted for ad-hoc PoC/inspection only, never in the documented setup/teardown procedure.
- The documented setup/teardown steps contain no `aws` CLI invocations; all resource creation is attributable to the Terraform configuration.

## 5. Non-Functional Requirements

- **NFR-1: Consumer idempotency** — every event consumer keys dedupe on the deterministic `eventId` derived from `(videoId, status)`; redelivery produces no duplicate records and no re-work.
- **NFR-2: Exactly-once emission per transition** — deterministic, name-based `eventId` is restart-proof; a retry is an idempotent republish, never a new id.
- **NFR-3: Error semantics** — client-facing HTTP surfaces return appropriate status codes (400/404/409/500) with body `{"error": "<message>"}`.
- **NFR-4: Config not code** — no endpoint, region, credential, or resource name is hardcoded in function code; all come from Lambda environment variables and Terraform-declared names.
- **NFR-5: Observability** — CloudWatch Logs capture every Lambda invocation; Step Functions execution history is inspectable; the full path of one video is traceable through logs.
- **NFR-6: Reproducible environment** — verification is a clean `terraform destroy` + `apply` against floci plus a smoke upload run; no CI/CD in the lab.
- **NFR-7: Performance non-goal** — no optimization for throughput, latency, scale, or production resilience; adding such polish is scope creep against the learning goal.
- **NFR-8: Terraform-only setup/teardown** — all resource creation is attributable to the Terraform configuration.

## 6. Success Metrics

- **SM-1:** From a clean `terraform destroy` + `apply` with floci running, one upload through the gateway produces: a `PROCESSED` metadata record, a status-history entry, and a search hit — all queryable through the gateway — and the execution path demonstrably exercises each target service (API Gateway, Lambda, S3, DynamoDB, EventBridge, Step Functions), verified via Step Functions execution history, Lambda logs, and event records.
- **SM-2:** Every resource in the environment was created by Terraform (nothing manual).
- **SM-3:** The builder can explain each AWS service's role in the pipeline and why each architectural decision was made (self-assessed).

**Counter-metrics:** no throughput/latency targets; no concurrency or scale testing; time-to-completion of a single video is irrelevant.

## 7. Out of Scope

- Production readiness: auth, multi-tenancy, resilience, monitoring/alerting beyond CloudWatch Logs.
- Real ffmpeg transcoding (deferred future extension — demo-mode copy fallback for v1).
- The FAILED producing path (state-machine rules exist; nothing produces FAILED in v1).
- FAILED-video retry and ingest-leg reconciliation.
- Event-schema versioning as a learning surface (schemas exist and are shared, but versioning policy is not a feature).
- CI/CD, remote Terraform state, modules/workspaces, multi-region.
- Real AWS deployment (floci only for now; the config keeps a real-AWS path possible).

## 8. Open Questions

1. **floci integration coverage** — does floci wire the integrations this pipeline depends on (EventBridge → Step Functions triggering, API Gateway v2 → Lambda, Lambda environment variables, DynamoDB conditional writes) end-to-end? Phase 0 verified only S3 via Terraform. *Revisit:* a spike/PoC — a Terraform-created EventBridge rule → Step Functions → Lambda chain — before or during the architecture phase.
2. **FAILED demo later?** — whether a FAILED-producing demo is wanted in a future iteration. *Revisit:* when the FAILED/reprocessing story matters.

## 9. Assumptions Index

- **[A-1]** floci is the only deployment target for v1; real AWS is deferred, with the Terraform config keeping that path possible.
- **[A-2]** floci's emulation of the pipeline's required integrations (EventBridge → Step Functions, API Gateway v2 → Lambda, Lambda env wiring, DynamoDB conditional writes) is sufficient for the lab — **unverified as of Phase 0**; see Open Question 1.
- **[A-3]** A zip-packaged Python Lambda with demo-mode copy fallback is sufficient for the learning goal; real ffmpeg via container-image Lambda is a documented future extension.
- **[A-4]** Single region `us-east-1`, dummy credentials, local Terraform state.
