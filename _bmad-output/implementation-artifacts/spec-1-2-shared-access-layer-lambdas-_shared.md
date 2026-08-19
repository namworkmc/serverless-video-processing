---
title: 'Story 1.2: Shared Access Layer (lambdas/_shared/)'
type: 'feature'
created: '2026-08-19'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'd46d947953512f40b5b8fb2f1a16ed2fb4a23e6f'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every later function must enforce identical status-transition rules, event envelope shapes, and error semantics. Without one shared layer those rules get duplicated and drift — and the DynamoDB conditional-write enforcement point (FR-11/12/13) would exist in N copies.

**Approach:** Implement `lambdas/_shared/` as the single enforcement point — state machine via conditional writes, deterministic event envelopes, error mapping, env-driven client factories — plus a `smoke` Lambda declared in Terraform that runs the layer inside floci's real Docker runtime against the real `video-metadata` table, confirming boto3 availability (or wiring the proven stdlib/urllib fallback) and that conditional writes behave as designed.

## Boundaries & Constraints

**Always:**
- Python 3.11, stdlib-only runtime code; boto3 used iff the smoke run proves it present in the floci runtime image (document the outcome)
- Config-not-code: endpoint via `AWS_ENDPOINT_URL`, table/bus/bucket names via Terraform-set env vars; nothing hardcoded (NFR-4)
- Legal transitions exactly `UPLOADED→PROCESSING`, `PROCESSING→PROCESSED`, `PROCESSING→FAILED`; terminals final; same-status re-assertion idempotent with no side effect
- Transitions via `UpdateItem` + `ConditionExpression: #s = :expected`; create via `PutItem` + `attribute_not_exists(videoId)`, retry returns existing record unchanged
- `eventId` = UUID5 of `(videoId, status)` with a fixed namespace — identical across calls/restarts (NFR-2); every envelope carries `eventId` + `schemaVersion` + `detail`; event names verb-in-past
- Error mapping: transition conflict → 409, unknown videoId → 404, malformed input → 400, else 500, body `{"error": "<message>"}` (NFR-3)
- `video-metadata` table (PK `videoId` S, on-demand) is declared in Terraform in THIS story — the layer needs a real table; Story 1.3 reuses it, does not redeclare
- Smoke artifacts (table, function, role, zip) stay declared after verification as a re-runnable lab fixture; the smoke run cleans up its own test record

**Ask First:**
- Any change to the transition table, event detail shapes, or `schemaVersion` value
- Tearing smoke artifacts down instead of keeping them, or any Terraform resource beyond table + smoke function + role + archive
- Any provider `endpoints{}` change

**Never:**
- Declare buckets, EventBridge bus/rules, SQS, API Gateway, Step Functions, or real pipeline functions (Stories 1.3+)
- Publish events from the smoke function — envelope construction only
- `aws` CLI in setup/teardown (ad-hoc invoke/inspection only)
- Runtime dependencies beyond stdlib unless the boto3 check forces the documented fallback
- Modify `_bmad-output/` planning artifacts

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Legal transition | record `UPLOADED`, request `→PROCESSING` | condition matches; record updated, `updatedAt` advanced | N/A |
| Illegal transition | record `PROCESSED`, request `→PROCESSING` | ConflictError; record untouched | maps to 409 |
| Terminal re-assertion | record `PROCESSED`, request `→PROCESSED` | idempotent success, no write | N/A |
| Transition unknown videoId | no record | NotFoundError, never silent success | maps to 404 |
| Create twice, same videoId | second `create` call | first creates; second returns existing unchanged (FR-12) | N/A |
| Envelope build | `(videoId, status)` | identical `eventId` every call/restart; `eventId`+`schemaVersion`+`detail` present | N/A |
| Smoke rerun | table dirty from prior run | handler deletes its fixed test record at end; rerun starts clean | N/A |

</frozen-after-approval>

## Code Map

