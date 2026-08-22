---
title: 'Integration test suite — Step 1: swap the CI smoke gate'
type: 'feature'
created: '2026-08-22'
status: 'in-progress' # draft | ready-for-dev | in-progress | in-review | done
review_loop_iteration: 0
baseline_commit: 'b96a2a8a8ab3c551c01afbcfb7e0844c83f571e1'
context:
  - '{project-root}/_bmad-output/test-artifacts/integration-test-plan.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** CI stage 5 gates on the `smoke` Lambda invoked through floci's Lambda API, bypassing API Gateway entirely — the gateway route, request/response mapping, and the `_aws/execute-api` data plane are never exercised by CI (plan §1).

**Approach:** Step 1 of `_bmad-output/test-artifacts/integration-test-plan.md`: add a pytest integration suite (`tests/integration/`, T1–T10) that drives the deployed stack through real gateway calls and AWS-API side-effect reads; rename `terraform/smoke.tf` → `integration.tf` stripping only the smoke function/role/archive; rewire stage 5 in both CI entry points; update docs. `lambdas/smoke/` survives — Step 2 deletes it after one green CI run (D8).

## Boundaries & Constraints

**Always:**
- Follow integration-test-plan.md decisions D1–D8; scope is §6 Step 1 items 1–4 exactly.
- Keep capture queue + policy + rule + target at IDENTICAL Terraform resource addresses (`aws_sqs_queue.smoke_capture`, `aws_sqs_queue_policy.smoke_capture`, `aws_cloudwatch_event_rule.video_processed_capture`, `aws_cloudwatch_event_target.smoke_capture_queue`) — no state migration, no recreate (D4).
- T1 uses the D6 binary fixture (`bytes(range(256)) * 4`, generated in conftest — no fixture file) and asserts byte-identical S3 round-trip.
- Work in the existing worktree `.worktrees/integration-test-plan` (branch `bmad/integration-test-plan`).
- `bash scripts/ci-local.sh` fully green before committing.

**Ask First:**
- Any deviation from plan decisions D1–D8 or from the T1–T10 assertions in plan §4.

**Never:**
- Do not delete or modify `lambdas/smoke/` (Step 2).
- Do not touch AGENTS.md or `lambdas/README.md` smoke references (Step 2 items).
- No new infrastructure beyond what exists; no `aws` CLI anywhere; no new Python deps beyond pytest/requests/boto3 via `uv run --with`.
- Do not rename or redeclare the `video-metadata` table (it stays in the renamed file).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| T1 happy upload | multipart: binary fixture (all 256 byte values) + title → `{gateway}/videos/upload` | 200 `{"videoId"}`; `video-uploads` object at `{videoId}/{filename}` byte-identical to fixture; `video-metadata` record UPLOADED with createdAt/updatedAt | N/A |
| T2 malformed | multipart without file part | 400 with `{"error": ...}` body passed through unchanged | N/A |
| T3 journey | T1 upload | record → PROCESSED (poll ≤180 s); `video-processed` object `processed/{videoId}/{filename}`; capture queue exactly 1 `video.processed` for eventId=UUID5(videoId, PROCESSED); SFN execution `eb-{UUID5(videoId, UPLOADED)}` exists | poll timeout fails test |
| T4 redelivery | republish same `video.uploaded` via `events:put_events` | still exactly 1 execution, record still PROCESSED, no 2nd processed event (ExecutionAlreadyExists acked) | wait, then assert |
| T5 ad-hoc SFN | seeded S3 object + UPLOADED record; StartExecution `{videoId, status, bucket, key}` | PROCESSED with `processedKey`; processed object exists; exactly 1 processed event | N/A |
| T6 rerun | StartExecution again (fresh name, record PROCESSED) | execution FAILED at MarkProcessing; status stays PROCESSED; no 2nd event | N/A |
| T7 ad-hoc transcode | seeded object + record; invoke deployed `transcode` via floci Lambda REST `{videoId, originalKey}` | processed object exists; record still UPLOADED; no event published | N/A |
| T8 history | T1 upload | exactly one `status-history` entry `{eventId, videoId, status: PROCESSED, timestamp}` with deterministic eventId | poll |
| T9 history dedupe | republish `video.processed` | still exactly one entry for that eventId | N/A |
| T10 poison | `video.processed` with fabricated eventId + unknown videoId | no table entry; message acked (no retry storm) | N/A |

</frozen-after-approval>

## Code Map

