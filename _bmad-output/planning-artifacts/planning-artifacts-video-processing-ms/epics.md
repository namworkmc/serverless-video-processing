---
title: 'Video processing platform - Epic Breakdown'
created: '2026-08-15'
updated: '2026-08-15'
status: 'in-progress'
stepsCompleted:
  - step-01-validate-prerequisites
  - step-02-design-epics
  - step-03-create-stories
  - step-04-final-validation
inputDocuments:
  - planning-artifacts/prds/prd-aws-lab-2026-08-15/prd.md
  - planning-artifacts/architecture/architecture-aws-lab-2026-08-15/ARCHITECTURE-SPINE.md
  - implementation-artifacts/spec-video-processing-microservices.md
---

# aws-lab - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for aws-lab (video-processing platform), decomposing the requirements from the PRD, Architecture spine, and the frozen Story 1 spec into implementable stories.

## Requirements Inventory

### Functional Requirements

- **FR-1:** Shared gRPC contract — all services compile against one `.proto` defining `VideoMetadataService` (CreateVideo, UpdateVideoStatus, GetVideo, ListVideos) and `VideoSearchService` (SearchVideos), with `VideoInfo` as the canonical shape.
- **FR-2:** Shared event DTOs — `video.uploaded` and `video.processed` are defined once in `video-common`, each carrying `eventId` + `schemaVersion`.
- **FR-3:** Shared names — queue and bucket names are declared once in Terraform and mirrored in `video-common.Names`; services reference only the shared constants.
- **FR-4:** Versioning policy — contract evolves additively; renumber/rename/remove is a breaking change requiring a new proto version, not an in-place edit.
- **FR-5:** HTTP upload to S3 — a client uploads a video file over HTTP multipart and the object lands in the uploads bucket.
- **FR-6:** `videoId` minted once at ingress — upload-service generates the `videoId` and supplies it to metadata-service via `CreateVideo` (required field).
- **FR-7:** Publish `video.uploaded` — upload-service publishes the event after the record is created.
- **FR-8:** Consume `video.uploaded` — processing-service subscribes to `video-uploaded`; a redelivered duplicate for an already-processing/terminal video is a no-op (acked, no re-transcode).
- **FR-9:** Transcode and store output — processing-service produces a processed object in the processed bucket.
- **FR-10:** Drive status transitions — processing-service sets `PROCESSING` at transcode start and `PROCESSED` at completion via metadata gRPC.
- **FR-11:** Publish `video.processed` — exactly one event per terminal transition, deterministic `eventId` (dedupe, not a second row).
- **FR-12:** Create and read video records — `CreateVideo`, `GetVideo`, `ListVideos` via gRPC on metadata-service.
- **FR-13:** Idempotent create by ingress id — a `CreateVideo` retry with the same ingress-minted `videoId` returns the existing record unchanged; foreign collision → `ALREADY_EXISTS`.
- **FR-14:** Enforce the state machine — only legal `VideoStatus` transitions (`UPLOADED → PROCESSING → PROCESSED | FAILED`) are accepted; terminal statuses are final; same-status re-assertion is idempotent for the transition (request fields still applied).
- **FR-15:** Standard error semantics — gRPC returns standard status codes; HTTP facades map per the spine table (400/404/409/409/500).
- **FR-16:** Consume `video.processed` and record status history — notification-service appends per unique `eventId`; duplicate `eventId` does not append; poison events (metadata reports unknown `videoId`) are dropped; `UNAVAILABLE`/deadline errors retried, never dropped.
- **FR-17:** Query status history — a client can query the recorded status history for a video through the gateway.
- **FR-18:** Index processed videos — search-service indexes a video when its `PROCESSED` event is consumed; `FAILED` videos never indexed; poison-event handling per FR-16 rule.
- **FR-19:** Search by title substring — client searches videos by title substring through the gateway's HTTP search route.
- **FR-20:** Rebuild index from metadata — rebuildable from `ListVideos`; trigger is gRPC/admin only (no HTTP surface).
- **FR-21:** Single client ingress — every client-facing HTTP surface (upload, status history, search) is reached only through the API Gateway; direct gRPC to services stays a debugging/inspection surface.
- **FR-22:** Route client HTTP to services — gateway routes by path per the spine's authoritative path table (gateway route path == facade client path, no edge rewriting); responses pass through unchanged.
- **FR-23:** gRPC stays internal — gRPC (metadata, search) is service-to-service only, never gateway-exposed.
- **FR-24:** No auth on the gateway (lab) — all gateway routes are open.
- **FR-25:** All ministack infra via Terraform — every backing resource (buckets, queues, API Gateway routes/integrations/stage) is declared in Terraform and created by `terraform apply`.
- **FR-26:** No `aws cli` in setup — setup/teardown uses Terraform commands only.