- `lambdas/_shared/` -- empty today (only `lambdas/README.md` exists); the layer to build
- `lambdas/_shared/status.py` -- STATUSES, LEGAL_TRANSITIONS, `create_record` (PutItem + `attribute_not_exists(videoId)`), `transition` (read → re-assert short-circuit → UpdateItem `#s = :expected` → on ConditionalCheckFailedException re-read ⇒ ConflictError), `get_record` (NotFoundError)
- `lambdas/_shared/events.py` -- fixed UUID5 namespace, `event_id(videoId, status)`, `build_envelope(name, detail)`; detail builders fix downstream shapes: `video.uploaded` = `{videoId, status, bucket, key}`, `video.processed` = `{videoId, status, bucket, originalKey, processedKey}`
- `lambdas/_shared/errors.py` -- MalformedInputError / NotFoundError / ConflictError / InternalError + `map_error` → `(status_code, {"error": msg})`
- `lambdas/_shared/clients.py` -- boto3 client factories (dynamodb, s3, events) reading `AWS_ENDPOINT_URL` + region; resource names passed in from caller env, never typed here
- `lambdas/_shared/tests/test_shared.py` -- pytest with fake DynamoDB client (stdlib only): transition table, idempotent create/re-assert, UUID5 determinism, envelope shape, error mapping
- `lambdas/smoke/handler.py` -- scenario handler (`create` / `create-idempotent` / `transition-legal` / `transition-illegal` / `reassert` / `envelope` / `all`); fixed test videoId; deletes its record before exit
- `terraform/providers.tf` -- provider skeleton, complete endpoints block; READ-ONLY this story
- `terraform/smoke.tf` -- NEW: `aws_dynamodb_table.video_metadata` (`video-metadata`, PK `videoId` S, PAY_PER_REQUEST), `archive_file` zipping `_shared` + `smoke` (shared importable at zip root), `aws_iam_role` (logs + dynamodb CRUD on table), `aws_lambda_function.smoke` (python3.11, env: `TABLE_NAME`, `AWS_ENDPOINT_URL=http://host.docker.internal:4566`)
- `README.md`, `lambdas/README.md` -- Status + shared-layer/smoke docs
- `requirements-dev.txt` -- NEW: pytest (local tests only, never in zips)
## Tasks & Acceptance

**Execution:**
- [x] `lambdas/_shared/*.py` -- implement status / events / errors / clients per Code Map -- single enforcement point for all later stories
- [x] `lambdas/_shared/tests/test_shared.py` -- unit tests covering every I/O Matrix row except floci-runtime rows -- edge cases verified locally
- [x] `lambdas/smoke/handler.py` -- scenario handler exercising create/transition/envelope against the real table via the layer -- runtime confirmation AC
- [x] `terraform/smoke.tf` -- declare table + zip + role + smoke function -- FR-23; real table + runtime probe
- [x] `requirements-dev.txt` -- add pytest -- local test runner
- [x] `lambdas/README.md`, `README.md` -- document shared layer, smoke fixture, boto3 outcome; Status section -- keep docs truthful
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- sync `1-2-shared-access-layer-lambdas-_shared` per workflow sprint-sync step (done by workflow at in-progress)

**Acceptance Criteria:**
- Given `lambdas/_shared/` implemented, when unit tests run locally, then all pass — transition table, idempotent create, idempotent re-assert, UUID5 determinism, envelope shape, error mapping (FR-11/12/13, NFR-2/3)
- Given floci running and `terraform apply`, when the smoke Lambda is invoked ad-hoc in floci's real Docker runtime, then it reports boto3 present (or the stdlib fallback is wired in and documented) and every scenario passes against the real table — including ConditionalCheckFailedException on the illegal transition (Architecture deferred item resolved)
- Given the Terraform configuration, when `terraform plan` runs, then exactly table + function + role + archive are created and nothing else (FR-23)
- Given any runtime module, when grepped for endpoints or resource names, then all come from env vars — none string-typed (NFR-4)

## Spec Change Log

## Design Notes

