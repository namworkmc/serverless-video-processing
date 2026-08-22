---
title: 'Story 3.1: History Consumer — Recording Terminal Events'
type: 'feature'
created: '2026-08-22'
status: 'done'
baseline_commit: '8cca516951c92005761c0d0fd7c6ce527bdf973a'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 2's pipeline ends with a `video.processed` event on `video-bus` that nobody consumes — the pipeline's terminal events leave no audit trail, and Epic 3's history query surface (Story 3.2) has nothing to read.

**Approach:** Declare the history leg in a new `terraform/history.tf` (AD-1 pattern, mirrors `trigger.tf`): `status-history` table (PK `eventId`), `history-queue` (SQS), a NEW `video.processed` rule targeting only the history queue, `history-consumer` Lambda + role + ESM. The consumer unwraps `Records[].body` → EventBridge envelope → `detail`, validates `videoId` against `video-metadata` via the shared layer, and appends one entry per unique `eventId` via a conditional `PutItem` (the condition IS the dedupe). Unknown videoId = poison → drop + ack; transient errors raise → SQS redelivers.

## Boundaries & Constraints

**Always:**
- New consumer = new queue + new rule target (AD-1): the `video.processed` rule for history is a NEW rule; the `video.uploaded` rule (`trigger.tf`) and the smoke capture rule (`smoke.tf`) are untouched
- Consumer unwraps `Records[].body` → EventBridge event → `detail` (tolerate detail as dict OR JSON string — same as shim); reads the FLAT wire shape `{eventId, schemaVersion, videoId, status, bucket, originalKey, processedKey, detail}` (AD-6 as-built)
- Validation via shared layer: `status.get_record(metadata_table, videoId)` — `NotFoundError` = unknown videoId = poison event → log, ack, never retry (FR-15); any other error raises → ESM retries, never dropped (FR-15)
- Dedupe by conditional write: `PutItem` with `ConditionExpression: attribute_not_exists(eventId)`; `ConditionalCheckFailedException` = duplicate → log as dedupe, ack (FR-14, NFR-1) — same idiom as `status.create_record`
- History entry = `{eventId, videoId, status, timestamp}` where timestamp is ISO-8601 UTC at consumption (same format as `shared.status._now_iso`)
- Handler stdlib-only + shared layer; NO new shared-layer code (existing `clients.dynamodb_table`, `status.get_record`, `errors.is_conditional_check_failed` cover everything)
- Config-not-code (NFR-4): `METADATA_TABLE`, `HISTORY_TABLE`, `AWS_ENDPOINT_URL` from Terraform-set env vars; queue/bus/rule names exist only in Terraform
- Python 3.11, zip layout mirrors `trigger.tf` hand-maintained source blocks (`shared/` at zip root + `history_consumer/` package); handler string `history_consumer.handler.handler`
- Least-privilege IAM: logs + `dynamodb:GetItem` on `video-metadata` only + `dynamodb:PutItem` on `status-history` only + standard SQS-ESM set on the history queue only
- Malformed records (unparseable body, missing detail/eventId/videoId/status) logged + acked (skipped) — same policy as shim (no DLQ in v1)
- Keep the CI mirror green (gitleaks, ruff E,F, terraform fmt, pytest, terraform validate, smoke)

**Ask First:**
- Any change to `lambdas/_shared/`
- Any change to existing resources in `upload.tf` / `transcode.tf` / `processing.tf` / `trigger.tf` / `smoke.tf`
- If floci does not deliver rule→queue or ESM→Lambda at apply/run time: HALT and report before inventing a workaround