### NonFunctional Requirements

- **NFR-1:** Consumer idempotency (reliability) — every SQS consumer keys dedupe on the deterministic `eventId` derived from `(videoId, status)`; redelivery/republish produces no duplicate rows and no re-work.
- **NFR-2:** Exactly-once event emission per transition across restarts — deterministic, name-based `eventId` is restart-proof; a retry is an idempotent republish, never a new id.
- **NFR-3:** Error semantics — gRPC services return standard status codes only; HTTP facades map per the spine table (`INVALID_ARGUMENT`→400, `NOT_FOUND`→404, `ALREADY_EXISTS`→409, `FAILED_PRECONDITION`→409, `INTERNAL`→500) and return the gRPC description verbatim in `{"error": "<description>"}`.
- **NFR-4:** Config-not-code — no AWS endpoint, region, credential, or gRPC target is hardcoded; all live in `application.yml` (`spring.cloud.aws.*`, `spring.grpc.client.channel.<name>.target`) with ministack as the default profile and a real-AWS profile as an override.
- **NFR-5:** Names authority — queue/bucket names are declared once in Terraform; `com.videolab.common.Names` mirrors them and MUST match; the build/verification FAILS on divergence.
- **NFR-6:** Observability — every service exposes actuator health; gRPC servers expose `grpc.health.v1`; logging is Spring Boot defaults (logback, INFO, `com.videolab` loggers).
- **NFR-7:** Build gate — verification is `mvn -q -DskipTests package` (all modules compile) plus a compose-up smoke run against a clean `terraform destroy` + `apply` result; no CI/CD in the lab.
- **NFR-8:** Setup/teardown uses Terraform only — no `aws cli` in the documented setup/teardown procedure; all resource creation is attributable to the Terraform configuration.
- **NFR-9:** Performance non-goal — do not optimize for throughput, latency, scale, or production resilience (SM-C1); adding such polish is scope creep against the teaching goal.
- **NFR-10:** Normative port table — service ports are fixed (upload 8080, processing 8081, notification 8082, metadata web 8090 / gRPC 9090, search web 8091 / gRPC 9091); changing a port is a spine change.

### Additional Requirements

