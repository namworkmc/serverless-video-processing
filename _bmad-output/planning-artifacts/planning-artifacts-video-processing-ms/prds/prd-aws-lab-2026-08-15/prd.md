---
title: 'Video processing platform'
created: '2026-08-15'
updated: '2026-08-15'
status: 'final'
---

# PRD: Video processing platform
*Working title — confirm.*

## 0. Document Purpose

This PRD defines the requirements for a 5-service video-processing lab built on AWS patterns (emulated locally via ministack). It is for the builder (Kygor) and for the BMAD downstream workflow: epics, stories, and implementation specs hang off the features defined here. This PRD covers the **requirements layer** — vision, features per service, scope, success. The technical decisions (service boundaries, state machine, contracts, port table, client ingress, infra provisioning) already live in the finalized architecture spine at `_bmad-output/planning-artifacts/architecture/architecture-aws-lab-2026-08-15/ARCHITECTURE-SPINE.md`; this PRD binds to it and does not duplicate it. Tech/how detail from this conversation goes to the addendum.

> **NOTE FOR PM — spine binding:** the API Gateway as single client ingress (§4.7, FR-21..24) and Terraform-managed ministack infrastructure (§4.8, FR-25/26) are now bound in the architecture spine as AD-9 and AD-10 (spine revised 2026-08-15). Stories may derive from these features; where this PRD and the spine differ, the spine's pinned details (path table, stage `dev`, rebuild surface) win.

## 1. Vision

A local, running 5-service video-processing platform on ministack that demonstrates the AWS + Spring Boot microservices patterns end-to-end: an HTTP upload into S3, async SQS-driven transcoding, a single source-of-truth metadata store, derived status/search/notification services, a versioned shared contract, and an API Gateway as the single client ingress. The lab succeeds when the builder can upload one video, watch it flow through every stage, and then explain and teach each architectural decision. The goal is learning and confidence to teach the patterns — not production readiness.

## 2. Target User

### 2.1 Jobs To Be Done

- Practice building each layer of a microservices system (boundary → service → persistence) in Spring Boot.
- Exercise real AWS service integrations (S3, SQS) against ministack emulation.
- Learn gRPC contract design and a versioned-from-day-one shared contract.
- Learn async event-driven decoupling with idempotent consumers.
- Learn single-writer data ownership and a strictly enforced status state machine.
- Learn how derived services (search, notification) are rebuilt from events.
- Be able to explain each of these patterns to someone else.

### 2.2 Non-Users (v1)

- No real end users, no multi-tenancy, no auth. The "user" is the builder driving the platform with Bruno — client flows through the API Gateway (HTTP), plus direct gRPC as a debugging/inspection surface only.

### 2.3 Key User Journeys

**UJ-1. Kygor exercises the full pipeline (happy path).**
- **Persona + context:** Kygor, the solo builder, has the 5 services running locally on ministack with an API Gateway in front.
- **Entry state:** services and ministack up; gateway deployed; Bruno collection ready; no prior state required.
- **Path:** upload a video to the **API Gateway** via `upload-service` HTTP route → watch it land in S3, metadata record created with status `UPLOADED` → processing-service picks up `video.uploaded` → transcodes → metadata status becomes `PROCESSED` → `video.processed` event → notification-service records history and search-service indexes it.
- **Climax:** a status/history query via the gateway shows `PROCESSED`, and a title search via the gateway returns the video.
- **Resolution:** one video, all five services touched, final `PROCESSED` record + search hit, every client call made through the API Gateway. Builder can then explain each step and each pattern.

Realizes the teaching JTBD above.

## 3. Glossary

