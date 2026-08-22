---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
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
- Gateway route table (declared in Terraform): `POST /videos/upload` (multipart/form-data) → upload-handler; `GET /videos/{videoId}/history` → history-query; `GET /videos/search?title=` → search-query. The gateway delivers non-text bodies (incl. multipart) base64 with `isBase64Encoded: true` (floci >= 1.7.0, PR #2203; matches real AWS) — the upload handler decodes, then parses multipart itself.
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

**Success Metrics (PRD §6):**

- **SM-1 — definition of done:** from a clean `terraform destroy` + `apply` with floci running, one upload through the gateway produces a `PROCESSED` metadata record, a status-history entry, and a search hit — all queryable through the gateway — and the execution path demonstrably exercises each target service (API Gateway, Lambda, S3, DynamoDB, EventBridge, Step Functions), verified via Step Functions execution history, Lambda logs, and event records. Verified in Story 4.4.
- **SM-2:** every resource in the environment was created by Terraform — covered by FR-23 / NFR-8 (verified in Story 4.4).
- **SM-3:** the builder can explain each service's role in the pipeline — self-assessed, deliberately no story coverage.

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
**And** the `detail` payload shape is fixed here and consumed unchanged downstream (shim → ASL → transcode → publisher → consumers): `video.uploaded` detail = `{videoId, status, bucket, key}`; `video.processed` detail = `{videoId, status, bucket, originalKey, processedKey}` (AD-6)

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
**Then** the handler parses the multipart body itself (base64-decoded when `isBase64Encoded: true` — floci >= 1.7.0 / real AWS deliver non-text bodies base64)
**And** the handler reads an optional `title` multipart form field, falling back to the uploaded filename when absent, and stores it in the record (FR-10 — this is the field Story 4.2's title search matches on)
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

## Epic 2: Event-Driven Processing Pipeline

The builder uploads a video and watches it process itself: video.uploaded → processing-trigger queue → shim Lambda → Step Functions (status-first ASL with direct service integrations) → transcode → PROCESSED → exactly one video.processed event. Redeliveries are absorbed as no-ops.

### Story 2.1: Transcode Worker Lambda (pure S3 in → S3 out)

As a builder,
I want a transcode Lambda that reads the uploaded object and writes a processed object (demo-mode copy fallback),
So that the pipeline has a pure worker — no status writes, no events — exactly how real pipeline workers look.

**Acceptance Criteria:**

**Given** a video uploaded through the gateway (Epic 1) with its object in `video-uploads`
**When** Terraform declares the `transcode` Lambda (python3.11 zip, env vars for bucket names + `AWS_ENDPOINT_URL`) and its IAM role, and `terraform apply` runs
**Then** the function exists and is invocable

**Given** the transcode Lambda invoked with the domain payload (`videoId`, original key)
**When** it runs
**Then** it reads the object from `video-uploads` and writes the processed object to `video-processed` under a key tied to the same `videoId` (FR-6, demo-mode copy fallback — no ffmpeg)
**And** it performs **no** status writes and publishes **no** events (pure worker, AD-4)
**And** its invocation appears in CloudWatch Logs (NFR-5)

### Story 2.2: Processing State Machine + Event Publisher

As a builder,
I want a Step Functions state machine that drives UPLOADED → PROCESSING → PROCESSED via direct DynamoDB integrations and ends with an event-publisher Lambda emitting exactly one video.processed,
So that status-first ordering is structural (in the ASL, not in code discipline) and I learn direct service integrations.

**Acceptance Criteria:**

**Given** Terraform declares the `event-publisher` Lambda (sole constructor of the `video.processed` envelope via the shared layer — AD-4/AD-6), its IAM role, and the `processing-state-machine` whose ASL is, in order: `Task(dynamodb:updateItem UPLOADED→PROCESSING)` → `Task(lambda:invoke transcode)` → `Task(dynamodb:updateItem →PROCESSED)` → `Task(lambda:invoke event-publisher)`
**When** `terraform apply` runs
**Then** the state machine exists
**And** the ASL's inline condition pairs (`#s = UPLOADED` → `PROCESSING`, `#s = PROCESSING` → `PROCESSED`) mirror the shared layer's legal-transition table exactly (AD-4)

**Given** an `UPLOADED` video (from Epic 1)
**When** I start an execution ad-hoc (`StartExecution` with the domain payload: videoId, status, keys)
**Then** the metadata record transitions `UPLOADED → PROCESSING → PROCESSED` in order, each acknowledged before the next state runs (FR-7)
**And** the processed object exists in `video-processed`
**And** exactly one `video.processed` event is on the bus, carrying `eventId` = UUID5(`videoId`, `PROCESSED`) + `schemaVersion` (FR-8)
**And** the execution is visible in Step Functions history with the `videoId` in its input (FR-5, NFR-5)

**Given** the same execution re-run for the same video (record already `PROCESSED`)
**When** the first `updateItem` condition fails
**Then** the execution fails — no status regression, no second event (FR-11 via ASL; the trigger-leg dedupe of Story 2.3 makes this unreachable in practice)

**Given** any future ASL change
**When** it is applied
**Then** it is done via `terraform apply -replace=aws_sfn_state_machine.<name>` (floci has no `UpdateStateMachine`), documented in the story's notes

### Story 2.3: Trigger Leg — EventBridge Rule, Queue, and Shim

As a builder,
I want video.uploaded to route through a processing-trigger queue to a shim Lambda that starts the state machine with a deterministic execution name,
So that uploading a video automatically processes it — and a republish/redelivery is a dedupe, never a second execution.

**Acceptance Criteria:**

**Given** Terraform declares the `processing-trigger-queue` (SQS), the EventBridge rule matching `video.uploaded` on the custom bus targeting **only** that queue, the `sfn-trigger-shim` Lambda, its IAM role (sqs:ReceiveMessage via ESM, states:StartExecution), and the SQS event-source mapping
**When** `terraform apply` runs
**Then** the wiring exists — and the rule targets the queue, never the state machine directly (floci can't; AD-5)

**Given** the full stack applied
**When** I upload a video through the gateway
**Then** the shim receives the SQS record, unwraps `Records[].body` → EventBridge envelope → `detail`, and calls `StartExecution` with execution name `eb-{eventId}` (deterministic from the event's `eventId`)
**And** a state machine execution starts automatically and runs to `PROCESSED` with exactly one `video.processed` event (FR-5, FR-7, FR-8)

**Given** the same `video.uploaded` redelivered (republish or SQS retry) after the video is already `PROCESSING`/`PROCESSED`
**When** the shim processes it again
**Then** `StartExecution` hits `ExecutionAlreadyExists`, which the shim treats as success (ack) — no second execution, no re-transcode, no status regression (FR-9, NFR-1/2)

**Given** the Bruno collection
**When** I re-run the upload journey and inspect ad-hoc (Step Functions history, Lambda logs)
**Then** exactly one execution named `eb-{eventId}` exists for that video and the full path is traceable through logs (NFR-5)

## Epic 3: Status History Surface

The builder queries a video's recorded terminal-event history through the gateway — history queue + history-consumer with eventId dedupe and poison-event handling, status-history table, history-query Lambda, and the gateway history route.

### Story 3.1: History Consumer — Recording Terminal Events

As a builder,
I want a history-consumer Lambda behind its own SQS queue that appends one status-history entry per unique video.processed event,
So that the pipeline's terminal events leave a queryable, deduplicated audit trail — and I learn queue-based event consumption.

**Acceptance Criteria:**

**Given** Terraform declares the `status-history` table (PK `eventId`, attributes: videoId, status, timestamp), the `history-queue` (SQS), the EventBridge rule matching `video.processed` targeting **only** the history queue, the `history-consumer` Lambda, its IAM role, and the SQS event-source mapping
**When** `terraform apply` runs
**Then** the wiring exists — and the `video.processed` rule now targets the history queue in addition to any existing targets, without altering the `video.uploaded` rule (AD-1: new consumer = new queue + new rule target)

**Given** a video that has processed to `PROCESSED` (Epic 2)
**When** the consumer receives the SQS record
**Then** it unwraps `Records[].body` → EventBridge envelope → `detail`, validates the `videoId` against `video-metadata`, and appends a history entry keyed by `eventId` carrying status, videoId, and timestamp (FR-14)
**And** ad-hoc inspection of `status-history` shows exactly that entry

**Given** the same event redelivered (same `eventId`)
**When** the consumer processes it again
**Then** the duplicate appends nothing — exactly one entry per unique `eventId` (FR-14, NFR-1)

**Given** an event whose `videoId` the metadata table reports unknown
**When** the consumer validates it
**Then** the event is dropped — not stored, message acked, never retried (FR-15 poison handling)

**Given** a transient metadata-unavailable error during validation
**When** the consumer fails the message
**Then** SQS redelivers and the event is retried, never dropped (FR-15)

### Story 3.2: History Query Through the Gateway

As a builder,
I want to GET a video's status history through the gateway,
So that the second client journey works end-to-end and I can see the terminal event recorded for my upload.

**Acceptance Criteria:**

**Given** Terraform declares the `history-query` Lambda, its IAM role, and the gateway route `GET /videos/{videoId}/history` → history-query
**When** `terraform apply` runs
**Then** the route exists alongside the upload route (FR-21 route table grows; responses pass through unchanged)

**Given** a processed video with a recorded history entry (Story 3.1)
**When** I GET `/videos/{videoId}/history` via the gateway
**Then** the response is HTTP 200 with the video's entries, each carrying status, `eventId`, and timestamp (FR-16)

**Given** an unknown `videoId`
**When** I GET its history
**Then** the gateway returns 404 with body `{"error": ...}` (FR-13 semantics, NFR-3)

**Given** the Bruno collection
**When** I add the history request (with assert blocks and poll-with-timeout — the consumer leg is async, so the request retries until the entry appears or the timeout fails the assertion; no fixed sleeps) and run it after the upload journey
**Then** it passes against the gateway URL only, and the returned entries match the ad-hoc `status-history` inspection (FR-22 grows)

## Epic 4: Search Surface & End-to-End Lab Verification

The builder searches processed videos by title through the gateway, rebuilds the index admin-only, and then proves the whole lab: clean terraform destroy + apply plus the full Bruno collection reproduces SM-1 (PROCESSED record + history entry + search hit, every target service demonstrably exercised).

### Story 4.1: Search Consumer — Indexing Processed Videos

As a builder,
I want a search-consumer Lambda behind its own SQS queue that upserts a search-index entry for every PROCESSED event,
So that processed videos become searchable — and I learn status-filtered consumption.

**Acceptance Criteria:**

**Given** Terraform declares the `search-index` table (PK `videoId`, attributes: title, processedKey, indexedAt), the `search-queue` (SQS), the EventBridge rule matching `video.processed` targeting the search queue (added alongside the history queue target — AD-1), the `search-consumer` Lambda, its IAM role, and the SQS event-source mapping
**When** `terraform apply` runs
**Then** the wiring exists — the `video.processed` rule now fans out to both the history and search queues, each consumer behind its own queue

**Given** a video that has processed to `PROCESSED` (Epic 2)
**When** the consumer receives the SQS record
**Then** it unwraps `Records[].body` → envelope → `detail`, checks `status = PROCESSED`, validates the `videoId` against `video-metadata`, and upserts a `search-index` entry keyed by `videoId` with title, processedKey, and indexedAt (FR-17)

**Given** a terminal event with `status = FAILED` (rules only in v1 — exercised by a hand-crafted test event)
**When** the consumer receives it
**Then** it indexes nothing — FAILED videos never appear in the index (FR-17, AD-6 status filter)

**Given** the same event redelivered (same `videoId`)
**When** the consumer processes it again
**Then** the upsert overwrites with the same entry — no duplicates (NFR-1)

**Given** an event whose `videoId` the metadata reports unknown / a transient metadata error
**When** the consumer validates it
**Then** unknown → dropped and acked; transient → retried, never dropped (FR-15 semantics, per FR-17)

### Story 4.2: Title Search Through the Gateway

As a builder,
I want to search processed videos by title substring through the gateway,
So that the third and final client journey works end-to-end.

**Acceptance Criteria:**

**Given** Terraform declares the `search-query` Lambda, its IAM role, and the gateway route `GET /videos/search?title=` → search-query
**When** `terraform apply` runs
**Then** all three routes of the authoritative route table now exist (FR-21 complete)

**Given** indexed processed videos (Story 4.1)
**When** I GET `/videos/search?title=<substring>` via the gateway
**Then** the response is HTTP 200 with the matching processed videos (Scan with contains filter — lab scale, NFR-7) (FR-18)

**Given** a title substring matching nothing
**When** I search
**Then** the response is HTTP 200 with an empty result list (not an error)

**Given** a missing or empty `title` parameter
**When** I search
**Then** the gateway returns 400 with body `{"error": ...}` (NFR-3)

**Given** the Bruno collection
**When** I add the search request (with assert blocks and poll-with-timeout — indexing is async; retry until the hit appears or the timeout fails the assertion; no fixed sleeps) and run it after the upload journey
**Then** it passes against the gateway URL only and returns the uploaded video by title substring (FR-22 grows)

### Story 4.3: Admin-Only Index Rebuild

As a builder,
I want to rebuild the search index from the metadata table via a direct Lambda invoke,
So that I can prove the index is disposable and derived — and the rebuild stays admin-only with no client-facing surface.

**Acceptance Criteria:**

**Given** Terraform declares the `search-rebuild` Lambda and its IAM role — and **no** gateway route, rule, or queue references it
**When** `terraform apply` runs
**Then** the function exists reachable only by direct invoke (FR-19, AD dependency direction: search-rebuild is the sole repopulator of `search-index`)

**Given** the `search-index` table cleared (ad-hoc) and processed videos present in `video-metadata`
**When** I invoke `search-rebuild` directly (`lambda invoke`, ad-hoc — allowed inspection/admin, not setup)
**Then** it scans `video-metadata`, repopulates the index with `PROCESSED` videos only, and a subsequent gateway search returns them (FR-19)

**Given** the Bruno collection and the gateway route table
**When** I inspect both
**Then** no request or route exposes the rebuild — the constraint "no client-facing rebuild surface" holds structurally (FR-19)

### Story 4.4: End-to-End Lab Verification (SM-1)

As a builder,
I want a clean terraform destroy + apply followed by the full Bruno collection to reproduce the entire pipeline,
So that I've proven the lab is reproducible and every target AWS service is demonstrably exercised — SM-1, the definition of done.

**Acceptance Criteria:**

**Given** floci running and the complete Terraform configuration
**When** I run `terraform destroy` then `terraform apply`
**Then** the entire environment rebuilds from the same configuration with no manual steps and no `aws` CLI in the procedure (FR-23, FR-24, NFR-6, NFR-8)

**Given** the fresh environment
**When** I run the complete Bruno collection via `bru run` (upload → history → search, gateway URL only; the history and search requests use the poll-with-timeout pattern from Stories 3.2/4.2, since the pipeline between upload and the derived surfaces is async)
**Then** every request passes its assertions
**And** one upload produces: a `PROCESSED` metadata record, a status-history entry, and a search hit — all queryable through the gateway (SM-1)

**Given** that run
**When** I inspect ad-hoc (Step Functions execution history, Lambda CloudWatch Logs, event records)
**Then** the full path of the video is traceable through logs and the execution demonstrably exercises each target service: API Gateway, Lambda, S3, DynamoDB, EventBridge, Step Functions (SM-1, NFR-5)

**Given** the README / setup documentation
**When** I review it
**Then** it documents the fixed bring-up order (`docker compose up` → `terraform apply` → exercise via Bruno through the `_aws/execute-api` URL with the `apiId` output), the `-replace` caveat for ASL changes, and contains no `aws` CLI in setup/teardown (FR-24, AD-8/AD-9)
