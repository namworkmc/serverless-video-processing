# Epic 4 Context: Search Surface & End-to-End Lab Verification

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Complete the third and final client journey — title search over processed videos — and then prove the entire lab. A search consumer indexes PROCESSED events into a derived search-index table, a gateway route serves title-substring search, and an admin-only rebuild proves the index is disposable. The epic closes with the definition of done: from a clean `terraform destroy` + `apply`, the full Bruno collection reproduces the whole pipeline — one upload yields a PROCESSED metadata record, a status-history entry, and a search hit, all queryable through the gateway, with every target service (API Gateway, Lambda, S3, DynamoDB, EventBridge, Step Functions) demonstrably exercised.

## Stories

- Story 4.1: Search Consumer — Indexing Processed Videos
- Story 4.2: Title Search Through the Gateway
- Story 4.3: Admin-Only Index Rebuild
- Story 4.4: End-to-End Lab Verification (SM-1)

## Requirements & Constraints

- Only PROCESSED videos are indexed; FAILED events are never indexed. Redelivered events upsert the same entry — no duplicates.
- Poison handling: events whose videoId is unknown to the metadata table are dropped and acked; transient metadata errors are retried, never dropped.
- Title search is by substring: matches return HTTP 200 with results; no match returns HTTP 200 with an empty list (not an error); a missing or empty `title` parameter returns 400 with body `{"error": ...}`.
- Index rebuild must be admin-only: no gateway route, rule, or queue may reference it — the constraint must hold structurally, not by convention.
- All client HTTP goes through the single gateway, no auth; responses (status codes and `{"error": ...}` bodies) pass through unchanged.
- The Bruno collection exercises all three journeys through the gateway only — never backend endpoints directly — and running it against a fresh `terraform apply` reproduces the full pipeline end-to-end.
- Setup/teardown is Terraform-only: `destroy` + `apply` rebuilds the entire environment with no manual steps and no `aws` CLI in the documented procedure (aws CLI is permitted for ad-hoc inspection only).
- Performance is a non-goal — no throughput/latency/scale polish.
- Final verification requires traceability: the video's full path must be traceable through Step Functions execution history and Lambda logs.

## Technical Decisions

- `search-index` is a derived, disposable table (PK `videoId`; attributes: title, processedKey, indexedAt), rebuildable from `video-metadata`. Title-substring search is a Scan with a contains filter — acceptable at lab scale, no GSI.
- The search consumer sits behind its own SQS queue (`search-queue`) behind its OWN EventBridge rule (`video-processed-to-search`); routing is one independent rule per consumer — `video-processed-to-history` → history queue, `video-processed-to-search` → search queue. New consumer = new queue + new rule, never a change to an existing consumer or rule.
- Consumers unwrap SQS `Records[].body` (JSON-stringified EventBridge envelope) → `detail`; as-built (AD-6 flat view) the `video.processed` detail carries `{eventId, schemaVersion, videoId, status, bucket, originalKey, processedKey}` — consumers validate `eventId`/`videoId`/`status`/`processedKey`. The indexed title comes from the `video-metadata` record (set at upload from an optional multipart `title` field, falling back to the filename).
- Reuse the shared access layer (`lambdas/_shared/`) for event envelope shapes, error mapping (409 conflict / 404 not-found / 400 malformed / 500), and service clients. Endpoints and resource names come from environment variables only, never hardcoded.
- Dependency direction: search-consumer is the sole event writer of `search-index` (upsert); search-rebuild is the sole repopulator (scans `video-metadata`, PROCESSED only); search-query reads the index. No function reads a derived table to answer a question the metadata table owns.
- Gateway route `GET /videos/search?title=` → search-query completes the authoritative three-route table. The gateway data plane resolves only at `http://localhost:4566/_aws/execute-api/{apiId}/{stage}/{path}`; `apiId` comes from the Terraform output.
- Every resource (table, queue, rule target, Lambdas, IAM roles, gateway route) is declared in Terraform.
- Bruno requests for async legs use a poll-with-timeout pattern — retry until the expected result appears or the timeout fails the assertion; no fixed sleeps.
- The README documents the fixed bring-up order (`docker compose up` → `terraform apply` → exercise via Bruno through the `_aws/execute-api` URL), the `-replace` caveat for state-machine changes, and contains no `aws` CLI in setup/teardown.

## Cross-Story Dependencies

- Epic sequence is 1 → 2 → 3 → 4: this epic consumes Epic 2's `video.processed` events and verifies Epic 3's history surface alongside its own in final verification.
- Story 4.2 requires 4.1's indexed entries; 4.3's rebuild is verified through 4.2's gateway route; 4.4 depends on all prior stories and epics — it runs the complete Bruno collection against the complete Terraform configuration.
- Story 4.1 adds its OWN EventBridge rule `video-processed-to-search` targeting only the search queue — one independent rule per consumer (AD-1 as-built; same precedent as `video-processed-to-history`). It must not alter the `video.uploaded` rule, the `video-processed-to-history` rule, or any existing target.