- **Video** — the core entity: a single upload with a `videoId`, title, status, S3 keys, content metadata, timestamps. Owned by metadata-service.
- **videoId** — the immutable unique identifier, minted exactly once at ingress (upload-service), used across S3 keys, events, and the record.
- **VideoStatus** — the state machine: `UPLOADED → PROCESSING → PROCESSED | FAILED`. `PROCESSED`/`FAILED` are terminal. Enforced by metadata-service.
- **Event** — a JSON payload on an SQS queue (`video.uploaded`, `video.processed`) carrying `eventId` + `schemaVersion`. A projection of owner-confirmed state. **Naming:** event names use dots (`video.uploaded`); the queues that carry them use dashes (`video-uploaded`).
- **eventId** — the deterministic idempotency key of an event, derived from `(videoId, status)`.
- **Status history** — the notification-service's recorded, queryable log of terminal events for a video. Derived and append-only per unique `eventId`.
- **Record store** — a store owning its data: metadata DB (sole record DB), search index, notification history. All record stores run PostgreSQL (spine AD-11); single-writer. Each service owns its own database — no cross-service reads.
- **Derived service** — a service built from events, rebuildable, disposable (search, notification).
- **Shared contract** — `video-common`: the gRPC `.proto` services/messages and JSON event DTOs all services compile against.
- **Stateless worker** — a service with no record store (upload, processing), all state lives elsewhere.
- **ministack** — local AWS emulation (S3 + SQS + API Gateway) at `localhost:4566` via docker compose.
- **API Gateway** — the single client ingress (AWS API Gateway v2 HTTP API, emulated by ministack, provisioned by Terraform — spine AD-9/AD-10). All client HTTP traffic reaches the services only through it; no client HTTP call goes directly to a service (direct gRPC stays a debugging/inspection surface per FR-21). No auth in this lab.

## 4. Features

Each service is a feature cluster that demonstrates a named architectural pattern, and the API Gateway is a feature cluster demonstrating the single-ingress pattern. Cross-cutting rules (error mapping, config-not-code, contract versioning) are governed by the architecture spine.

### 4.1 Shared Contract (video-common) — *versioned-from-day-one contract*

**Description:** the single shared module every service compiles against. Holds the gRPC contract (`VideoMetadataService`, `VideoSearchService`), the JSON event DTOs, and queue/bucket name constants. Demonstrates the versioned-from-day-one pattern: breaking change = new proto version, not an in-place edit. Realizes UJ-1.

**Functional Requirements:**

#### FR-1: Shared gRPC contract

All services can compile against one `.proto` defining `VideoMetadataService` (CreateVideo, UpdateVideoStatus, GetVideo, ListVideos) and `VideoSearchService` (SearchVideos) with `VideoInfo` as the canonical shape.

**Consequences (testable):**
- `video-common` jar builds and exposes generated gRPC classes used by services.
- `VideoInfo` carries: videoId, title, status, bucket, originalKey, processedKey, contentType, sizeBytes, durationSeconds, failureReason, timestamps.

#### FR-2: Shared event DTOs

`video.uploaded` and `video.processed` events are defined once with `eventId` + `schemaVersion`.

**Consequences (testable):**
- Event DTOs exist in `video-common`, used by publisher and consumers alike.
- `eventId` is deterministic from `(videoId, status)` — identical on redelivery.

#### FR-3: Shared names

Queue and bucket names are declared once in Terraform (spine AD-6) and mirrored in `video-common.Names`; services reference only the shared constants.

**Consequences (testable):**
- No service string-types a queue/bucket name inline; all reference the shared `Names` (which mirror the Terraform-declared names).
- The verification story asserts `Names` equals the `terraform output` names (spine AD-6 enforcement); a rename is a coordinated change.

#### FR-4: Versioning policy

Contract evolves additively; renumber/rename/remove is a breaking change requiring a new version, not an edit.

**Consequences (testable):**
- Proto field numbers and message names are immutable within a version.
- New versions coexist (server exposes both during migration).

**Out of Scope:**
- Breaking changes actually executed in this lab; the policy is established and documented.

### 4.2 Upload (upload-service) — *stateless ingress worker + single identity mint*

**Description:** the entry point. Accepts an HTTP multipart upload, writes the object to S3, mints the `videoId`, creates the `UPLOADED` record via metadata-service gRPC, and publishes `video.uploaded`. Stateless: no record store. Demonstrates identity minted exactly once at ingress. Realizes UJ-1.

