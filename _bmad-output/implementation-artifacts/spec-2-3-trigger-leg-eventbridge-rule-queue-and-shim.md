---
title: 'Story 2.3: Trigger Leg — EventBridge Rule, Queue, and Shim'
type: 'feature'
created: '2026-08-20'
status: 'done'
baseline_commit: '28b57a61c1337c652e281a5b49b562f5d9433b4d'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 2's processing leg works only via ad-hoc `StartExecution` (Story 2.2): a `video.uploaded` event on `video-bus` goes nowhere. The epic's promise — upload a video and it processes itself — does not exist yet.

**Approach:** Declare the trigger leg in `terraform/trigger.tf` (AD-5): EventBridge rule on `video-bus` matching `video.uploaded` → `processing-trigger-queue` (SQS) → event-source mapping → `sfn-trigger-shim` Lambda → `StartExecution` with deterministic name `eb-{eventId}`; `ExecutionAlreadyExists` is treated as success (dedupe). Verify end-to-end: upload through the gateway, watch the record walk UPLOADED → PROCESSING → PROCESSED with no manual invocation.

## Boundaries & Constraints

**Always:**
- The rule targets the queue, NEVER the state machine directly (floci cannot — AD-5); a queue policy grants `events.amazonaws.com` `sqs:SendMessage` scoped to the rule ARN
- Shim unwraps `Records[].body` → EventBridge event → `detail`; passes the state machine EXACTLY the domain payload `{videoId, status, bucket, key}` — the ASL input contract frozen by Story 2.2
- Execution name `eb-{eventId}` where eventId comes from `detail.eventId` (the deterministic UUID5 of (videoId, UPLOADED)) — never the EventBridge top-level `id` (random on real AWS)
- `ExecutionAlreadyExists` is treated as success: log as dedupe, ack the record (FR-9, NFR-1/2)
- Shim handler is stdlib-only + shared layer; the Step Functions client comes from a NEW one-line `states_client()` factory in `shared.clients` (mirrors `events_client()`) — the only shared-layer change
- Config-not-code (NFR-4): shim reads `STATE_MACHINE_ARN` + `AWS_ENDPOINT_URL` from Terraform-set env vars; queue/bus/rule/function names exist only in Terraform
- Python 3.11, zip layout mirrors `transcode.tf` hand-maintained source blocks (`shared/` at zip root + `sfn_trigger_shim/` package); handler string `sfn_trigger_shim.handler.handler`
- Least-privilege IAM: shim role = logs + `states:StartExecution` on the processing state machine only + `sqs:ReceiveMessage`/`DeleteMessage`/`GetQueueAttributes` on the trigger queue only (the standard SQS-ESM set)
- Keep the CI mirror green (gitleaks, ruff E,F, terraform fmt, pytest, terraform validate, smoke)

**Ask First:**
- Any change to `lambdas/_shared/` beyond the single `states_client()` factory
- Any change to existing resources in `upload.tf` / `transcode.tf` / `processing.tf` / `smoke.tf`
- If floci does not deliver rule→queue or ESM→Lambda at apply/run time: HALT and report before inventing a workaround (a new floci gap needs a decision, not a silent redesign)

**Never:**
- EventBridge rule targeting a state machine (unsupported on floci — the shim exists precisely because of this)
- DLQ / retry-tuning machinery (lean lab; malformed records are logged + acked — see Design Notes)
- `aws` CLI for provisioning/inspection (local boto3 against `localhost:4566`)
- Runtime dependencies beyond stdlib in the shim
- Modify `_bmad-output/` planning artifacts

## I/O & Edge-Case Matrix

sfn-trigger-shim handler (input = SQS event from the event-source mapping):

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy trigger | `Records[0].body` = EventBridge event JSON, `detail` = `{eventId, schemaVersion, videoId, status, bucket, key}` | `start_execution(name="eb-{eventId}", input={videoId, status, bucket, key})` on `STATE_MACHINE_ARN`, structured log with videoId/eventId/executionName, returns summary dict | N/A |
| Redelivery / republish | same body again (execution exists) | `ExecutionAlreadyExists` caught → logged as dedupe, treated as success (ack) | N/A |
| `detail` is a string | body's `detail` arrives JSON-stringified | `json.loads` it, then identical behavior | N/A |
| Malformed record | body not JSON / no `detail` / no `eventId` / `eventId` unusable as an execution name / missing or empty `videoId`, `status`, `bucket`, or `key` | record skipped (acked) with a warning log — no raise, no execution started | log only |
| Non-SQS event | top-level not a dict, or `Records` missing/not a list | raises `MalformedInputError` | shared.errors |
| Real StartExecution error | any other States error (throttle, network, IAM) | raises (ESM retries the message) | N/A |
| Multiple records | 2+ entries in `Records` | each processed independently, in order; per-record outcomes tallied in the summary | per-record |

