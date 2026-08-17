---
stepsCompleted: [step-01-validate-prerequisites]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-serverless-video-processing-2026-08-17/prd.md
  - _bmad-output/planning-artifacts/prds/prd-serverless-video-processing-2026-08-17/addendum.md
  - _bmad-output/planning-artifacts/architecture/architecture-serverless-video-processing-2026-08-17/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/architecture/architecture-serverless-video-processing-2026-08-17/solution-design.md
---

# Serverless Video Processing Platform - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for the Serverless Video Processing Platform, decomposing the requirements from the PRD and Architecture requirements into implementable stories. There is no UI — the client surface is HTTP via API Gateway v2 exercised with Bruno/curl — so no UX design requirements apply.

## Requirements Inventory

### Functional Requirements

**Upload Ingest (API Gateway + Lambda + S3 + DynamoDB)**

FR-1: A client uploads a video file over HTTP multipart through the API Gateway; the object lands in the uploads bucket (Bruno/curl POST to the gateway upload path returns HTTP 2xx and the object exists in the uploads bucket).
FR-2: The upload handler generates the videoId (UUID) exactly once at ingress; the same id is used across the S3 key, the metadata record, and the emitted event.
FR-3: On successful upload, a metadata record is created in DynamoDB with status UPLOADED and both created/updated timestamps populated.
FR-4: After the record is created, a `video.uploaded` event is emitted carrying the videoId and a deterministic eventId, and the processing workflow picks it up.

**Processing Pipeline (EventBridge + Step Functions + Lambda + S3)**

FR-5: The `video.uploaded` event triggers a Step Functions state machine that drives the processing workflow (execution visible in Step Functions history with the videoId in its input).
FR-6: A Lambda function produces a processed object in the processed bucket under a key tied to the videoId; demo-mode copy fallback is acceptable when ffmpeg is absent (real ffmpeg is a documented future extension).
FR-7: Status advances UPLOADED → PROCESSING at transcode start and → PROCESSED at completion; each transition is acknowledged in the metadata store before the corresponding event is published.
FR-8: Exactly one `video.processed` event is emitted per terminal transition; the deterministic eventId (derived from (videoId, status)) makes retries idempotent — a republish is a dedupe, never a duplicate, and holds across restarts.
FR-9: A redelivered `video.uploaded` for an already-processing/terminal video is a no-op — no re-transcode, no status regression.

**Metadata & State Machine (DynamoDB + Lambda)**

FR-10: DynamoDB holds the video record: videoId, title, status, bucket, original key, processed key, content type, size, duration, failure reason, timestamps; a record read returns all carried fields for a known videoId.
FR-11: Only legal transitions are accepted (UPLOADED → PROCESSING → PROCESSED | FAILED); terminal statuses are final; illegal transitions are rejected; same-status re-assertion is idempotent.
FR-12: Record creation is idempotent by videoId — a retry returns the existing record unchanged.
FR-13: Reads/updates for an unknown videoId return a not-found error, not a silent success.

**Status History (EventBridge + Lambda + DynamoDB)**

FR-14: A consumer records a history entry for each consumed `video.processed` event; a duplicate eventId appends nothing (exactly one entry per unique eventId).
FR-15: Events whose videoId the metadata reports unknown are dropped (not stored, not retried); transient metadata-unavailable errors are retried, never dropped.
FR-16: A client can query a video's recorded status history through the gateway; each entry carries status, eventId, and timestamp.

**Search (EventBridge + Lambda + DynamoDB)**

FR-17: A consumer indexes a video when its PROCESSED event is consumed; FAILED videos are never indexed; poison-event handling per FR-15.
FR-18: A client can search processed videos by title substring through the gateway.
FR-19: The search index is rebuildable from the metadata table; the rebuild trigger is admin-only (no client-facing surface, no gateway route).

**Client Ingress (API Gateway v2)**

FR-20: All client HTTP (upload, status history, search) goes through one API Gateway; no auth.
FR-21: Gateway routes map 1:1 to the three client journeys (upload, history, search); responses pass through unchanged (status codes and `{"error": ...}` bodies).
FR-22: A reproducible test collection (Bruno or curl scripts) exercises all three journeys through the gateway only — never backend endpoints directly; running it against a fresh `terraform apply` reproduces SM-1 end-to-end.

**Infrastructure as Code (Terraform)**