**Functional Requirements:**

#### FR-5: HTTP upload to S3

A client can upload a video file over HTTP multipart and the object lands in the uploads bucket.

**Consequences (testable):**
- Bruno REST call to the gateway upload path with a file upload returns HTTP 2xx and the object exists in the uploads bucket.

#### FR-6: videoId minted once

upload-service generates the `videoId` and supplies it to metadata-service via `CreateVideo`.

**Consequences (testable):**
- The returned `videoId` is a UUID, reused across S3 key, event, and record.
- Metadata `CreateVideo` receives a required `video_id`; generate-if-absent is a fallback used only for tooling/grpcurl-style calls, never the normal path.

#### FR-7: Publish video.uploaded

After the record is created, upload-service publishes the `video.uploaded` event.

**Consequences (testable):**
- Processing-service receives a `video.uploaded` for the new video.

**Out of Scope:**
- FAILED-video retry path, ingest-leg reconciliation (deferred in spine).

### 4.3 Processing (processing-service) — *idempotent SQS consumer + sole terminal-status producer*

**Description:** consumes `video.uploaded`, transcodes the video (ffmpeg; demo-mode copy fallback), writes the processed object to S3, advances the status via metadata-service gRPC (`PROCESSING` at start, `PROCESSED` at completion), and publishes `video.processed`. Demonstrates an idempotent consumer and the state machine's producer assignment: only processing-service mints terminal statuses. Realizes UJ-1.

**Functional Requirements:**

#### FR-8: Consume video.uploaded

processing-service subscribes to the `video-uploaded` queue and processes each message.

**Consequences (testable):**
- A published `video.uploaded` leads to a transcode attempt.
- Redelivered duplicate `video.uploaded` for an already-processing/terminal video is a no-op (acked, no re-transcode).

#### FR-9: Transcode and store output

The service produces a processed object and writes it to the processed bucket.

**Consequences (testable):**
- After processing, the processed object exists in the processed bucket for the videoId.

#### FR-10: Drive status transitions

processing-service sets `PROCESSING` at transcode start and `PROCESSED` at successful completion via metadata gRPC — the legal `VideoStatus` transitions in FR-14.

**Consequences (testable):**
- Metadata record transitions `UPLOADED → PROCESSING → PROCESSED` in order.
- Regressions or transitions out of terminal status are rejected with `FAILED_PRECONDITION` by metadata.

#### FR-11: Publish video.processed

On terminal completion, processing-service publishes exactly one `video.processed` event per transition.

**Consequences (testable):**
- `video.processed` is delivered to notification-service and search-service.
- Republish/redelivery reuses the same `eventId` (dedupe, not a second row).

**Out of Scope:**
- Actual `FAILED` producing path (demoed via state-machine rules only).
- ffmpeg install strategy (deferred to Dockerfile story).

### 4.4 Metadata (metadata-service) — *single-writer source of truth + enforced state machine*

**Description:** the source of truth for the Video record. Owns the video record DB (PostgreSQL, spine AD-11) and enforces the status state machine. All writes cross its gRPC API; nothing reads its DB directly. Demonstrates single-writer data ownership. Realizes UJ-1.

**Functional Requirements:**

#### FR-12: Create and read video records

Clients create, get, and list videos via gRPC.

**Consequences (testable):**
- `CreateVideo` persists a record with status `UPLOADED`, timestamps populated.
- `GetVideo` returns a persisted record; unknown `videoId` → `NOT_FOUND`.
- `ListVideos` returns at most `limit` records.

#### FR-13: Idempotent create by ingress id

A `CreateVideo` retry with the same ingress-minted `videoId` returns the existing record unchanged.

**Consequences (testable):**
- Re-issuing `CreateVideo` with the same `video_id` returns the original record (idempotent success).
- A genuinely foreign `videoId` collision → `ALREADY_EXISTS`.

#### FR-14: Enforce the state machine

Only legal `VideoStatus` transitions (`UPLOADED → PROCESSING → PROCESSED | FAILED`) are accepted; terminal statuses are final.