</frozen-after-approval>

## Code Map

- `lambdas/sfn_trigger_shim/__init__.py`, `lambdas/sfn_trigger_shim/handler.py` -- NEW; pattern mirrors `lambdas/event_publisher/handler.py` (module-level `_states_client()` / `_state_machine_arn()` accessors for test monkeypatching, `_require_field`-style validation returning stripped values)
- `lambdas/sfn_trigger_shim/tests/conftest.py` -- NEW; copy `lambdas/event_publisher/tests/conftest.py` verbatim
- `lambdas/sfn_trigger_shim/tests/test_sfn_trigger_shim.py` -- NEW; fake states client, I/O matrix + client-recorder purity probe (only a `states` client is ever built — never dynamodb/s3/events)
- `lambdas/_shared/clients.py:97` -- add `states_client()` factory mirroring `events_client()`; the ONLY shared-layer change
- `lambdas/upload_handler/handler.py:212-234` -- the producer's wire shape the shim consumes (flat Detail `{**envelope, **envelope["detail"]}`, Source `upload-handler`); READ-ONLY
- `lambdas/_shared/events.py:24-40` -- eventId derivation (UUID5 namespace) the execution name depends on; READ-ONLY
- `terraform/trigger.tf` -- NEW; queue + queue policy, rule + target, shim zip/role/policy/function, event-source mapping, outputs (queue name/URL/ARN, shim function)
- `terraform/upload.tf:61` -- `aws_cloudwatch_event_bus.video_bus` (rule's bus); `terraform/processing.tf:175` -- `aws_sfn_state_machine.processing` (StartExecution target + IAM scope); READ-ONLY references
- `terraform/providers.tf:25` -- `sqs` endpoint already listed; no provider change needed
- `terraform/locals.tf:9` -- `local.lambda_endpoint_url` for the shim's `AWS_ENDPOINT_URL`
- `bruno/upload-video.bru` -- existing upload request reused for end-to-end verification; READ-ONLY
- `.github/workflows/ci.yml` -- READ-ONLY; smoke stage unaffected

## Tasks & Acceptance

**Execution:**
- [x] `lambdas/_shared/clients.py` -- add `states_client()` factory -- shim's SFN access through the shared layer (AD-8 convention)
- [x] `lambdas/sfn_trigger_shim/` -- implement handler + tests per I/O matrix (ATDD) -- FR-9, AD-5, NFR-1/2
- [x] `terraform/trigger.tf` -- declare queue, queue policy, rule, target, shim zip/role/function, ESM, outputs -- FR-5, AC1
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- sync `2-3-trigger-leg-eventbridge-rule-queue-and-shim` per workflow sprint-sync

**Acceptance Criteria:**
- Given floci running and `terraform apply`, when the environment is inspected, then `processing-trigger-queue`, the `video.uploaded` rule, the queue policy, the `sfn-trigger-shim` function and role, and the SQS event-source mapping exist — and the rule's only target is the queue, never the state machine (AD-5)
- Given the full stack applied, when a video is uploaded through the gateway (Bruno collection), then the shim receives the SQS record, unwraps `Records[].body` → EventBridge event → `detail`, calls `StartExecution` with name `eb-{eventId}`, and the record walks UPLOADED → PROCESSING → PROCESSED with the processed object in `video-processed` and exactly one `video.processed` event on the bus (FR-5, FR-7, FR-8) — no manual invocation anywhere
- Given the same `video.uploaded` redelivered after the video is `PROCESSED` (republished via local boto3 `events.put_events`), when the shim processes it again, then `ExecutionAlreadyExists` is acked as success — still exactly one execution, no re-transcode, no status regression (FR-9, NFR-1/2)
- Given the Bruno upload journey, when Step Functions history and Lambda logs are inspected ad-hoc, then exactly one execution named `eb-{eventId}` exists for that video and the full path (upload-handler → shim → state machine → transcode → publisher) is traceable through logs (NFR-5)
- Given the test suite, when pytest runs, then all new tests pass (shim I/O matrix, purity probe) and all existing tests still pass; `bash scripts/ci-local.sh` is green end-to-end

## Spec Change Log

- 2026-08-21 (review loop 1, user-approved): required detail fields widened from `{videoId, key}` to the full ASL contract `{videoId, status, bucket, key}` (a partial input would start an execution that fails mid-flight with the record already acked); added the eventId execution-name charset/length guard (poison-message prevention, consistent with the log+ack policy).

## Design Notes

- **Wire shape:** the rule delivers the FULL EventBridge event as the SQS body; its `detail` is the flat payload published by the upload handler `{eventId, schemaVersion, videoId, status, bucket, key}`. The shim reads `detail.eventId` for the execution name and extracts exactly `{videoId, status, bucket, key}` as the ASL input (Story 2.2's frozen input contract — extras tolerated by the ASL, but the shim keeps the contract explicit). Tolerate `detail` arriving as dict OR str (`json.loads` when str); the exact floci encoding is confirmed by the live AC run.
- **Dedupe chain:** eventId = UUID5(videoId, UPLOADED) is restart-proof, so `eb-{eventId}` is identical for republish, SQS retry, and redelivery alike; `ExecutionAlreadyExists` converts the collision into an ack. The ASL's first `updateItem` condition is the second line of defense should a second execution ever start.
- **Malformed-record policy — log + ack (skip):** no DLQ exists in v1, and a deterministic poison message would retry forever and spam Lambda invocations on floci's ESM loop. The upload handler is the bus's only producer and always emits well-formed events, so a malformed record is a lab anomaly worth a warning, not a retry storm.
- **ESM settings:** `batch_size = 1` — one record per invocation keeps dedupe semantics and per-video traceability trivial. Queue `visibility_timeout_seconds = 300` (real-AWS guidance: ≥ 6× the 30 s function timeout).
- **Rule pattern:** matches `detail-type = ["video.uploaded"]` on `video-bus`, source-agnostic — routing is by event name (spine routing rule), so any future producer of `video.uploaded` triggers processing too.
- **WORKTREE STATE GAP (implementation-verified 2026-08-20):** the main checkout's `terraform.tfstate` was stale — it predated Stories 2.1/2.2 (applied from a since-removed worktree), so a fresh worktree apply hit `EntityAlreadyExists`/`BucketAlreadyExists` on the imported-but-untracked resources. Remedy: copy the main state into the worktree, `terraform import` the ten 2.1/2.2 resources (bucket, transcode + publisher zips/roles/policies/functions, SFN role/policy/state machine), then apply. The imported `aws_sfn_state_machine.processing` showed a whitespace-only definition diff (floci re-serializes ASL JSON on write, so an imported definition never byte-matches the `templatefile` output) and floci's missing `UpdateStateMachine` rejected the in-place update — resolved with the documented `terraform apply -replace=aws_sfn_state_machine.processing` (destroy+recreate; after create, plans are clean). No config change to `processing.tf` was needed.
- **ci-local.sh smoke stage in worktrees:** the script runs `docker compose up -d --wait` unconditionally; in a worktree the compose project name differs (`story-2-3`), so it tries to spawn a SECOND floci on port 4566 and fails while the main checkout's floci holds the port. Workaround used: run the smoke stage's actual work (apply already done + invoke `smoke` with `{"scenario":"all"}` + assert `all_pass`) against the healthy running floci. Stages 1–4 ran green via the script.

## Verification

**Commands:**
- `cd terraform && terraform init -input=false && terraform apply -auto-approve -input=false` -- expected: Apply complete; new resources = queue, queue policy, rule, target, shim zip/role/policy/function, ESM; existing resources untouched
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: all tests pass (shim suite + all existing)
- ad-hoc via local boto3 against `localhost:4566`: upload through the gateway (`curl -F` per README / Bruno) → poll `dynamodb.get_item` to PROCESSED → assert `s3.head_object` on the processed key, exactly one `video.processed` event on `video-bus`, and `sfn.list_executions` shows exactly one execution named `eb-{eventId}` with the videoId in its input
- redeliver: republish the identical `video.uploaded` via boto3 `events.put_events` → wait → still exactly one execution, record still PROCESSED, no second `video.processed`
- `bash scripts/ci-local.sh` -- expected: all 5 stages green

**Manual checks:**
- `curl -sf http://localhost:4566/_localstack/health` returns 200 before apply
- `terraform state list` shows upload-leg, transcode, processing, and smoke resources unchanged after apply