FR-23: Every resource (buckets, tables, functions, state machine, event bus/rules, gateway routes/integrations/stage, IAM roles) is declared in Terraform and created by `terraform apply` against floci; `terraform destroy` + re-apply rebuilds the entire environment from the same configuration.
FR-24: Setup/teardown uses Terraform commands only; `aws cli` is permitted for ad-hoc PoC/inspection only, never in the documented setup/teardown procedure.

### NonFunctional Requirements

NFR-1: Consumer idempotency — every event consumer keys dedupe on the deterministic eventId derived from (videoId, status); redelivery produces no duplicate records and no re-work.
NFR-2: Exactly-once emission per transition — deterministic, name-based eventId is restart-proof; a retry is an idempotent republish, never a new id.
NFR-3: Error semantics — client-facing HTTP surfaces return appropriate status codes (400/404/409/500) with body `{"error": "<message>"}`.
NFR-4: Config not code — no endpoint, region, credential, or resource name is hardcoded in function code; all come from Lambda environment variables and Terraform-declared names.
NFR-5: Observability — CloudWatch Logs capture every Lambda invocation; Step Functions execution history is inspectable; the full path of one video is traceable through logs.
NFR-6: Reproducible environment — verification is a clean `terraform destroy` + `apply` against floci plus a smoke upload run; no CI/CD in the lab.
NFR-7: Performance non-goal — no optimization for throughput, latency, scale, or production resilience; adding such polish is scope creep against the learning goal.
NFR-8: Terraform-only setup/teardown — all resource creation is attributable to the Terraform configuration.

### Additional Requirements

**Starter / greenfield note (impacts Epic 1 Story 1):** Architecture specifies NO starter template — this is a greenfield repo. The structural seed defines the layout to scaffold: `docker-compose.yaml` (floci only), `terraform/`, `lambdas/_shared/` + one dir per function (`upload-handler`, `sfn-trigger-shim`, `transcode`, `event-publisher`, `history-consumer`, `search-consumer`, `history-query`, `search-query`, `search-rebuild`), and `bruno/` for the test collection.

**Infrastructure & platform (from Architecture AD-8, AD-9):**

- floci 1.6.0 runs via Docker at localhost:4566, no auth token, dummy credentials, region us-east-1, local Terraform state; `docker-compose.yaml` MUST mount `/var/run/docker.sock` into the floci container or no Lambda runs.
- Terraform >= 1.6.0 with hashicorp/aws provider ~> 5.0 targeting `http://localhost:4566`, `s3_use_path_style=true`, skip credential validation; the provider `endpoints{}` block must list every service used (missing endpoint → InvalidClientTokenId 403).
- Fixed bring-up order: `docker compose up` → `terraform apply` → exercise via Bruno/curl through the gateway only; `terraform destroy` + re-apply rebuilds everything.
- floci does not support `UpdateStateMachine` — any state-machine definition change requires `terraform apply -replace=aws_sfn_state_machine.<name>`.
- API Gateway v2 data plane is reachable only at `http://localhost:4566/_aws/execute-api/{apiId}/{stage}/{path}` — the Terraform-output invoke URL does not resolve locally; `apiId` must be exposed as a Terraform output and used by the test collection/README.
- Lambda runtime python3.11, zip-packaged; function-to-floci calls use `AWS_ENDPOINT_URL` env var (resolves to `host.docker.internal:4566` from inside Lambda containers).
- boto3 is assumed present in the floci Lambda runtime image but unverified — must be confirmed in the first real function story; stdlib-only urllib fallback is proven by the spike.

**Integration workarounds mandated by floci gaps (spike-verified):**

- EventBridge CANNOT target Step Functions state machines → `video.uploaded` routes to a processing-trigger SQS queue consumed by a shim Lambda that calls `StartExecution` with deterministic execution name `eb-{eventId}`; `ExecutionAlreadyExists` is treated as success (dedupe).
- Step Functions direct integration `arn:aws:states:::events:putEvents` is UNSUPPORTED → a dedicated event-publisher Lambda is the sole constructor and publisher of the `video.processed` envelope; the ASL passes it only the domain payload.

**Architecture rules binding implementation (AD-1…AD-9):**