**Consequences (testable):**
- `UpdateVideoStatus` to a legal next state succeeds.
- Regression or transition out of terminal → `FAILED_PRECONDITION`.
- Same-status re-assertion is idempotent for the transition (request fields still applied).

#### FR-15: Standard error semantics

gRPC returns standard status codes; the client-facing HTTP facades (AD-9 enumerated surface) map per the spine's table.

**Consequences (testable):**
- `NOT_FOUND`, `INVALID_ARGUMENT`, `ALREADY_EXISTS`, `FAILED_PRECONDITION` map to 404/400/409/409 over HTTP facades.

**Out of Scope:**
- Transactional outbox (deferred; carve-out documented in spine).

### 4.5 Notification (notification-service) — *derived event-sourced store*

**Description:** consumes `video.processed` and keeps a status history (append per unique `eventId`), queryable over the API. Demonstrates a derived, disposable store built from events — it can be rebuilt from retained events, never written by another service. Realizes UJ-1 (status history query).

**Functional Requirements:**

#### FR-16: Consume video.processed and record history

notification-service records a history entry for each terminal event.

**Consequences (testable):**
- A `video.processed` event yields a queryable status history entry.
- Duplicate `eventId` does not append a second row.
- **Poison-event handling (shared derived-service rule, referenced by FR-18):** the consumed event is validated against metadata (`GetVideo`); an event for a `videoId` that metadata reports as unknown is dropped as poison (not stored / not indexed). `UNAVAILABLE`/deadline errors are retried, never dropped.

#### FR-17: Query status history

A client can query the recorded status history for a video.

**Consequences (testable):**
- Bruno REST call to the gateway history path returns the history showing `PROCESSED`.

**Out of Scope:**
- Live SSE streaming (demonstrated surface is history query).
- Real delivery channels (email/push) — deferred.
- Reconciliation of missing notifications (accepted; SSE best-effort).

### 4.6 Search (search-service) — *derived event-sourced index + rebuild*

**Description:** consumes `video.processed` and indexes videos for title-substring search; serves search via gRPC `VideoSearchService` and an HTTP facade. Index is derived and rebuildable from metadata `ListVideos`. Demonstrates a derived event-sourced index. Realizes UJ-1 (search hit).

**Functional Requirements:**

#### FR-18: Index processed videos

search-service indexes a video when its `PROCESSED` event is consumed.

**Consequences (testable):**
- A processed video appears in search results by title substring.
- `FAILED` videos are never indexed; un-processed videos are not searchable.
- Poison-event handling per the shared derived-service rule in FR-16.

#### FR-19: Search by title substring

A client can search videos by a substring of the title through the gateway's HTTP search route.

**Consequences (testable):**
- Bruno REST search through the gateway returns matching videos.

#### FR-20: Rebuild from metadata

The index can be rebuilt from `ListVideos` on metadata-service.

**Consequences (testable):**
- After clearing the index, a rebuild repopulates it from metadata.
- A rebuild is triggerable — at minimum on service startup (index empty) and via an explicit rebuild request over the gRPC surface (admin), so the story is testable without restarting the service. **No HTTP rebuild surface** — the rebuild trigger is gRPC/admin only (spine AD-9 keeps the client-facing surface enumerated as upload/history/search).

**Out of Scope:**
- Richer search (fuzzy, relevance ranking, faceted) — deferred.

### 4.7 Client Ingress (API Gateway) — *single ingress / API Gateway as edge*

**Description:** the only door clients use. An AWS API Gateway v2 (HTTP API, emulated by ministack) sits in front of the services; all client HTTP traffic reaches a service only through it. Bruno never calls a service's HTTP facade directly (gRPC debugging/inspection via service gRPC ports is permitted per FR-21). The gateway routes by path to the upload-service, notification-service, and search-service HTTP facades; gRPC stays strictly internal service-to-service. No auth in the lab. Demonstrates the API-Gateway-as-single-ingress pattern: one entry point, services behind it, client unaware of service topology. Realizes UJ-1.

