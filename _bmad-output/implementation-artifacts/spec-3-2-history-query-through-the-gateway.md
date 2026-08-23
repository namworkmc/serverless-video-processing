---
title: 'Story 3.2: History Query Through the Gateway'
type: 'feature'
created: '2026-08-23'
status: 'done'
baseline_commit: 'd900264c60b4efe8022cbbf6074af540bb5ca341'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 3.1's audit trail is only readable via ad-hoc local boto3 — the second client journey (querying a video's status history) has no gateway surface, leaving FR-16 and the FR-21 route table incomplete.

**Approach:** A new `history-query` Lambda — validates the videoId via the shared layer (the 404 gate), then reads `status-history` filtered by videoId and returns the entries sorted by timestamp — wired into the EXISTING API Gateway v2 as `GET /videos/{videoId}/history` (new integration + route + invoke permission, declared in `terraform/history.tf`). The Bruno collection grows a history request with poll-with-timeout that runs after the upload journey.

## Boundaries & Constraints

**Always:**
- The route joins the existing `aws_apigatewayv2_api.gateway` (upload.tf) — declare only a new integration, route, and `aws_lambda_permission` scoped to the new route; upload-leg resources are untouched (FR-21 route table grows, responses pass through unchanged)
- 404 gate BEFORE the history read: `status.get_record(metadata_table, videoId)` — `NotFoundError` → 404 `{"error": ...}` via `errors.map_error` (FR-13, NFR-3). A KNOWN videoId with zero entries returns 200 with empty entries — the consumer leg is async, so "no entries yet" is not "no video"
- History read = `Scan` with `FilterExpression videoId = :vid` — `status-history` PK is `eventId` only and AD-3 binds the key schema (no GSI; lab scale, NFR-7). Entries sorted by `timestamp` ascending in the handler (ISO-8601 sorts lexicographically)
- 200 response body: `{"videoId": ..., "entries": [{"status", "eventId", "timestamp"}, ...]}` (FR-16)
- Handler stdlib-only + shared layer; NO new shared-layer code (`status.get_record`, `errors.map_error`, `clients.dynamodb_table` cover everything)
- Config-not-code (NFR-4): `METADATA_TABLE`, `HISTORY_TABLE`, `AWS_ENDPOINT_URL` from Terraform-set env vars
- Python 3.11, zip layout mirrors `history.tf` hand-maintained source blocks (`shared/` at zip root + `history_query/` package); handler string `history_query.handler.handler`
- Least-privilege IAM: logs + `dynamodb:GetItem` on `video-metadata` only + `dynamodb:Scan` on `status-history` only
- Bruno request targets the gateway base URL only; poll-with-timeout — retry until the entry appears or the timeout fails the assertion; no fixed sleeps (FR-22)
- Keep the CI mirror green (gitleaks, ruff E,F, terraform fmt, pytest, terraform validate, integration)

**Ask First:**
- Any change to `lambdas/_shared/`
- Any change to existing resources in `upload.tf` or to Story 3.1's resources in `history.tf` (table, queue, rule, consumer)
- If floci does not deliver `pathParameters` per API Gateway v2 payload format 2.0, or the new route does not resolve at `_aws/execute-api`: HALT and report before inventing a workaround

**Never:**
- GSI or key-schema change on `status-history` (spine AD-3; single-table design is a deferred learning surface)
- Pagination machinery (NFR-7)
- Reading `video-metadata` fields into the response (the metadata record is only the 404 gate)
- `aws` CLI for provisioning/inspection (local boto3 against `localhost:4566`)
- Runtime dependencies beyond stdlib
- Modify `_bmad-output/` planning artifacts

## I/O & Edge-Case Matrix

history-query handler (input = API Gateway v2 payload format 2.0 event):

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | `pathParameters.videoId` known to metadata, 1+ history entries | 200 `{"videoId", "entries": [...]}` sorted timestamp ascending; structured log with videoId + entry count | N/A |
| Known videoId, no entries yet | metadata record exists, scan returns nothing | 200 with `"entries": []` — NOT 404 | N/A |
| Unknown videoId | `status.get_record` raises `NotFoundError` | 404 `{"error": ...}`; no scan performed | FR-13, NFR-3 |
| Missing/empty videoId | no `pathParameters`, or empty/whitespace `videoId` | 400 via `MalformedInputError` | shared.errors |
| Transient DynamoDB error | `get_record` or `scan` raises anything else | 500 via `map_error` | NFR-3 |

</frozen-after-approval>

## Code Map