**Never:**
- DLQ / retry-tuning machinery
- Status filtering in the history consumer (it records every terminal event it consumes — status filtering is the Epic 4 search consumer's contract, not this one)
- Reading `status-history` to answer anything (derived table; Story 3.2 owns reads)
- `aws` CLI for provisioning/inspection (local boto3 against `localhost:4566`)
- Runtime dependencies beyond stdlib
- Modify `_bmad-output/` planning artifacts

## I/O & Edge-Case Matrix

history-consumer handler (input = SQS event from the event-source mapping):

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy record | `Records[0].body` = EventBridge event JSON, flat detail `{eventId, schemaVersion, videoId, status, ...}`, videoId exists in `video-metadata` | `PutItem` on `status-history`: `{eventId, videoId, status, timestamp}` with `attribute_not_exists(eventId)` condition; structured log with videoId/eventId; summary `recorded=1` | N/A |
| Duplicate eventId | same body redelivered (entry exists) | `ConditionalCheckFailedException` caught → logged as dedupe, acked; summary `deduped=1`; still exactly one table entry | N/A |
| Unknown videoId (poison) | metadata `get_record` raises `NotFoundError` | event dropped — no write, warning log, acked, never retried; summary `dropped=1` | FR-15 |
| Transient DynamoDB error | `get_record` or `put_item` raises any other error | raises (ESM retries the message) — never dropped | FR-15 |
| `detail` is a string | body's `detail` arrives JSON-stringified | `json.loads` it, then identical behavior | N/A |
| Malformed record | body not JSON / no `detail` / missing or empty `eventId`, `videoId`, or `status` | record skipped (acked) with warning log — no raise, no write | log only |
| Non-SQS event | top-level not a dict, or `Records` missing/not a list | raises `MalformedInputError` | shared.errors |
| Multiple records | 2+ entries in `Records` | each processed independently, in order; per-record outcomes tallied in summary `{processed, recorded, deduped, dropped, skipped}` | per-record |

</frozen-after-approval>

## Code Map

- `lambdas/history_consumer/__init__.py`, `lambdas/history_consumer/handler.py` -- NEW; pattern mirrors `lambdas/sfn_trigger_shim/handler.py` (module-level `_metadata_table()` / `_history_table()` accessors for test monkeypatching, per-record outcome tally, `_parse_detail` tolerance of dict/str detail)
- `lambdas/history_consumer/tests/conftest.py` -- NEW; copy `lambdas/sfn_trigger_shim/tests/conftest.py` verbatim
- `lambdas/history_consumer/tests/test_history_consumer.py` -- NEW; fake DynamoDB tables (metadata get_item + history put_item with condition semantics), I/O matrix + client-recorder purity probe (only `dynamodb` resource ever built — never s3/events/states/sqs)
- `lambdas/_shared/status.py:117-125` -- `get_record()` raises `NotFoundError` for unknown videoId — the poison-detection primitive; READ-ONLY
- `lambdas/_shared/errors.py:63-65` -- `is_conditional_check_failed()` for the dedupe branch; READ-ONLY
- `lambdas/_shared/clients.py:83-90` -- `dynamodb_resource()` / `dynamodb_table()` factories; READ-ONLY (no shared-layer change needed)
- `lambdas/_shared/events.py:24-40` -- eventId derivation (UUID5) the dedupe key depends on; READ-ONLY
- `lambdas/sfn_trigger_shim/handler.py:92-105` -- `_parse_detail` dict/str tolerance to mirror; READ-ONLY
- `terraform/history.tf` -- NEW; table, queue + queue policy, rule + target, consumer zip/role/policy/function, ESM, outputs (table name, queue name/URL/ARN, consumer function)
- `terraform/smoke.tf:47-57` -- `aws_dynamodb_table.video_metadata` (GetItem target + IAM scope); `terraform/upload.tf:61` -- `aws_cloudwatch_event_bus.video_bus` (rule's bus); READ-ONLY references
- `terraform/trigger.tf` -- the structural template to mirror (queue/policy/rule/target/zip/role/function/ESM/outputs); READ-ONLY
- `terraform/providers.tf:25` -- `sqs` endpoint already listed; no provider change needed
- `terraform/locals.tf:9` -- `local.lambda_endpoint_url` for the consumer's `AWS_ENDPOINT_URL`

## Tasks & Acceptance

**Execution:**
- [x] `lambdas/history_consumer/` -- implement handler + tests per I/O matrix (ATDD) -- FR-14, FR-15, NFR-1
- [x] `terraform/history.tf` -- declare table, queue, queue policy, rule, target, consumer zip/role/function, ESM, outputs -- AC1
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- sync `3-1-history-consumer-recording-terminal-events` per workflow sprint-sync

**Acceptance Criteria:**
- Given floci running and `terraform apply`, when the environment is inspected, then `status-history` table, `history-queue`, the `video.processed`→history rule, the queue policy, the `history-consumer` function and role, and the SQS event-source mapping exist — the `video.uploaded` rule and the smoke capture rule are unchanged (AD-1)
- Given the full stack applied and a video uploaded through the gateway (Bruno collection), when the record reaches `PROCESSED`, then the consumer receives the SQS record, unwraps `body → detail`, validates the videoId, and ad-hoc inspection of `status-history` (local boto3) shows exactly one entry `{eventId, videoId, status: PROCESSED, timestamp}` with the deterministic eventId of (videoId, PROCESSED) (FR-14)
- Given the same `video.processed` redelivered (republished via local boto3 `events.put_events`), when the consumer processes it again, then the duplicate appends nothing — still exactly one entry for that eventId (FR-14, NFR-1)
- Given an event whose videoId is unknown to `video-metadata` (published via local boto3 with a fabricated eventId), when the consumer validates it, then the event is dropped — no table entry, message acked, never retried (FR-15)
- Given the test suite, when pytest runs, then all new tests pass (consumer I/O matrix, purity probe) and all existing tests still pass; `bash scripts/ci-local.sh` is green end-to-end

## Spec Change Log

## Design Notes

- **Dedupe is the write, not a read-then-write:** `PutItem` + `attribute_not_exists(eventId)` makes the table the enforcement point (same philosophy as AD-2's conditional transitions) — no race window, no GetItem before PutItem. eventId = UUID5(videoId, PROCESSED) is restart-proof, so republish, SQS retry, and redelivery all collide on the same key.
- **Poison vs transient is decided by exception type:** `NotFoundError` from `status.get_record` = successful negative lookup = drop (FR-15). Anything else (network, throttle, 5xx) raises → the ESM leaves the message in-flight → SQS redelivers. No error-code sniffing beyond the shared layer's duck-typed helpers.
- **Rule naming:** `video-processed-to-history` (mirrors `video-uploaded-to-processing-trigger` / `video-processed-to-smoke-capture`). Routing is by event name, source-agnostic — any future producer of `video.processed` feeds history too.
- **ESM settings:** `batch_size = 1`, queue `visibility_timeout_seconds = 300` — same rationale as the trigger leg (dedupe semantics + per-video traceability trivial; ≥ 6× the 30 s function timeout).
- **Smoke scenario deferred:** no new smoke scenario in this story — the live AC run (upload → poll `status-history`) backstops the deployed wiring; a `history-leg` scenario is an Epic 3 retro candidate if the pattern proves worth keeping in CI.
- **Worktree state gap (inherited from Story 2.3):** the main checkout's `terraform.tfstate` may predate resources applied from other worktrees — if a fresh apply hits `EntityAlreadyExists`, copy the main state in and `terraform import` the untracked resources before applying.

## Verification

**Commands:**
- `cd terraform && terraform init -input=false && terraform apply -auto-approve -input=false` -- expected: Apply complete; new resources = table, queue, queue policy, rule, target, consumer zip/role/policy/function, ESM; existing resources untouched
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: all tests pass (consumer suite + all existing)
- ad-hoc via local boto3 against `localhost:4566`: upload through the gateway (Bruno / `curl -F`) → poll `dynamodb.get_item` on `video-metadata` to PROCESSED → `dynamodb.get_item` on `status-history` with Key `{eventId: UUID5(videoId, PROCESSED)}` returns exactly one entry with videoId/status/timestamp
- redeliver: republish the identical `video.processed` via boto3 `events.put_events` → wait → still exactly one entry; poison: publish a `video.processed` with unknown videoId + fabricated eventId → wait → no entry for that eventId
- `bash scripts/ci-local.sh` -- expected: all 5 stages green

**Manual checks:**
- `curl -sf http://localhost:4566/_localstack/health` returns 200 before apply
- `terraform state list` shows upload-leg, transcode, processing, trigger, and smoke resources unchanged after apply

## Suggested Review Order

**Consumer logic (entry point first)**

- Per-record pipeline: unwrap → validate → poison check → conditional write; the whole design in one function
  [`handler.py:93`](../../lambdas/history_consumer/handler.py#L93)
- Poison vs transient split: NotFoundError drops, everything else raises for SQS retry (FR-15)
  [`handler.py:131`](../../lambdas/history_consumer/handler.py#L131)
- Dedupe is the write: attribute_not_exists(eventId) makes the table the enforcement point (FR-14, NFR-1)
  [`handler.py:147`](../../lambdas/history_consumer/handler.py#L147)
- EventBridge unwrap with dict/str detail tolerance, mirrored from the shim
  [`handler.py:78`](../../lambdas/history_consumer/handler.py#L78)
- Config-not-code accessors: table names strictly from Terraform env vars
  [`handler.py:60`](../../lambdas/history_consumer/handler.py#L60)

**Terraform wiring (AD-1 pattern)**

- New video.processed rule targeting only the history queue; existing rules untouched
  [`history.tf:70`](../../terraform/history.tf#L70)
- status-history table: PK eventId, append-only derived store (AD-3)
  [`history.tf:22`](../../terraform/history.tf#L22)
- Least-privilege IAM: GetItem on metadata + PutItem on history + SQS-ESM trio only
  [`history.tf:140`](../../terraform/history.tf#L140)
- ESM batch_size=1: dedupe semantics and per-video traceability stay trivial
  [`history.tf:204`](../../terraform/history.tf#L204)

**Tests**

- Dedupe suite: conditional-write collision acked, still exactly one entry
  [`test_history_consumer.py:242`](../../lambdas/history_consumer/tests/test_history_consumer.py#L242)
- Poison suite: unknown videoId dropped without write, never retried
  [`test_history_consumer.py:268`](../../lambdas/history_consumer/tests/test_history_consumer.py#L268)
- Purity probe: real accessors run against a recorder; only dynamodb may be constructed
  [`test_history_consumer.py:431`](../../lambdas/history_consumer/tests/test_history_consumer.py#L431)