**Functional Requirements:**

#### FR-21: Single client ingress

A client can reach every client-facing HTTP surface (upload, status history, search) through one API Gateway endpoint; no client call goes directly to a service.

**Consequences (testable):**
- Bruno's upload, history, and search requests all target the gateway URL, never `localhost:8080/8081/8082/8090/8091` directly.
- A request to a service's direct port from the client is not part of the exercised path.
- **Carve-out:** Bruno gRPC requests to a service's gRPC port are permitted as a debugging/inspection surface (e.g. verifying metadata state, exercising internal contracts), explicitly distinct from the gateway-exercised client path. This does not relax FR-23; gRPC is never gateway-exposed.

#### FR-22: Route client HTTP to services

The gateway routes each request to the correct service HTTP facade (upload → upload-service, history → notification-service, search → search-service), per the spine's authoritative path table (AD-9: gateway route path == facade client path, no edge rewriting).

**Consequences (testable):**
- A gateway upload path reaches upload-service; a gateway history path reaches notification-service; a gateway search path reaches search-service.
- HTTP responses (status codes, `{"error": ...}` bodies) pass through the gateway unchanged.

#### FR-23: gRPC stays internal

gRPC calls (metadata, search) are service-to-service only; they are not exposed through the gateway.

**Consequences (testable):**
- Services reach metadata/search via gRPC channels as before; the gateway exposes HTTP routes only.

#### FR-24: No auth on the gateway (lab)

The gateway requires no authentication; all routes are open in this learning lab.

**Consequences (testable):**
- Gateway requests succeed without credentials.

**Out of Scope:**
- Gateway auth, rate limiting, usage plans, custom domains, WebSocket — deferred.
- Gateway stage/deployment mechanics beyond what a minimal lab needs.

### 4.8 Infrastructure Provisioning (Terraform) — *infrastructure as code*