- Event backbone: one custom EventBridge bus; normative routing — `video.uploaded` → processing-trigger queue only; `video.processed` → history queue and search queue only; one SQS queue per consumer; no SNS; new consumer = new queue + new rule target.
- No metadata service-Lambda: status transitions enforced by DynamoDB conditional writes (`UpdateItem` with `ConditionExpression: #s = :expected`) issued through a shared access layer (`lambdas/_shared/`); create is idempotent via `PutItem` with `attribute_not_exists(videoId)`; the shared layer is the only code that knows the legal-transition table; UPLOADED minted only by the upload handler, PROCESSING/PROCESSED/FAILED only by the processing state machine.
- Three DynamoDB tables, one entity per table: `video-metadata` (PK videoId, source of truth), `status-history` (PK eventId, append-only, derived), `search-index` (PK videoId, upsert, derived); derived tables are disposable/rebuildable; title-substring search = Scan with contains filter; no function reads a derived table to answer a question the metadata table owns.
- Processing state machine ASL, in order: `Task(dynamodb:updateItem UPLOADED→PROCESSING)` → `Task(lambda:invoke transcode)` → `Task(dynamodb:updateItem →PROCESSED)` → `Task(lambda:invoke event-publisher)`; transcode Lambda is a pure worker (S3 in → S3 out, no status writes, no events); ASL inline condition pairs MUST mirror the shared layer's legal-transition table (a transition-table change is one coordinated ASL + shared-layer change).
- Identity & events: videoId = UUID minted once at ingress; eventId = deterministic name-based UUID (UUID5) from `(videoId, status)`; every event carries eventId + schemaVersion; event names verb-in-past; publisher allow-list — only upload-handler publishes `video.uploaded`, only event-publisher publishes `video.processed`; consumers unwrap SQS `Records[].body` = JSON-stringified EventBridge envelope (`body → detail`).
- Status-filtered consumption: search consumer indexes only status = PROCESSED events; history consumer records every terminal event; poison handling — unknown videoId dropped (successful negative lookup), transient errors retried.
- Error mapping in shared layer: ConditionalCheckFailedException on transition → 409; unknown videoId → 404; malformed input → 400; else 500; gateway passes responses through unchanged (no remapping at the edge); no auth anywhere.
- Gateway route table (declared in Terraform): `POST /videos/upload` (multipart/form-data) → upload-handler; `GET /videos/{videoId}/history` → history-query; `GET /videos/search?title=` → search-query. The gateway delivers multipart bodies RAW (isBase64Encoded: false) — the upload handler parses multipart itself, never assumes base64.
- Naming conventions: buckets `video-uploads` / `video-processed`; tables `video-metadata` / `status-history` / `search-index`; queues `processing-trigger-queue` / `history-queue` / `search-queue`; one dir per function under `lambdas/`; all names declared in Terraform and consumed via env vars, never string-typed in code.
- Dependency direction: no function touches a store it does not own; the state machine is the only mutator of video-metadata status after ingress; derived tables have exactly one writer each (history-consumer, search-consumer, search-rebuild); search-rebuild is admin-only direct invoke with no gateway route.
- Observability: print/structlog to CloudWatch Logs via floci; Step Functions execution history inspectable.

**Explicitly deferred (NOT in scope for any story):** presigned-URL ingest (v2 variant), FAILED-producing path, real ffmpeg transcoding, single-table DynamoDB design, event-schema versioning policy, SQS DLQ/retry/redrive policy, ingest-leg reconciliation, real AWS deployment, CI/CD, remote Terraform state, modules/workspaces.

### UX Design Requirements

None — this project has no UI. All client interaction is HTTP requests (Bruno/curl) against API Gateway v2 routes.

### FR Coverage Map

FR-1: Epic 1 — HTTP multipart upload through the gateway lands the object in the uploads bucket
FR-2: Epic 1 — videoId minted once at ingress, reused across S3 key, record, and event
FR-3: Epic 1 — UPLOADED metadata record created with timestamps
FR-4: Epic 1 — video.uploaded emitted with deterministic eventId
FR-5: Epic 2 — video.uploaded triggers the Step Functions state machine
FR-6: Epic 2 — transcode Lambda produces the processed object (demo-mode copy fallback)
FR-7: Epic 2 — status transitions UPLOADED → PROCESSING → PROCESSED, acknowledged before events
FR-8: Epic 2 — exactly one video.processed per terminal transition; republish is a dedupe
FR-9: Epic 2 — redelivered video.uploaded is a no-op (no re-transcode, no regression)
FR-10: Epic 1 — video-metadata table holds the full record shape
FR-11: Epic 1 — legal-transition enforcement via conditional writes (shared layer); exercised by Epic 2's ASL condition pairs
FR-12: Epic 1 — idempotent create by videoId
FR-13: Epic 1 — not-found semantics for unknown videoId
FR-14: Epic 3 — history entry per unique eventId; duplicate appends nothing
FR-15: Epic 3 — poison events dropped; transient errors retried
FR-16: Epic 3 — status history queryable through the gateway
FR-17: Epic 4 — PROCESSED videos indexed; FAILED never indexed
FR-18: Epic 4 — title-substring search through the gateway
FR-19: Epic 4 — admin-only rebuild, no client-facing surface
FR-20: Epic 1 — single gateway ingress, no auth (founded in E1; routes added in E3/E4)
FR-21: Epic 1 — gateway routes map 1:1 to journeys, responses pass through unchanged (route table grows in E3/E4)
FR-22: Epic 1 → Epic 4 — Bruno collection founded with the upload request in E1, grows per journey, completed and verified against SM-1 in E4
FR-23: Epic 1 — everything declared in Terraform; rebuild-from-destroy proven in Epic 4
FR-24: Epic 1 — Terraform-only setup/teardown discipline, maintained by all epics