- `tests/integration/conftest.py` (NEW) — boto3 clients pinned to `http://localhost:4566` (creds `test`/`test`, `us-east-1`); `gateway_base_url` from `GATEWAY_BASE_URL` env, fallback `terraform output -raw gateway_base_url` (plan D5); constants `video-metadata`, `video-uploads`, `video-processed`, `video-bus`, `status-history`, `smoke-capture-queue`; binary fixture `bytes(range(256)) * 4` (D6); `poll_until(fn, timeout=180)`; capture-queue drain scoped to own eventId; per-test cleanup (metadata + history items, objects in both buckets).
- `tests/integration/test_upload_api.py` (NEW) — T1, T2.
- `tests/integration/test_processing_journey.py` (NEW) — T3, T4.
- `tests/integration/test_state_machine.py` (NEW) — T5, T6.
- `tests/integration/test_transcode.py` (NEW) — T7.
- `tests/integration/test_history_leg.py` (NEW) — T8, T9, T10.
- `terraform/smoke.tf` → `terraform/integration.tf` — keep `aws_dynamodb_table.video_metadata` (lines 47–57), `aws_sqs_queue.smoke_capture` (66–69), `aws_sqs_queue_policy.smoke_capture` (71–88), `aws_cloudwatch_event_rule.video_processed_capture` (90–97), `aws_cloudwatch_event_target.smoke_capture_queue` (99–103); delete `data.archive_file.smoke_zip`, `aws_iam_role.smoke`, `aws_iam_role_policy.smoke`, `aws_lambda_function.smoke`, `output "smoke_function"`; rewrite header comment.
- `.github/workflows/ci.yml` — `smoke` job (lines 136–181): rename job/steps, replace invoke step per plan §5; keep destroy `if: always()`, failure logs, artifact (rename `smoke-failure` → `integration-failure`); update header comment (lines 12–14).
- `scripts/ci-local.sh` — stage 5 (lines 24–30) same replacement.
- `docs/ci.md` — stage table row 17, job graph 19, secrets para 55, "Smoke stage details" section 70–81, troubleshooting row 95.
- `README.md` — smoke references at lines 331–333, 339.
- Contracts (read-only): upload key `{videoId}/{filename}` (`lambdas/upload_handler/handler.py:194`); processed key `processed/{videoId}/{basename}` (`lambdas/transcode/handler.py:68-76`); eventId `uuid5(ns 99881bbf-…, "{videoId}:{status}")` (`lambdas/_shared/events.py:24,38-40`); wire Detail = flat `{**envelope, **envelope["detail"]}` (`upload_handler/handler.py:228`, `event_publisher/handler.py:101`); execution name `eb-{eventId}` (`lambdas/sfn_trigger_shim/handler.py:48,131`); transcode REST invoke `POST http://localhost:4566/2015-03-31/functions/transcode/invocations` — floci 1.7 wraps result as `{Payload, StatusCode}`; history item `{eventId(PK), videoId, status, timestamp}` (`lambdas/history_consumer/handler.py:150-158`); ASL fails at MarkProcessing when record ≠ UPLOADED (`terraform/processing.asl.json:5-24`).

## Tasks & Acceptance

**Execution:**
- [x] `tests/integration/conftest.py` — create fixtures/helpers per Code Map.
- [x] `tests/integration/test_upload_api.py`, `test_processing_journey.py`, `test_state_machine.py`, `test_transcode.py`, `test_history_leg.py` — implement T1–T10 per I/O matrix and plan §4.
- [x] `terraform/smoke.tf` → `terraform/integration.tf` — strip smoke resources, keep kept-resources at identical addresses, rewrite header comment.
- [x] `.github/workflows/ci.yml` — rewire stage 5 per plan §5.
- [x] `scripts/ci-local.sh` — rewire stage 5 per plan §5. (+ `COMPOSE_PROJECT_NAME` pin so worktree checkouts reuse the running floci instead of spawning a second one on 4566.)
- [x] `docs/ci.md` + `README.md` — update smoke references to the integration stage.

**Acceptance Criteria:**
- Given the stack applied from the renamed `integration.tf`, when `bash scripts/ci-local.sh` runs, then all 5 stages are green with stage 5 running `pytest tests/integration/ -q` (10 tests pass).
- Given an existing tfstate containing the smoke resources, when `terraform apply` runs after the rename, then the plan destroys only `data.archive_file.smoke_zip`, `aws_iam_role.smoke`, `aws_iam_role_policy.smoke`, `aws_lambda_function.smoke`, `output "smoke_function"` — every other resource shows no change (identical addresses).
- Given T1 runs, when the uploaded object is read back from `video-uploads`, then its bytes equal the binary fixture exactly.
- Given the commit is made, when `lambdas/smoke/` is inspected, then it is untouched.

## Design Notes

- eventId derivation is re-derived in conftest with `uuid.UUID("99881bbf-05eb-5ec6-8f3a-490d7496e518")` + `uuid5(ns, f"{video_id}:{status}")` — the namespace/derivation is a frozen wire contract (`_shared/events.py:24`), and importing `lambdas/_shared` would drag package layout into the integration suite.
- Capture-queue hygiene (plan §4): each journey test drains the queue at start; assertions count only messages whose detail `eventId` matches the test's own videoId — CI runs serially, residue from earlier tests is ignored, not assumed away.
- T5/T7 seed via direct boto3 S3 put + `status.create_record`-shaped PutItem (plain boto3 `put_item`, not the shared layer) — isolates the state-machine/transcode legs from the upload path.
- T4/T9 republish with the SAME deterministic eventId the real producer used — the dedupe under test is the deterministic-id collision, so a fresh fabricated id would test nothing.

## Verification

**Commands:**
- `bash scripts/ci-local.sh` — expected: all 5 stages green (the commit gate).
- `(cd terraform && terraform plan)` against the pre-change state — expected: destroys limited to the five smoke resources; zero other changes.
- `GATEWAY_BASE_URL=$(cd terraform && terraform output -raw gateway_base_url) uv run --with 'pytest>=8.0' --with requests --with boto3 pytest tests/integration/ -q` — expected: 10 passed.