**Description:** all ministack infrastructure (S3 buckets, SQS queues, API Gateway v2 API/routes/integrations/stage) is provisioned with Terraform — never `aws cli`. This is a hard rule for the lab: the ministack endpoint is the Terraform AWS provider target (with the provider configured against `http://localhost:4566`), and the same `.tf` configuration is what would target real AWS later. Demonstrates the infrastructure-as-code pattern: the environment is declarative, repeatable, and reviewable. Realizes UJ-1 (the lab's backing infrastructure is up because Terraform applied it).

**Functional Requirements:**

#### FR-25: All ministack infra via Terraform

Every backing resource the services depend on (uploads bucket, processed bucket, `video-uploaded` queue, `video-processed` queue, API Gateway v2 with routes/integrations to the services) is declared in Terraform and created by `terraform apply` against ministack.

**Consequences (testable):**
- `terraform apply` against ministack creates all buckets, queues, and the gateway with routes/integrations/stage.
- A fresh ministack is fully brought up by `terraform apply` alone; no resource is created out-of-band. Bring-up order: ministack up → `terraform apply` → services boot last (spine AD-10).
- `terraform destroy`/re-apply rebuilds the environment from the same configuration; services surviving a destroy+re-apply MUST be restarted (queue/bucket URLs change, spine AD-10).

#### FR-26: No aws cli in setup

The setup and teardown procedure uses Terraform commands only; `aws cli` is not used to create or configure any ministack resource.

**Consequences (testable):**
- The lab's documented setup/teardown steps contain no `aws` CLI invocations.
- All resource creation is attributable to the Terraform configuration.

**Out of Scope:**
- Advanced Terraform (remote state, modules, workspaces, providers beyond AWS) — deferred; local state suffices for the lab.
- Real-AWS provider/auth wiring — the same config targets ministack now; real AWS is the infra phase.

## 5. Non-Goals (Explicit)

- Not production-ready: no real AWS, no kubernetes, no DNS, no secrets management (deferred infra phase).
- No auth, no multi-tenancy, no real end users; the API Gateway is open (no auth, rate limits, usage plans, or custom domains in this lab).
- No real FAILED-video producing/demo path; no retry/reprocess workflow.
- No live SSE streaming demo; status surfaced via history query.
- No real notification channels (email/push).
- No richer search beyond title substring.
- No CI/CD pipeline; verification is build + compose-up smoke + Bruno.
- No `aws cli` for infrastructure setup/teardown; Terraform only.
- No observability beyond actuator health and gRPC health.

## 6. MVP Scope

### 6.1 In Scope

- Maven multi-module: `video-common` + 5 services.
- Shared gRPC/event contract, versioned policy.
- API Gateway (v2 HTTP API, ministack) as the single client ingress; Bruno calls the gateway only.
- Upload (HTTP→S3 + CreateVideo + publish), Processing (consume + transcode + status + publish), Metadata (record store + state machine), Notification (history query), Search (index + title search).
- Ministack (S3 + SQS + API Gateway) local emulation at the workspace root + PostgreSQL record stores (each record-owning service declares its own `postgres` instance in its own repository's `docker-compose.yml`, one DB per service); fixed port table; config-not-code.
- Terraform-managed ministack infrastructure (buckets, queues, API Gateway) — no `aws cli` in setup.
- Bruno collection as the test surface, pointed at the gateway.

### 6.2 Out of Scope for MVP

- Everything in §5 Non-Goals.
- Reconciliation job, outbox, DLQ policy, dedupe retention policy — deferred with revisit conditions in the spine.

## 7. Success Metrics

**Primary**
- **SM-1**: One upload flows end-to-end — video reaches `PROCESSED`, status history shows it, search finds it, all 5 services exercised, every client call made through the API Gateway, and the whole backing environment rebuilt from `terraform apply`. Validates FR-1..FR-26 (the shared contract in FR-1..4 is the foundation these outcomes stand on). Target: reproducible via Bruno against the gateway on a clean ministack.

**Secondary**
- **SM-2**: The builder can explain each service's named pattern (spine ADs) and its contract without re-reading code. Validates the teaching JTBD.

**Counter-metrics (do not optimize)**
- **SM-C1**: Do not optimize for throughput, latency, scale, or production resilience — this is a learning lab; adding such polish is scope creep against the teaching goal.

## 8. Open Questions

1. Whether a FAILED-demo is wanted later (a future iteration beyond this MVP) — revisit when the FAILED/reprocessing story matters.

## 9. Assumptions Index

- **[A-1]** Assume ministack is the only deployment target for MVP (no real AWS until the infra phase).
- **[A-2]** Assume ministack's API Gateway v2 emulation (HTTP API, HTTP proxy integrations, path-based data plane at `/_aws/execute-api/{apiId}/{stage}/{path}`) is sufficient for the lab's gateway routes; no DNS/Host-override tricks needed for Bruno. Spine AD-10 pins a named stage (`dev`) with a deployment; the invoke URL flows to client config via `terraform output`.
- **[A-3]** Assume the gateway and all ministack resources are provisioned via Terraform (AWS provider targeting `http://localhost:4566`), not `aws cli` and not ad-hoc control-plane calls. The ministack API Gateway reaches the host-local service facades via `host.docker.internal:<port>` (spine AD-9).
- **[A-4]** Assume Bruno covers the gateway-exercised client paths (HTTP) and, separately, direct gRPC as a debugging/inspection surface per the FR-21 carve-out.
- **[A-5]** Assume ffmpeg availability is handled in the processing-service Dockerfile story (deferred detail).
- **[A-6]** Assume PostgreSQL is the engine for every record store (metadata video record, notification history, search index — spine AD-11). Each record-owning service declares its own `postgres` instance in a `docker-compose.yml` inside its own repository (`metadata` for metadata-service, `notification` for notification-service, `search` for search-service) for dev — the root `docker-compose.yml` holds only the agentic engineering workspace (ministack + redis) and never a service's database. Real AWS: Amazon Aurora (PostgreSQL-compatible), reached via Spring Boot `spring.datasource.*` (not SCAWS auto-config). `ddl-auto: update` suffices for the lab.