- **Zip layout:** one `archive_file` over two source dirs — `_shared` package at zip root plus `smoke/handler.py`; handler does `from shared import status, events, errors, clients`. Zip packaging only; no Lambda layer resource.
- **Transition algorithm:** GetItem first — unknown ⇒ NotFoundError; current == target ⇒ return current (no write); else UpdateItem with `#s = :expected`; on ConditionalCheckFailedException re-read and raise ConflictError.
- **boto3 decision tree:** smoke imports boto3 → present: clients.py uses it, documented. Absent: wire the spike-proven stdlib urllib fallback into `clients.py`, documented. No third option.
- **Smoke hygiene:** fixed test `videoId` constant; handler deletes its record at the end of every run so reruns and Story 1.3 start from an empty table.
- **`:expected` semantics (implementation clarification):** the ConditionExpression's `:expected` is the target status's unique legal source, derived from `LEGAL_TRANSITIONS` via the inverted `_EXPECTED_SOURCE` map (e.g. requesting `PROCESSED` asserts `#s = PROCESSING`). This makes the TABLE the enforcement point — an illegal transition fails the condition rather than an if-statement. Transitioning to `UPLOADED` is always a conflict (only `create_record` mints it).
- **Local test import alias:** `tests/conftest.py` registers the local `_shared/` dir as the `shared` package so `from shared import ...` matches the zip layout.
- **Verification deviation:** the `aws` CLI shim on this machine is broken (Python 3.12 launcher mis-resolves the script path), so the ad-hoc smoke invoke used local boto3 against `localhost:4566` instead — same inspection, same evidence.
- **boto3 outcome:** CONFIRMED present in the floci 1.6.0 runtime image (`boto3_available: true` from the smoke run). The stdlib/urllib fallback was not needed.

## Verification

**Commands:**
- `cd terraform && terraform init -input=false && terraform apply -auto-approve -input=false` -- expected: Apply complete with exactly the smoke resources
- `python -m pytest lambdas/_shared/tests -q` (venv with `requirements-dev.txt`) -- expected: all tests pass
- `aws lambda invoke --endpoint-url http://localhost:4566 --function-name smoke --payload '{"scenario":"all"}' $LOCALAPPDATA/Temp/smoke-out.json` (ad-hoc — allowed) -- expected: HTTP 200, JSON report with every scenario `pass` incl. boto3 availability flag
- `grep -rnE 'localhost:4566|video-metadata' lambdas/ --include='*.py'` -- expected: no hardcoded endpoint/table name in runtime code (env vars only; test fixtures exempt)

**Manual checks (if no CLI):**
- `curl -sf http://localhost:4566/_localstack/health` returns 200 before apply
- After smoke run, ad-hoc scan of `video-metadata` shows zero items (smoke cleaned up)

## Suggested Review Order

**The enforcement point — status state machine**

- The legal-transition table is the single source of truth Epic 2's ASL must mirror.
  [`status.py:33`](../../lambdas/_shared/status.py#L33)

- `:expected` derives from the inverted table — the TABLE rejects illegal transitions, not an if-statement.
  [`status.py:46`](../../lambdas/_shared/status.py#L46)

- Conditional-write transition: re-assert short-circuit, `ReturnValues=ALL_NEW`, fresh re-read on conflict.
  [`status.py:128`](../../lambdas/_shared/status.py#L128)

- Idempotent create via `attribute_not_exists(videoId)`; retry returns the existing record (FR-12).
  [`status.py:72`](../../lambdas/_shared/status.py#L72)

**Event envelopes & error semantics**

- Deterministic UUID5 eventId + name/status cross-check; detail shapes fixed here for all downstream consumers.
  [`events.py:43`](../../lambdas/_shared/events.py#L43)

- Error mapping contract: conflict→409, unknown→404, malformed→400, else 500, body `{"error": ...}`.
  [`errors.py:44`](../../lambdas/_shared/errors.py#L44)

**Config-not-code clients**

- Endpoint strictly from `AWS_ENDPOINT_URL` (no fallback), per-service client cache, env credentials.
  [`clients.py:30`](../../lambdas/_shared/clients.py#L30)

**Runtime proof — smoke fixture**

- Scenario-driven handler proving the layer inside floci's real Docker runtime; cleans up its own record.
  [`handler.py:142`](../../lambdas/smoke/handler.py#L142)

- Zip layout: `_shared` packaged at zip root as `shared/` — hand-maintained manifest, documented why.
  [`smoke.tf:12`](../../terraform/smoke.tf#L12)

- Shared local for the in-container endpoint — declared once, consumed by every future function.
  [`locals.tf:9`](../../terraform/locals.tf#L9)

- The smoke function: env-driven config, table + role declared alongside (Story 1.3 reuses the table).
  [`smoke.tf:102`](../../terraform/smoke.tf#L102)

**Supporting**

- FakeTable honours both conditional expressions + `ALL_NEW` — the unit-test double for the enforcement point.
  [`test_shared.py:25`](../../lambdas/_shared/tests/test_shared.py#L25)

- conftest registers local `_shared/` as the `shared` package so test imports match the zip layout.
  [`conftest.py:17`](../../lambdas/_shared/tests/conftest.py#L17)