- **Greenfield build, no starter template:** there is no code yet; the Maven multi-module skeleton is established from scratch (frozen Story 1 spec). Parent `pom.xml` at repo root, modules `video-common` + 5 services.
- **Stack pins (spine):** Java 21; Spring Boot 4.1.0; Spring Cloud AWS 4.1.0 (BOM); spring-grpc 1.1.0 (`spring-boot-starter-grpc-server`/`client`); protobuf codegen via `io.github.ascopes:protobuf-maven-plugin` 5.x; `package videolab.v1`, `java_package com.videolab.proto.v1`, `java_multiple_files = true`.
- **Shared contract (video-common):** one `.proto` with `VideoMetadataService` + `VideoSearchService`; JSON event DTOs `VideoUploadedEvent`/`VideoProcessedEvent`; `Names` constants; generated classes packaged into the `video-common` jar.
- **ministack emulation:** docker compose at `localhost:4566` (S3 + SQS + API Gateway v2), image tag pinned ≥ 1.3.6 (path-based `/_aws/execute-api/...` data plane needs it); ministack and its backing redis are the only containers in the root compose — services run as host JVMs and each service's database runs from that service's own repo compose (AD-11).
- **Terraform provisioning:** AWS provider with `endpoints{}` → `http://localhost:4566`, `s3_use_path_style=true`, dummy creds, skip credential validation; local state only (no remote state/modules/workspaces); API Gateway uses a named stage (`dev`) with a deployment; `terraform output` exposes the invoke URL.
- **Bring-up order:** ministack up (root compose) → each record-owning service's `postgres` up (from that service's repo compose, AD-11) → `terraform apply` → services boot last; services surviving a `destroy` + re-apply MUST be restarted (queue/bucket URLs change).
- **Gateway integration addressing:** ministack API Gateway reaches the host-local service facades via `host.docker.internal:<port>`.
- **Event payloads:** SQS payloads are JSON via Jackson; timestamps are epoch-millis `int64` in proto.
- **gRPC client channel names:** pinned `channel.metadata` and `channel.search` in service `application.yml`.
- **Deployment:** one Dockerfile per service, all `eclipse-temurin:21-jre`, expose only normative ports, actuator healthcheck; ffmpeg layer deferred to the processing-service Dockerfile story.
- **Record-store DBs:** PostgreSQL for every record store (spine AD-11) — dev: each record-owning service declares its own `postgres` container in a `docker-compose.yml` inside its own repository (one DB per service — `metadata`, `notification`, `search`), `ddl-auto: update`; the root `docker-compose.yml` holds only the agentic engineering workspace (ministack + redis) and never a service's database; real AWS: Aurora PostgreSQL-compatible via Spring Boot `spring.datasource.*` (not SCAWS auto-config).
- **Status-first ordering:** `UpdateVideoStatus` completes (and metadata acknowledges the transition) before the corresponding event is published; terminal events are published only after the terminal transition is acknowledged.
- **Poison-event rule (derived stores):** consumed event validated against metadata `GetVideo`; event for a `videoId` metadata reports unknown is dropped (not stored / not indexed); `UNAVAILABLE`/deadline errors are retried, never dropped.
- **Error body/envelope:** HTTP error body is `{"error": "<gRPC description>"}`; facade client paths are under `/api/videos/*` with response DTOs in `video-common`.

### UX Design Requirements

No UX design contract exists for this project — no UX-DR extraction applies. This is a backend learning lab with a Bruno-driven test surface; the client-facing surface is exactly the three gateway journeys (upload, status history, search) enumerated in the spine (AD-9).

### FR Coverage Map

- FR1: Epic 1 - Shared gRPC contract (`.proto` with VideoMetadataService + VideoSearchService, VideoInfo canonical)
- FR2: Epic 1 - Shared event DTOs (`video.uploaded`, `video.processed` with eventId + schemaVersion)
- FR3: Epic 1 - Shared names (`com.videolab.common.Names` mirroring Terraform-declared names)
- FR4: Epic 1 - Additive-only contract versioning policy
- FR5: Epic 2 - HTTP multipart upload to the uploads S3 bucket
- FR6: Epic 2 - `videoId` minted exactly once at ingress (upload-service)
- FR7: Epic 2 - Publish `video.uploaded` after record creation
- FR8: Epic 3 - Consume `video-uploaded` (idempotent; redelivered duplicate is a no-op)
- FR9: Epic 3 - Transcode and write processed object to the processed bucket
- FR10: Epic 3 - Drive status transitions (PROCESSING at start, PROCESSED at completion)
- FR11: Epic 3 - Publish exactly one `video.processed` per terminal transition
- FR12: Epic 1 - Create/Get/List video records via gRPC
- FR13: Epic 1 - Idempotent create by ingress-minted id (retry returns existing record)
- FR14: Epic 1 - Enforce the VideoStatus state machine (terminal statuses final)
- FR15: Epic 1 - Standard gRPC status codes mapped to HTTP per AD-7
- FR16: Epic 4 - Consume `video.processed`, record history per unique eventId (poison-event handling)
- FR17: Epic 4 - Query status history for a video
- FR18: Epic 4 - Index PROCESSED videos only (FAILED never indexed; poison handling)
- FR19: Epic 4 - Search by title substring
- FR20: Epic 4 - Rebuild index from metadata ListVideos (gRPC/admin trigger only)
- FR21: Epic 5 - Single client ingress through the API Gateway (no direct service calls)
- FR22: Epic 5 - Route client HTTP to services per the authoritative path table (no edge rewriting)
- FR23: Epic 5 - gRPC stays internal (never gateway-exposed)
- FR24: Epic 5 - No auth on the gateway (lab)
- FR25: Epic 2 - All ministack infra via Terraform (buckets, queues, gateway)
- FR26: Epic 2 - No `aws cli` in setup/teardown