- `lambdas/history_query/__init__.py`, `lambdas/history_query/handler.py` -- NEW; pattern mirrors `lambdas/upload_handler/handler.py` (API GW v2 response shape `{statusCode, headers, body}` + `map_error` catch-all, upload_handler/handler.py:246-258) and `lambdas/history_consumer/handler.py` (module-level table accessors for test monkeypatching)
- `lambdas/history_query/tests/conftest.py` -- NEW; copy `lambdas/history_consumer/tests/conftest.py` verbatim
- `lambdas/history_query/tests/test_history_query.py` -- NEW; fake tables (metadata get_item + history scan with filter semantics), I/O matrix + client-recorder purity probe (only `dynamodb` resource ever built)
- `lambdas/_shared/status.py:117-125` -- `get_record()` raises `NotFoundError` for unknown videoId — the 404 gate; READ-ONLY
- `lambdas/_shared/errors.py:68-74` -- `map_error()` exception→(status, body); READ-ONLY
- `lambdas/_shared/clients.py:87-90` -- `dynamodb_table()` factory; READ-ONLY
- `terraform/history.tf` -- EXTEND; add history-query zip source blocks (mirror the consumer's blocks at history.tf:87-125), role/policy, function, `aws_apigatewayv2_integration` + `aws_apigatewayv2_route` (`GET /videos/{videoId}/history`) + `aws_lambda_permission`, output
- `terraform/upload.tf:140-181` -- the gateway api/stage to extend + integration/route/permission pattern to mirror; `gateway_base_url` output already exists; READ-ONLY (no change)
- `terraform/history.tf:22-32` -- `status_history` table: PK `eventId` only, no videoId index — why the read is a filtered Scan; READ-ONLY
- `bruno/history-query.bru` -- NEW; GET `{{gatewayBaseUrl}}/videos/{{videoId}}/history`, assert blocks, poll-with-timeout (Design Notes)
- `bruno/upload-video.bru` -- EXTEND; add `script:post-response` capturing `res.body.videoId` into a collection var so the history request chains after the upload journey (spec draft said `script:test` — Bruno 4.0 rejects that block name; `script:post-response` is the renamed equivalent, verified live)
- `tests/integration/test_history_query.py` -- NEW; gateway-read journey: upload → poll to history entry → GET via gateway → 200 + entries match `stack.history_entries()` (conftest.py:335, the direct-table oracle); unknown videoId → 404. Pattern mirrors `tests/integration/test_history_leg.py` (upload + `poll_until` + cleanup)
- `_bmad-output/implementation-artifacts/sprint-status.yaml:53` -- `3-2-history-query-through-the-gateway` key to sync per workflow sprint-sync

## Tasks & Acceptance

**Execution:**
- [x] `lambdas/history_query/` -- implement handler + tests per I/O matrix (ATDD) -- FR-16, FR-13, NFR-3
- [x] `terraform/history.tf` -- declare history-query zip/role/policy/function, gateway integration + route + invoke permission, output -- AC1
- [x] `bruno/history-query.bru` + `bruno/upload-video.bru` videoId capture -- history request with poll-with-timeout, gateway URL only -- FR-22, AC4
- [x] `tests/integration/test_history_query.py` -- gateway-read journey + 404 passthrough -- AC2, AC3
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- sync `3-2-history-query-through-the-gateway` per workflow sprint-sync

**Acceptance Criteria:**
- Given floci running and `terraform apply`, when the environment is inspected, then the route `GET /videos/{videoId}/history` exists alongside the upload route with its integration, invoke permission, and the `history-query` function + role — upload-leg and Story 3.1 resources unchanged (FR-21)
- Given the full stack applied and a video uploaded through the gateway (Bruno collection), when the record reaches `PROCESSED` and the consumer has recorded it, then GET `/videos/{videoId}/history` via the gateway returns 200 with entries matching the ad-hoc `status-history` inspection (local boto3) — same eventId/status/timestamp (FR-16)
- Given an unknown `videoId`, when GET its history via the gateway, then the gateway passes the handler's 404 + `{"error": ...}` body through unchanged (FR-13, FR-21, NFR-3)
- Given the Bruno collection with the history request, when run after the upload journey, then it passes against the gateway URL only — poll-with-timeout, no fixed sleeps — and the returned entries match the ad-hoc `status-history` inspection (FR-22)
- Given the test suite, when pytest runs, then all new tests pass (handler I/O matrix, purity probe, integration journey) and all existing tests still pass; `bash scripts/ci-local.sh` is green end-to-end

## Spec Change Log

## Design Notes

- **Scan, not Query — and why that is architecture, not laziness:** `status-history` is keyed by `eventId` only (AD-3, append-per-unique-event); a videoId lookup has no index to hit. AD-3 forbids reshaping the key schema in this story, and the spine's own posture for lab-scale lookups without an index is a filtered `Scan` (same as title search). The table is disposable and tiny.
- **The 404 gate is the metadata lookup, not an empty scan:** this distinguishes "unknown video" (404, fail fast) from "entry hasn't arrived yet" (200 + empty, keep polling). The Bruno poll-with-timeout depends on exactly this distinction — a 404-on-empty design would make the async consumer leg untestable without races.
- **Bruno poll mechanism:** `script:pre-request` deadline loop — `await bru.sendRequest(...)` + `await bru.sleep(~2000)` until the response carries ≥1 entry or a ~120 s deadline throws; the request itself then fires once more and the `assert` block validates that final state. `upload-video.bru`'s `script:post-response` does `bru.setVar("videoId", res.body.videoId)` so the history request chains. (Bruno 4.0 note: `script:test` is rejected by the 4.0 grammar — `script:post-response` is the renamed equivalent; `bru.sendRequest` takes an axios-style config and the parsed body lives on `res.data`.)
- **Worktree state gap (inherited from Story 3.1):** the main checkout's `terraform.tfstate` may predate resources applied from other worktrees — if a fresh apply hits `EntityAlreadyExists`, copy the main state in and `terraform import` the untracked resources before applying.

## Verification

**Commands:**
- `cd terraform && terraform init -input=false && terraform apply -auto-approve -input=false` -- expected: Apply complete; new resources = history-query zip/role/policy/function, integration, route, lambda permission; existing resources untouched
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: all tests pass (query suite + all existing)
- ad-hoc via local boto3 + curl against `localhost:4566`: upload through the gateway → poll `video-metadata` to PROCESSED → poll `status-history` for the entry → `curl {gateway_base_url}/videos/{videoId}/history` returns 200 with the entry `{status: PROCESSED, eventId: UUID5(videoId, PROCESSED), timestamp}`; `curl {gateway_base_url}/videos/unknown-id/history` returns 404 with `{"error": ...}`
- `cd bruno && bru run --env Local` -- expected: upload + history requests pass (history polls until the entry appears)
- `bash scripts/ci-local.sh` -- expected: all 5 stages green

**Manual checks:**
- `curl -sf http://localhost:4566/_localstack/health` returns 200 before apply
- `terraform state list` shows upload-leg, transcode, processing, trigger, and Story 3.1 history resources unchanged after apply

## Suggested Review Order

**Handler — the whole design in 40 lines**

- Entry point: 404 gate → filtered Scan → sorted projection; the I/O matrix is this function
  [`handler.py:68`](../../lambdas/history_query/handler.py#L68)
- The gate itself — metadata lookup before any scan, fail-fast on unknown videoId
  [`handler.py:73`](../../lambdas/history_query/handler.py#L73)
- The read — filtered Scan with bound `:vid` (AD-3 key schema, no GSI; ponytail comment names the ceiling)
  [`handler.py:78`](../../lambdas/history_query/handler.py#L78)

**Gateway wiring (terraform/history.tf)**

- Zip source blocks — hand-maintained set, the known failure class; verify shared/ + history_query/ completeness
  [`history.tf:216`](../../terraform/history.tf#L216)
- The new route joining the existing gateway — `GET /videos/{videoId}/history`
  [`history.tf:326`](../../terraform/history.tf#L326)
- Invoke permission scoped to the new route only
  [`history.tf:332`](../../terraform/history.tf#L332)

**Bruno journey**

- Poll-with-timeout pre-request (2s loop, 120s deadline, videoId guard) — FR-22, no fixed sleeps
  [`history-query.bru:13`](../../bruno/history-query.bru#L13)
- videoId capture chaining the history request after upload (Bruno 4.0 `script:post-response`)
  [`upload-video.bru:23`](../../bruno/upload-video.bru#L23)

**Peripherals**

- Integration journey: upload → poll → GET via gateway → oracle compare; 404 passthrough proves pathParameters delivery
  [`test_history_query.py:10`](../../tests/integration/test_history_query.py#L10)
- Unit suite T1–T10: I/O matrix, filter binding, sort, projection, purity probe, config-not-code
  [`test_history_query.py:1`](../../lambdas/history_query/tests/test_history_query.py#L1)
- Function docs + suite count (lambdas/README.md convention)
  [`README.md:185`](../../lambdas/README.md#L185)
