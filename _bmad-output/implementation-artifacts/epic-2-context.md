# Epic 2: Event-Driven Processing Pipeline

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Make an uploaded video process itself: the `video.uploaded` event emitted by Epic 1's upload leg is routed through a queue-based shim into a Step Functions state machine that drives the status UPLOADED → PROCESSING → PROCESSED via direct DynamoDB integrations, invokes a pure transcode worker (demo-mode copy fallback — no ffmpeg), and ends with exactly one `video.processed` event on the bus. Redeliveries and republishes are absorbed as no-ops. This is the epic where the builder learns Step Functions direct service integrations, status-first orchestration in ASL, and deterministic-execution-name dedupe — under two binding floci workarounds (no EventBridge→SFN targets, no `events:putEvents` direct integration).

## Stories

- Story 2.1: Transcode Worker Lambda (pure S3 in → S3 out)
- Story 2.2: Processing State Machine + Event Publisher
- Story 2.3: Trigger Leg — EventBridge Rule, Queue, and Shim

## Requirements & Constraints

- The transcode worker reads the uploaded object from `video-uploads` and writes a processed object to `video-processed` under a key tied to the same videoId; demo-mode copy fallback stands in for real ffmpeg (deferred). It performs **no** status writes and publishes **no** events — a pure worker.
- Status transitions UPLOADED → PROCESSING → PROCESSED, each acknowledged in DynamoDB before the next state runs; the terminal event is published only after the terminal transition is acknowledged.
- Exactly one `video.processed` event per terminal transition: eventId is the deterministic UUID5 of (videoId, status) — a republish is a dedupe, never a duplicate, and holds across restarts.
- A redelivered `video.uploaded` for an already-processing/terminal video is a no-op: no re-transcode, no status regression, no second execution.
- Every Lambda invocation must appear in CloudWatch Logs; the full path of one video must be traceable through logs and Step Functions execution history.
- Config-not-code: no endpoint, region, credential, or resource name hardcoded in function code — everything from Terraform-set env vars.
- Everything is declared in Terraform and created by `terraform apply` against floci; no `aws` CLI in setup/teardown (ad-hoc inspection only — and the local aws CLI shim is broken, use local boto3 against `localhost:4566`).

## Technical Decisions

- **Processing state machine ASL, in order:** `Task(dynamodb:updateItem UPLOADED→PROCESSING)` → `Task(lambda:invoke transcode)` → `Task(dynamodb:updateItem →PROCESSED)` → `Task(lambda:invoke event-publisher)`. Status-first ordering is structural (in the ASL, not code discipline). The ASL's inline condition pairs MUST mirror the shared layer's legal-transition table exactly; a transition-table change is one coordinated ASL + shared-layer change.
- **Transcode is a pure worker** (S3 in → S3 out); the **event-publisher Lambda is the sole constructor of the `video.processed` envelope** (via the shared layer); the ASL passes it only the domain payload (videoId, status, keys). floci does not support the `arn:aws:states:::events:putEvents` direct integration — the publisher Lambda is mandatory.
- **Trigger leg (floci constraint):** EventBridge cannot target Step Functions state machines. `video.uploaded` → `processing-trigger-queue` (SQS) → `sfn-trigger-shim` Lambda → `StartExecution` with deterministic execution name `eb-{eventId}`; `ExecutionAlreadyExists` is treated as success (dedupe).
- **Event shapes:** consumers unwrap SQS `Records[].body` = JSON-stringified EventBridge envelope (`body → detail`). `video.uploaded` detail = `{videoId, status, bucket, key}`; `video.processed` detail = `{videoId, status, bucket, originalKey, processedKey}`. Every envelope carries `eventId` + `schemaVersion`.
- **floci platform facts:** no `UpdateStateMachine` — any ASL change requires `terraform apply -replace=aws_sfn_state_machine.<name>`; the Terraform provider `endpoints{}` block must list every service used; Lambda containers reach floci via `AWS_ENDPOINT_URL` (`host.docker.internal:4566`).
- **Conventions:** Lambda runtime python3.11, zip-packaged, stdlib-only handler code with all service access through the shared layer (`lambdas/_shared/` packed into each zip as `shared/`); one directory per function under `lambdas/` (underscore package names for importability, hyphenated Terraform `function_name`); queues named `processing-trigger-queue`; all names declared in Terraform and consumed via env vars.

## Cross-Story Dependencies

- Epic 2 builds entirely on Epic 1 outputs: `video-uploads` bucket, `video-metadata` table, EventBridge custom bus, the shared access layer, and the `video.uploaded` event emitted by the upload handler.
- Story 2.1 (transcode worker) is invoked by Story 2.2's state machine; Story 2.2's event-publisher output is consumed by Epic 3 (history) and Epic 4 (search); Story 2.3 wires the automatic trigger so an upload processes itself end-to-end.
- The FAILED-producing path is deferred — nothing in v1 produces FAILED; the state-machine rules for it exist but are not exercised.