## Epic List

### Epic 1: Platform Foundation — Shared Contract & Metadata Source of Truth

The build compiles (Maven multi-module), the versioned gRPC/event contract is shared by all future services, and the single source-of-truth metadata service enforces the status state machine — the substrate every other service stands on. Testable via gRPC alone (grpcurl).

**FRs covered:** FR-1, FR-2, FR-3, FR-4, FR-12, FR-13, FR-14, FR-15

### Epic 2: Upload Ingest — HTTP → S3 + first event

You can upload a video over HTTP; it lands in S3, gets its one-time `videoId`, an `UPLOADED` record via metadata gRPC, and a `video.uploaded` event. Backing infrastructure (buckets, queues) is provisioned by Terraform, no `aws cli`.

**FRs covered:** FR-5, FR-6, FR-7, FR-25, FR-26

### Epic 3: Video Processing Pipeline — transcode to PROCESSED

Uploaded videos are consumed, transcoded (ffmpeg / demo-mode copy fallback), and advance `UPLOADED → PROCESSING → PROCESSED` with exactly one `video.processed` event per terminal transition.

**FRs covered:** FR-8, FR-9, FR-10, FR-11

### Epic 4: Derived Services — Status History & Title Search

You can query a video's status history and title-search processed videos; the search index is rebuildable from metadata. Both stores are derived purely from events and validated against metadata.

**FRs covered:** FR-16, FR-17, FR-18, FR-19, FR-20

### Epic 5: Single Client Ingress — API Gateway + Bruno Journeys

All three client journeys (upload, status history, search) are exercised through one API Gateway; gRPC stays strictly internal; Bruno targets the gateway only.

**FRs covered:** FR-21, FR-22, FR-23, FR-24

---

## Epic 1: Platform Foundation — Shared Contract & Metadata Source of Truth

The build compiles (Maven multi-module), the versioned gRPC/event contract is shared by all future services, and the single source-of-truth metadata service enforces the status state machine — the substrate every other service stands on. Testable via gRPC alone (grpcurl). Grounded in the frozen spec `spec-video-processing-microservices.md`.

**FRs covered:** FR-1, FR-2, FR-3, FR-4, FR-12, FR-13, FR-14, FR-15

### Story 1.1: Maven multi-module skeleton + shared contract (video-common)

As a developer,
I want a compiling multi-module Maven build with a versioned shared gRPC/event contract and shared names constants,
So that all five services compile against one canonical contract on a stable substrate.

**Acceptance Criteria:**

**Given** an empty repo root,
**When** `mvn -q -DskipTests package` runs at `D:\Projects\aws-lab`,
**Then** the `video-common` module compiles and the build returns SUCCESS
**And** generated proto classes (`VideoMetadataServiceGrpc`, `VideoInfo`) are packaged in the `video-common` jar.

**Given** the parent `pom.xml`,
**When** it is inspected,
**Then** it uses Spring Boot `4.1.0` parent, Java 21, `<packaging>pom</packaging>`, and declares the `video-common` module (metadata-service added in Story 1.2).

**Given** `video.proto`,
**When** compiled,
**Then** it defines `package videolab.v1` / `java_package com.videolab.proto.v1` with `VideoMetadataService` (CreateVideo, UpdateVideoStatus, GetVideo, ListVideos) and `VideoSearchService` (SearchVideos), with `VideoInfo` carrying video_id, title, status, bucket, original_key, processed_key, content_type, size_bytes, duration_seconds, failure_reason, timestamps.

**Given** `video-common`,
**When** inspected,
**Then** it defines `VideoUploadedEvent` and `VideoProcessedEvent` each with a deterministic `eventId` (name-based UUID from `(videoId, status)`) and `schemaVersion` defaulting to 1.

**Given** `Names.java`,
**When** inspected,
**Then** it declares `QUEUE_VIDEO_UPLOADED=video-uploaded`, `QUEUE_VIDEO_PROCESSED=video-processed`, `BUCKET_UPLOADS=video-uploads`, `BUCKET_PROCESSED=video-processed` — no service string-types a name inline.

**Given** the contract,
**When** evolution is needed,
**Then** changes are additive-only; renumber/rename/remove requires a new proto version (e.g. `videolab.v2`), never an in-place edit.

