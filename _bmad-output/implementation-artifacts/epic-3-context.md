# Epic 3 Context: Status History Surface

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give the pipeline's terminal events a queryable, deduplicated audit trail: a `history-consumer` Lambda behind its own SQS queue appends one `status-history` entry per unique `video.processed` event (dedupe on `eventId`, poison events dropped, transient errors retried), and a `history-query` Lambda exposes that trail through the gateway route `GET /videos/{videoId}/history`. This is the epic where the builder learns queue-based event consumption, eventId-keyed idempotent writes, and poison-event handling — and where the second client journey (history query) works end-to-end.

## Stories

- Story 3.1: History Consumer — Recording Terminal Events
- Story 3.2: History Query Through the Gateway

## Requirements & Constraints

- The history consumer records a history entry for every consumed `video.processed` event; a duplicate `eventId` appends nothing — exactly one entry per unique event, redelivery produces no duplicate records and no re-work.
- Poison handling: an event whose `videoId` the metadata table reports unknown is dropped — not stored, message acked, never retried. A transient metadata-unavailable error fails the message so SQS redelivers and the event is retried, never dropped.
- The history consumer records every terminal event it consumes (no status filtering — unlike the Epic 4 search consumer, which indexes only `PROCESSED`).
- A NEW `video.processed` rule (`video-processed-to-history`) targets the history queue, without altering the `video.uploaded` rule or any existing rule; a new consumer = new queue + new rule, never a change to an existing consumer.
- The gateway history query returns the video's entries, each carrying status, `eventId`, and timestamp; an unknown `videoId` returns 404 with body `{"error": ...}`. Gateway responses pass through unchanged (status codes and error bodies).
- The Bruno collection grows a history request with assert blocks and poll-with-timeout (the consumer leg is async — retry until the entry appears or the timeout fails the assertion; no fixed sleeps), passing against the gateway URL only.
- Consumer idempotency keys on the deterministic `eventId` derived from `(videoId, status)`; timestamps are ISO-8601 UTC strings.
- Config-not-code: no endpoint, region, credential, or resource name hardcoded in function code — everything from Terraform-set env vars. Everything declared in Terraform; no `aws` CLI in setup/teardown (ad-hoc inspection via local boto3 against `localhost:4566`).

## Technical Decisions

- **Event backbone routing is normative:** `video.processed` → history queue and search queue only. Every consumer sits behind its own SQS queue (at-least-once); no consumer is invoked directly by the bus.
- **Wire Detail shape (as-built, reconciled 2026-08-21):** publishers put a FLAT Detail on the bus — envelope fields with detail fields promoted to the top level: `{eventId, schemaVersion, videoId, status, bucket, key, detail}` (and `originalKey`/`processedKey` in place of `key` for `video.processed`). The flat view is canonical for consumers; the nested `detail` object stays intact for envelope-shaped readers. SQS-delivered events arrive as `Records[].body` = JSON-stringified EventBridge envelope — consumers unwrap `body → detail`.
- **Tables:** `status-history` (PK `eventId`, append-only, derived, disposable/rebuildable from `video-metadata`; attributes include videoId, status, timestamp). Derived tables have exactly one writer each — history-consumer is the sole writer of `status-history`. No function reads a derived table to answer a question the metadata table owns.
- **Gateway data plane:** reached at `http://localhost:4566/_aws/execute-api/{apiId}/{stage}/{path}` — the Terraform-output invoke URL does not resolve locally; `apiId` is a Terraform output.
- **floci platform facts:** the Terraform provider `endpoints{}` block must list every service used; Lambda containers reach floci via `AWS_ENDPOINT_URL` (`host.docker.internal:4566`); EventBridge cannot target Step Functions (irrelevant here — history leg is bus → queue → Lambda, natively supported).
- **Conventions:** Lambda runtime python3.11, zip-packaged, stdlib-only handler code with all service access through the shared layer (`lambdas/_shared/` packed into each zip as `shared/`); one directory per function under `lambdas/` (underscore package names for importability, hyphenated Terraform `function_name`); queues named `<purpose>-queue` (`history-queue`); all names declared in Terraform and consumed via env vars.

## Cross-Story Dependencies

- Epic 3 builds on Epic 2's output: the `video.processed` event emitted by the processing state machine's event-publisher Lambda is the sole input to the history leg; `video-metadata` (Epic 1) is the validation source for poison-event handling.
- Story 3.1 (consumer + table + queue wiring) must land before Story 3.2 (query surface) — the query reads what the consumer writes.
- Epic 4's search consumer follows the same queue-per-consumer pattern against the same `video.processed` rule (its queue is added as another target); the history leg must not disturb that wiring contract.