NFR cross-cutting: NFR-1 (consumer idempotency) Epics 2–4; NFR-2 (exactly-once emission) Epics 1–2; NFR-3 (error semantics) all gateway-facing surfaces; NFR-4 (config not code) all epics; NFR-5 (observability) all epics; NFR-6 (reproducible environment) verified in Epic 4; NFR-7 (performance non-goal) global scope guard; NFR-8 (Terraform-only) all epics.

## Epic List

### Epic 1: Lab Foundation & Video Upload Ingest
The builder brings up the lab (docker compose up → terraform apply) and uploads a video through the gateway — the object lands in S3, an UPLOADED record exists in DynamoDB, and video.uploaded is on the EventBridge bus. Includes the environment bootstrap (compose + Terraform provider skeleton), the shared access layer (state machine, event shapes, error mapping, clients), the video-metadata table, and the API Gateway with its upload route.
**FRs covered:** FR-1, FR-2, FR-3, FR-4, FR-10, FR-11, FR-12, FR-13, FR-20, FR-21, FR-23, FR-24 (+FR-22 upload request)

### Epic 2: Event-Driven Processing Pipeline
The builder uploads a video and watches it process itself: video.uploaded → processing-trigger queue → shim Lambda → Step Functions (status-first ASL with direct service integrations) → transcode → PROCESSED → exactly one video.processed event. Redeliveries are absorbed as no-ops.
**FRs covered:** FR-5, FR-6, FR-7, FR-8, FR-9 (FR-11 exercised via the ASL's inline condition pairs)

### Epic 3: Status History Surface
The builder queries a video's recorded terminal-event history through the gateway — history queue + history-consumer with eventId dedupe and poison-event handling, status-history table, history-query Lambda, and the gateway history route.
**FRs covered:** FR-14, FR-15, FR-16

### Epic 4: Search Surface & End-to-End Lab Verification
The builder searches processed videos by title through the gateway, rebuilds the index admin-only, and then proves the whole lab: clean terraform destroy + apply plus the full Bruno collection reproduces SM-1 (PROCESSED record + history entry + search hit, every target service demonstrably exercised).
**FRs covered:** FR-17, FR-18, FR-19, FR-22 (collection completed), SM-1/NFR-6 final verification

**Dependencies:** Epic 1 → Epic 2 → Epic 3 → Epic 4. Epics 3 and 4 are technically parallel after Epic 2 but sequenced for incremental value; FR-22 completion and SM-1 verification deliberately land last.

## Epic 1: Lab Foundation & Video Upload Ingest

The builder brings up the lab (docker compose up → terraform apply) and uploads a video through the gateway — the object lands in S3, an UPLOADED record exists in DynamoDB, and video.uploaded is on the EventBridge bus. Includes the environment bootstrap (compose + Terraform provider skeleton), the shared access layer (state machine, event shapes, error mapping, clients), the video-metadata table, and the API Gateway with its upload route.

### Story 1.1: Lab Environment Bootstrap (floci + Terraform skeleton)

As a builder,
I want `docker compose up` to start floci and a Terraform skeleton that targets it,
So that I have a reproducible, Terraform-only foundation for every later story.

**Acceptance Criteria:**

**Given** the greenfield repo with the structural seed scaffolded (`docker-compose.yaml`, `terraform/`)
**When** I run `docker compose up`
**Then** floci 1.6.0 is healthy at `localhost:4566`
**And** `/var/run/docker.sock` is mounted into the floci container (no Lambda can run without it)

**Given** the Terraform skeleton
**When** I inspect `terraform/`
**Then** the AWS provider targets `http://localhost:4566` with dummy credentials, `s3_use_path_style=true`, skipped credential validation, and an `endpoints{}` block listing every service the platform will use (s3, sqs, dynamodb, lambda, states, events, apigatewayv2, iam, cloudwatch, sts)
**And** no resource is declared yet — this story creates the substrate only

**Given** floci running
**When** I run `terraform apply` and then `terraform destroy`
**Then** both succeed with zero resources created or destroyed beyond the empty state
**And** the documented setup/teardown contains no `aws` CLI invocations (FR-23, FR-24)

### Story 1.2: Shared Access Layer (`lambdas/_shared/`)

As a builder,
I want a shared access layer holding the status state machine, event envelope construction, error mapping, and service clients,
So that every function enforces identical transition rules, event shapes, and error semantics — and I learn DynamoDB conditional writes as the enforcement point.

**Acceptance Criteria:**

**Given** `lambdas/_shared/` implemented
**When** a function calls the layer's transition helper with a legal transition (`UPLOADED→PROCESSING`, `PROCESSING→PROCESSED`, `PROCESSING→FAILED`)
**Then** it issues `UpdateItem` with `ConditionExpression: #s = :expected` and succeeds
**And** an illegal transition or a transition out of a terminal status raises the layer's conflict error (table rejects it — FR-11)
**And** re-asserting the current status succeeds idempotently with no event side effect

**Given** the layer's create helper
**When** it is called twice with the same `videoId`
**Then** the first call creates the record via `PutItem` with `ConditionExpression: attribute_not_exists(videoId)` and the second returns the existing record unchanged (FR-12)

**Given** the layer's event helpers
**When** an envelope is built for `(videoId, status)`
**Then** `eventId` is the deterministic name-based UUID5 of `(videoId, status)` — identical across calls and restarts (NFR-2)
**And** the envelope carries `eventId` + `schemaVersion` + `detail`, with verb-in-past event names

**Given** the layer's error mapping
**When** `ConditionalCheckFailedException` on a transition, unknown `videoId`, malformed input, or any other error occurs
**Then** it maps to 409, 404, 400, and 500 respectively, with body `{"error": "<message>"}` (NFR-3)

**Given** the layer's client factories read `AWS_ENDPOINT_URL` and resource names from environment variables only (NFR-4)
**When** a smoke Lambda using the layer runs in floci's real Docker runtime
**Then** boto3 availability in the runtime image is confirmed — or the proven stdlib/urllib fallback is wired in and documented (Architecture Deferred item)

### Story 1.3: Upload Journey Through the Gateway

As a builder,
I want to POST a video as multipart through the API Gateway,
So that the object lands in S3, an UPLOADED record exists in DynamoDB, and video.uploaded is on the bus — the first leg of the pipeline works end-to-end.

**Acceptance Criteria:**

**Given** the lab applied (Story 1.1) and the shared layer available (Story 1.2)
**When** Terraform declares the `video-uploads` bucket, `video-metadata` table (PK `videoId`, full record shape per FR-10), the `upload-handler` Lambda (python3.11 zip, env vars for endpoint/names/bus), its IAM role, the EventBridge custom bus, and API Gateway v2 with route `POST /videos/upload` → upload-handler
**Then** `terraform apply` creates all of it (FR-23)
**And** `apiId` is exposed as a Terraform output, since the data plane resolves only at `http://localhost:4566/_aws/execute-api/{apiId}/{stage}/{path}`

**Given** the gateway running
**When** I POST a multipart/form-data video to `/videos/upload` (via the `_aws/execute-api` URL)
**Then** the handler parses the raw multipart body itself (`isBase64Encoded: false` — never assumes base64)
**And** the response is HTTP 2xx returning the minted `videoId` (UUID)
**And** the object exists in `video-uploads` under a key containing that same `videoId` (FR-1, FR-2)
**And** the `video-metadata` record exists with status `UPLOADED` and created/updated timestamps populated (FR-3)
**And** a `video.uploaded` event with deterministic `eventId` is on the EventBridge bus (FR-4)

**Given** the Bruno collection (`bruno/`) with an environment file holding the gateway base URL (from the Terraform `apiId` output) and assert blocks on each request
**When** I run the collection via `bru run` (CLI) against a fresh `terraform apply`
**Then** every request passes its assertions (status codes, `videoId` returned)
**And** ad-hoc inspection (aws cli against `localhost:4566`) confirms the S3 object, the UPLOADED record, and the `video.uploaded` event exist
**And** no request in the collection targets anything but the gateway base URL (FR-22 seed)

**Given** a malformed request (missing file / unparseable multipart)
**When** I POST it to `/videos/upload`
**Then** the gateway returns 400 with body `{"error": ...}`, passed through unchanged (NFR-3, FR-21)