### Story 1.2: Metadata service — the single-writer source of truth

As a developer,
I want a gRPC-only metadata service backed by PostgreSQL that owns the Video record and enforces the status state machine,
So that every other service has one authoritative source of truth for existence and status.

**Acceptance Criteria:**

**Given** the running service,
**When** `grpcurl -plaintext localhost:9090 list` runs,
**Then** `videolab.v1.VideoMetadataService` is listed
**And** actuator `/actuator/health` on `:8090` returns `{"status":"UP"}`.

**Given** a `CreateVideo` request with an ingress-minted `video_id`,
**When** called via grpcurl,
**Then** a record is persisted with `status=UPLOADED` and both epoch-ms timestamps populated,
**And** `GetVideo` returns it.

**Given** `CreateVideo` retried with the same `video_id`,
**When** called again,
**Then** the existing record is returned unchanged (idempotent success)
**And** `ALREADY_EXISTS` is raised only for a genuinely foreign `videoId` collision.

**Given** a record in `UPLOADED`,
**When** `UpdateVideoStatus` sets `PROCESSING` then `PROCESSED`,
**Then** both legal transitions succeed
**And** `updated_at` refreshes.

**Given** a record in terminal `PROCESSED`,
**When** `UpdateVideoStatus` attempts a regression or a transition out of a terminal state,
**Then** gRPC returns `FAILED_PRECONDITION`.

**Given** `UpdateVideoStatus` or `GetVideo` for an unknown `video_id`,
**When** called,
**Then** gRPC returns `NOT_FOUND` with a message.

**Given** `ListVideos(limit)`,
**When** called with no records,
**Then** an empty list returns;
**And** with a limit and more records, at most `limit` entries return.

**Given** `application.yml`,
**When** inspected,
**Then** it pins web port `8090`, gRPC port `9090`, the `metadata` PostgreSQL database URL (`jdbc:postgresql://localhost:5432/metadata`), `ddl-auto: update`, and contains no hardcoded AWS endpoints/credentials.

---

## Epic 2: Upload Ingest — HTTP → S3 + first event

You can upload a video over HTTP; it lands in S3, gets its one-time `videoId`, an `UPLOADED` record via metadata gRPC, and a `video.uploaded` event. Backing infrastructure (buckets, queues) is provisioned by Terraform, no `aws cli`. FR-25's bucket/queue portion lands here; its API Gateway portion is completed in Epic 5.

**FRs covered:** FR-5, FR-6, FR-7, FR-25, FR-26

### Story 2.1: Ministack + Terraform backing infrastructure

As a developer,
I want the S3 buckets and SQS queues provisioned declaratively via Terraform against ministack,
So that the environment is reproducible and every service boots against declared resources.

**Acceptance Criteria:**

**Given** docker compose,
**When** ministack starts,
**Then** it is up at `localhost:4566` with S3 + SQS + API Gateway v2 emulation (image pinned ≥ 1.3.6).

**Given** `terraform/` with the AWS provider targeting `http://localhost:4566` (`endpoints{}`, `s3_use_path_style=true`, dummy creds),
**When** `terraform apply` runs,
**Then** buckets `video-uploads` / `video-processed` and queues `video-uploaded` / `video-processed` exist on ministack.

**Given** the verification story,
**When** it asserts `com.videolab.common.Names` equals the `terraform output` names,
**Then** it passes
**And** the build/verification FAILS on divergence.

**Given** the documented setup/teardown,
**When** inspected,
**Then** it contains no `aws` CLI invocations.

**Given** `terraform destroy` + re-apply,
**When** run,
**Then** the environment rebuilds from the same configuration,
**And** bring-up order is ministack up → `terraform apply` → services boot last.

### Story 2.2: Upload service — HTTP ingest, identity mint, first event

As a developer,
I want an HTTP endpoint that stores a video in S3, mints the one-time `videoId`, creates the `UPLOADED` record, and publishes `video.uploaded`,
So that the pipeline's first stage is complete.

**Acceptance Criteria:**

**Given** upload-service running on `:8080` with metadata reachable via `channel.metadata`,
**When** a client POSTs a multipart video to `/api/videos/upload`,
**Then** the object exists in the `video-uploads` bucket under a key tied to the returned `videoId`.

**Given** the upload response,
**When** inspected,
**Then** it returns a UUID `videoId` minted by upload-service (not metadata), reused across the S3 key, the metadata record, and the event.

**Given** a completed upload,
**When** `GetVideo` is called on metadata,
**Then** the record exists with status `UPLOADED`.

**Given** a successful upload,
**When** the `video-uploaded` queue is inspected,
**Then** a `video.uploaded` event exists carrying `eventId` + `schemaVersion` for that `videoId`.

**Given** `application.yml`,
**When** inspected,
**Then** AWS endpoints/credentials are config-not-code (`spring.cloud.aws.*` → `localhost:4566`, path-style)
**And** `channel.metadata` pins the gRPC target.

**Given** the FAILED-video retry and ingest-leg reconciliation are out of scope,
**When** implemented,
**Then** neither is included in this story (deferred per spine).

---

## Epic 3: Video Processing Pipeline — transcode to PROCESSED

Uploaded videos are consumed, transcoded (ffmpeg / demo-mode copy fallback), and advance `UPLOADED → PROCESSING → PROCESSED` with exactly one `video.processed` event per terminal transition.

**FRs covered:** FR-8, FR-9, FR-10, FR-11

### Story 3.1: Processing service — idempotent consumer, transcode, terminal status, publish

As a developer,
I want an idempotent SQS consumer that transcodes videos and drives them to `PROCESSED` while publishing `video.processed`,
So that uploaded videos progress through the pipeline exactly once.

**Acceptance Criteria:**

**Given** processing-service running with metadata reachable via `channel.metadata` and S3/SQS wired,
**When** a `video.uploaded` event is on the `video-uploaded` queue,
**Then** the service consumes it
**And** the metadata record transitions `UPLOADED → PROCESSING` at transcode start.

**Given** a successful transcode,
**When** it completes,
**Then** a processed object exists in the `video-processed` bucket for the `videoId` (demo-mode copy fallback acceptable when ffmpeg is absent).

**Given** successful completion,
**When** `UpdateVideoStatus` to `PROCESSED` is acknowledged,
**Then** the record transitions to `PROCESSED`
**And** exactly one `video.processed` event is published to `video-processed`.

**Given** a redelivered duplicate `video.uploaded` for an already-processing/terminal video,
**When** the service attempts its transition,
**Then** metadata returns `FAILED_PRECONDITION`
**And** the service acks the message as a no-op — no re-transcode, no metadata lookup.

**Given** the published event,
**When** inspected,
**Then** it carries a deterministic `eventId` (from `(videoId, status)`) + `schemaVersion`, reused on republish so dedupe holds across restarts.

**Given** the FAILED producing path is out of scope,
**When** implemented,
**Then** `FAILED` is exercised via state-machine rules only, not a producing path.

**Given** `application.yml`,
**When** inspected,
**Then** AWS/gRPC wiring is config-not-code
**And** web port `8081` serves actuator/health only.

**Given** ffmpeg is unavailable,
**When** the service runs,
**Then** demo-mode copy fallback is used;
**And** the ffmpeg install strategy stays deferred to the Dockerfile story.

---

## Epic 4: Derived Services — Status History & Title Search

You can query a video's status history and title-search processed videos; the search index is rebuildable from metadata. Both stores are derived purely from events and validated against metadata.

**FRs covered:** FR-16, FR-17, FR-18, FR-19, FR-20

### Story 4.1: Notification service — event-sourced status history

As a developer,
I want a derived, disposable store (PostgreSQL `notification` DB, spine AD-11) that records and serves a video's status history from `video.processed` events,
So that terminal statuses are queryable without touching the record store.

**Acceptance Criteria:**

**Given** notification-service running and consuming `video-processed`,
**When** a `video.processed` event arrives,
**Then** a status history entry is recorded and queryable.

**Given** a redelivered duplicate `eventId`,
**When** the same `video.processed` is consumed again,
**Then** no second history row is appended.

**Given** a poison event where metadata `GetVideo` reports `NOT_FOUND` for the `videoId`,
**When** consumed,
**Then** it is dropped (not stored) and not retried.

**Given** a metadata `UNAVAILABLE`/deadline error during validation,
**When** consuming,
**Then** the message is retried, never dropped.

**Given** a client request for a video's status history,
**When** it hits the notification-service HTTP facade,
**Then** the history shows the terminal events.

**Given** `application.yml`,
**When** inspected,
**Then** web port is `8082`, wiring is config-not-code, `channel.metadata` is used for validation, and the `notification` PostgreSQL DB (`jdbc:postgresql://localhost:5432/notification`) is the history store.

### Story 4.2: Search service — event-sourced title index + rebuild

As a developer,
I want a derived index (PostgreSQL `search` DB, spine AD-11) that makes processed videos title-searchable (`ILIKE`) and is rebuildable from metadata,
So that discovery is a disposable, event-built surface.

**Acceptance Criteria:**

**Given** search-service running and consuming `video-processed`,
**When** a `PROCESSED` event is consumed,
**Then** the video is indexed
**And** appears in a title-substring search.

**Given** a `FAILED` video,
**When** its terminal event is consumed,
**Then** it is never indexed.

**Given** a poison event where metadata reports the `videoId` unknown,
**When** consumed,
**Then** it is dropped, not indexed.

**Given** an empty/cleared index,
**When** a rebuild is triggered over the gRPC/admin surface,
**Then** the index repopulates from metadata `ListVideos`, indexing `PROCESSED` videos only.

**Given** the rebuild trigger,
**When** inspected,
**Then** it is gRPC/admin only — no HTTP rebuild surface, no gateway route.

**Given** a client search,
**When** it hits the search-service HTTP facade on `:8091` with a title substring,
**Then** matching videos return.

**Given** `application.yml`,
**When** inspected,
**Then** web port `8091` / gRPC `9091`, config-not-code, `channel.metadata` for validation/rebuild, and the `search` PostgreSQL DB (`jdbc:postgresql://localhost:5432/search`) is the index store (title-substring via `ILIKE`).

---

## Epic 5: Single Client Ingress — API Gateway + Bruno Journeys

All three client journeys (upload, status history, search) are exercised through one API Gateway; gRPC stays strictly internal; Bruno targets the gateway only. Completes the FR-25 gateway portion.

**FRs covered:** FR-21, FR-22, FR-23, FR-24

### Story 5.1: API Gateway — single ingress via Terraform

As a developer,
I want an API Gateway v2 provisioned by Terraform that routes the three client journeys to the right service with no edge rewriting,
So that the client has exactly one door into the platform.

**Acceptance Criteria:**

**Given** `terraform/` extended with the gateway,
**When** `terraform apply` runs,
**Then** an API Gateway v2 HTTP API exists with named stage `dev` + deployment and routes: `POST /api/videos/upload` → upload-service, `GET /api/videos/{videoId}/history` → notification-service, `GET /api/videos/search` → search-service, integrated via `host.docker.internal:<port>`.

**Given** the gateway route paths,
**When** compared to the facade client paths,
**Then** they are equal — no path rewriting at the edge (authoritative path table).

**Given** `terraform output`,
**When** inspected,
**Then** it exposes the invoke URL (apiId + stage + path).

**Given** a request through the gateway,
**When** it passes,
**Then** responses pass through unchanged (status codes and `{"error": ...}` bodies).

**Given** the gateway routes,
**When** inspected,
**Then** gRPC is never gateway-exposed — HTTP routes only.

**Given** the gateway,
**When** a client calls it,
**Then** no credentials are required (open lab).

**Given** a real multipart upload,
**When** exercised through the gateway,
**Then** it succeeds — a gateway upload is an acceptance gate, not a facade-local test.

### Story 5.2: Bruno journeys through the gateway

As a builder,
I want a Bruno collection that exercises upload, history, and search through the API Gateway only,
So that the single-ingress pattern is demonstrable and teachable.

**Acceptance Criteria:**

**Given** the Bruno collection,
**When** the three journeys (upload, status history, search) run against the gateway URL,
**Then** all succeed and produce the expected outcomes.

**Given** the Bruno requests,
**When** inspected,
**Then** they target the gateway URL only — never `localhost:8080/8081/8082/8090/8091` directly.

**Given** the FR-21 carve-out,
**When** debugging/inspecting,
**Then** direct gRPC to a service's gRPC port remains permitted as a debugging surface.

**Given** a direct client HTTP call to a service port,
**When** considered,
**Then** it is outside the exercised path — not part of the demonstrated client surface.
