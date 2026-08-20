---
title: 'Story 2.2: Processing State Machine + Event Publisher'
type: 'feature'
created: '2026-08-20'
status: 'done'
baseline_commit: 'c7577efdaecb8fe4fd459b650d746dfdcd197011'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 2 has a pure transcode worker (Story 2.1) but nothing orchestrates it: no status transitions, no terminal event. The processing leg's core promise — status-first ordering that is structural (in the ASL, not in code discipline) — does not exist yet.

**Approach:** Implement the `event-publisher` Lambda (sole constructor of the `video.processed` envelope via the shared layer — AD-4/AD-6) and the `processing-state-machine` whose ASL is, in order: `Task(dynamodb:updateItem UPLOADED→PROCESSING)` → `Task(lambda:invoke transcode)` → `Task(dynamodb:updateItem →PROCESSED)` → `Task(lambda:invoke event-publisher)` — all declared in Terraform (`terraform/processing.tf`). Verify by starting an execution ad-hoc against a real Epic 1 upload: the record walks UPLOADED → PROCESSING → PROCESSED, the processed object exists, and exactly one `video.processed` event lands on the bus.

## Boundaries & Constraints

**Always:**
- ASL state order and inline condition pairs (`#s = UPLOADED` → `PROCESSING`, `#s = PROCESSING` → `PROCESSED`) mirror `shared.status.LEGAL_TRANSITIONS` exactly (AD-4); a transition-table change is one coordinated ASL + shared-layer change
- event-publisher is the sole constructor of the `video.processed` envelope: `shared.events.build_envelope(EVENT_PROCESSED, processed_detail(...))`; the ASL passes it only the domain payload (the transcode result); eventId = UUID5(videoId, PROCESSED), schemaVersion carried (FR-8, NFR-2)
- publisher wire shape mirrors the upload handler's flat Detail: `json.dumps({**envelope, **envelope["detail"]})`, `Source="event-publisher"`, `DetailType="video.processed"`, bus from `EVENT_BUS_NAME` env; `FailedEntryCount > 0` raises (a dropped terminal event must not masquerade as success)
- publisher handler is stdlib-only + shared layer; no DynamoDB access of any kind (it must not build a DDB client); bucket for the detail from `PROCESSED_BUCKET` env (config-not-code, NFR-4)
- Python 3.11, zip layout mirrors `transcode.tf` hand-maintained source blocks (`shared/` at zip root + `event_publisher/` package); handler string `event_publisher.handler.handler`
- state-machine IAM: `dynamodb:GetItem`/`UpdateItem` on `video-metadata`, `lambda:InvokeFunction` on transcode + event-publisher only; publisher role: logs + `events:PutEvents` on `video-bus` only (least privilege)
- any ASL change after first apply uses `terraform apply -replace=aws_sfn_state_machine.processing` (floci has no `UpdateStateMachine`) — documented in README and Design Notes
- keep the CI mirror green (gitleaks, ruff E,F, terraform fmt, pytest, terraform validate, smoke)

**Ask First:**
- Any change to `lambdas/_shared/` or to existing resources in `upload.tf` / `transcode.tf` / `smoke.tf`
- Any ASL shape deviation from the four-state chain above (e.g. Catch/Retry/Choice blocks, dropping `updatedAt`/`processedKey` writes)
- If floci rejects the `dynamodb:updateItem` direct integration or `$$.State.EnteredTime` in Parameters at apply/run time: HALT and report before inventing a workaround (a new floci gap needs a decision, not a silent redesign)

**Never:**
- Trigger leg: SQS queue, EventBridge rule, `sfn-trigger-shim` (Story 2.3)
- FAILED-producing path (deferred — rules exist in the shared layer, nothing exercises them in v1)
- `arn:aws:states:::events:putEvents` direct integration (unsupported on floci — publisher Lambda is mandatory)
- `aws` CLI for provisioning/inspection (local boto3 against `localhost:4566`)
- Runtime dependencies beyond stdlib in the publisher
- Modify `_bmad-output/` planning artifacts

## I/O & Edge-Case Matrix

event-publisher handler (input = transcode result passed by the ASL):

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy publish | `{videoId, originalKey, processedKey, sizeBytes}` | builds envelope via shared layer, `put_events` one entry on `video-bus` (flat Detail, Source `event-publisher`), structured log with videoId/eventId, returns the envelope | N/A |
| Missing/empty field | payload missing any of videoId / originalKey / processedKey | raises `MalformedInputError`, no event published | shared.errors |
| Non-dict event | `None` / list / string | raises `MalformedInputError` | shared.errors |
| Bus rejects entry | `put_events` returns `FailedEntryCount > 0` | raises `RuntimeError` (ASL task fails the execution) | N/A |
| Unset env | `PROCESSED_BUCKET` or `EVENT_BUS_NAME` missing | raises `RuntimeError` | N/A |
| Re-invoke (same input) | same payload twice | identical eventId both times (UUID5) — republish is a dedupe | N/A |

</frozen-after-approval>

## Code Map

- `lambdas/event_publisher/__init__.py`, `lambdas/event_publisher/handler.py` -- NEW; pattern mirrors `lambdas/transcode/handler.py` (module-level `_events_client()` / `_processed_bucket()` / `_event_bus_name()` accessors for test monkeypatching, `_require_field` validation returning stripped values)
- `lambdas/event_publisher/tests/conftest.py` -- NEW; copy `lambdas/transcode/tests/conftest.py` verbatim
- `lambdas/event_publisher/tests/test_event_publisher.py` -- NEW; fake events client, I/O matrix + purity probes (AST: no dynamodb client construction; recorder: only `events_client()` ever built)
- `lambdas/_shared/events.py:79` -- `processed_detail(video_id, bucket, original_key, processed_key)`; `events.py:43` `build_envelope`; REUSE, do not modify
- `lambdas/_shared/status.py:33` -- `LEGAL_TRANSITIONS` (the table the ASL condition pairs must mirror); `status.py:62` `TRANSITION_EXTRA_ATTRIBUTES` (processedKey is a sanctioned transition extra)
- `lambdas/upload_handler/handler.py:213-234` -- the wire shape to mirror (flat Detail `{**envelope, **envelope["detail"]}`, FailedEntryCount check)
- `terraform/processing.tf` -- NEW; event-publisher archive (copy `transcode.tf:12-49` source-block pattern), role + policy, function (env `PROCESSED_BUCKET`/`EVENT_BUS_NAME`/`AWS_ENDPOINT_URL` = `local.lambda_endpoint_url`), SFN execution role, `aws_sfn_state_machine.processing` (definition via `jsonencode`, `States.StartAt` chain per Intent), outputs (state machine ARN/name, publisher function)
- `terraform/transcode.tf:102` -- `aws_lambda_function.transcode` to reference in ASL FunctionName + InvokeFunction policy; `terraform/upload.tf:61` -- `aws_cloudwatch_event_bus.video_bus`; `terraform/smoke.tf:47` -- `aws_dynamodb_table.video_metadata`
- `terraform/providers.tf:29` -- `stepfunctions` endpoint already listed; no provider change needed
- `lambdas/event_publisher/tests/test_asl_definition.py` -- NEW; ASL↔shared-layer mirror backstop: extract the `jsonencode` definition from `terraform/processing.tf` (regex → JSON parse), assert the 4-state order, each updateItem's `ConditionExpression #s = :expected` pair matches `LEGAL_TRANSITIONS` sources, and Lambda tasks target the right function names
- `README.md`, `lambdas/README.md` -- document the processing state machine (ASL chain, ad-hoc `StartExecution` via boto3, `-replace` rule for ASL changes, publisher contract); update Status
- `.github/workflows/ci.yml` -- READ-ONLY; smoke stage unaffected (no smoke changes in this story)

## Tasks & Acceptance

**Execution:**
- [x] `lambdas/event_publisher/` -- implement handler + tests per I/O matrix (ATDD) -- FR-8, AD-4/AD-6
- [x] `terraform/processing.tf` -- declare publisher zip/role/function, SFN role, state machine, outputs -- FR-5/7, AC1
- [x] `lambdas/event_publisher/tests/test_asl_definition.py` -- ASL↔transition-table mirror test -- AD-4 consistency, keeps ASL honest without a live apply
- [x] `README.md`, `lambdas/README.md` -- state machine docs, ad-hoc StartExecution, `-replace` rule; update Status
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- sync `2-2-processing-state-machine-event-publisher` per workflow sprint-sync

**Acceptance Criteria:**
- Given floci running and `terraform apply`, when the environment is inspected, then the `processing-state-machine`, the SFN execution role, the `event-publisher` function and its role exist, and the ASL is the four-state chain with condition pairs mirroring `LEGAL_TRANSITIONS` (FR-23, AD-4)
- Given an `UPLOADED` video uploaded through the gateway (Epic 1), when an execution is started ad-hoc with the domain payload `{videoId, status, bucket, key}`, then the metadata record transitions UPLOADED → PROCESSING → PROCESSED in order (each acknowledged before the next state runs — FR-7), the processed object exists in `video-processed`, exactly one `video.processed` event is on `video-bus` carrying `eventId` = UUID5(videoId, PROCESSED) + `schemaVersion` (FR-8), and the execution is visible in Step Functions history with the videoId in its input (FR-5, NFR-5)
- Given the same execution re-run for the same video (record already PROCESSED), when the first updateItem condition fails, then the execution fails — status stays PROCESSED, no second event on the bus (FR-11 via ASL)
- Given any future ASL change, when it is applied, then it is done via `terraform apply -replace=aws_sfn_state_machine.processing`, documented in README + this spec's Design Notes
- Given the test suite, when pytest runs, then all new tests pass (publisher I/O matrix, purity probes, ASL mirror) and all existing tests still pass; `bash scripts/ci-local.sh` is green end-to-end

## Spec Change Log

## Design Notes

- **ASL shape:** input contract = the `video.uploaded` detail `{videoId, status, bucket, key}` (Story 2.3's shim will pass exactly that; ad-hoc starts use it too). Transcode task uses `Parameters` to map `{"videoId.$": "$.videoId", "originalKey.$": "$.key"}` (transcode requires `originalKey`; extras tolerated but the mapping keeps the worker contract explicit). Transcode result `{videoId, originalKey, processedKey, sizeBytes}` flows unchanged into the PROCESSED updateItem (which also SETs `processedKey` — a sanctioned `TRANSITION_EXTRA_ATTRIBUTES` member — and `updatedAt` from `$$.State.EnteredTime`) and into the publisher task. Publisher's `bucket` comes from its `PROCESSED_BUCKET` env var, not the ASL — the ASL carries domain payload only (AD-4).
- **Direct-integration encoding:** `arn:aws:states:::dynamodb:updateItem` with `Key.videoId.S.$ = $.videoId`, `UpdateExpression "SET #s = :next, updatedAt = :updatedAt, processedKey = :pk"` (second task), `ConditionExpression "#s = :expected"`, `ExpressionAttributeNames {"#s": "status"}`. floci's support for this integration is architecture-assumed, not spike-verified — the live AC run is the verification; the Ask-First gate covers a gap discovery.
- **Failure semantics:** no Catch/Retry — any task failure (condition failed, transcode S3 error, publisher reject) fails the execution, which is exactly the FR-11 behavior. The FAILED status path stays deferred.
- **FLOCI SHAPE GAP (probe-verified 2026-08-20):** floci's `lambda:invoke` returns the Lambda result DIRECTLY as the task result — no `{Payload: ...}` wrapper like real AWS. The Transcode task therefore uses `ResultPath: "$"` only; a `ResultSelector` unwrapping `$.Payload.*` resolved to nulls on floci (first live run failed at MarkProcessed with null videoId). On real AWS the ResultSelector would be required — recorded in the ASL Comment and the mirror test.

## Verification

**Commands:**
- `cd terraform && terraform init -input=false && terraform apply -auto-approve -input=false` -- expected: Apply complete; new resources = publisher zip/role/policy/function, SFN role/policy/state machine; existing resources untouched
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: all tests pass (publisher + ASL mirror + existing 86)
- ad-hoc via local boto3 against `localhost:4566`: upload through the gateway (`curl -F` per README) → `sfn.start_execution(stateMachineArn=<output>, input={"videoId","status","bucket","key"})` → poll `describe_execution` to SUCCEEDED; then assert: `dynamodb.get_item` status PROCESSED (+ processedKey set), `s3.head_object` on the processed key, EventBridge events on `video-bus` show exactly one `video.processed` with the deterministic eventId, `sfn.get_execution_history` shows the videoId in input and all four task states
- re-run `start_execution` with the same input -- expected: execution FAILED at the first updateItem; record still PROCESSED; still exactly one `video.processed` event
- `bash scripts/ci-local.sh` -- expected: all 5 stages green

**Manual checks:**
- `curl -sf http://localhost:4566/_localstack/health` returns 200 before apply
- `terraform state list` shows upload-leg, transcode, and smoke resources unchanged after apply

## Suggested Review Order

**Entry point — the state machine (AD-4)**

- The four-state chain: status-first ordering is structural, in the ASL.
  [`processing.asl.json:3`](../../terraform/processing.asl.json#L3)

- Condition pairs mirror the shared layer's legal-transition table exactly.
  [`processing.asl.json:38`](../../terraform/processing.asl.json#L38)

- State machine resource: ASL via templatefile, `-replace` rule for changes.
  [`processing.tf:175`](../../terraform/processing.tf#L175)

**Event publisher — sole envelope constructor (AD-4/AD-6)**

- The whole function: validate → build envelope via shared layer → publish one entry.
  [`handler.py:96`](../../lambdas/event_publisher/handler.py#L96)

- Flat wire Detail mirrors the upload handler; FailedEntryCount raises.
  [`handler.py:121`](../../lambdas/event_publisher/handler.py#L121)

- Bucket comes from env, not the ASL — domain payload only.
  [`handler.py:64`](../../lambdas/event_publisher/handler.py#L64)

**Mirror backstop (AD-4 consistency)**

- Parses the ASL as JSON and asserts condition pairs against LEGAL_TRANSITIONS.
  [`test_asl_definition.py:93`](../../lambdas/event_publisher/tests/test_asl_definition.py#L93)

- Floci lambda:invoke shape gap recorded: ResultPath only, no ResultSelector.
  [`test_asl_definition.py:145`](../../lambdas/event_publisher/tests/test_asl_definition.py#L145)

**Terraform IAM (least privilege)**

- SFN role: GetItem/UpdateItem on video-metadata + InvokeFunction on the two workers only.
  [`processing.tf:144`](../../terraform/processing.tf#L144)

- Publisher role: logs + PutEvents on video-bus only.
  [`processing.tf:84`](../../terraform/processing.tf#L84)

**Peripherals**

- I/O-matrix ATDD suite incl. purity probes (AST + client-recorder).
  [`test_event_publisher.py:121`](../../lambdas/event_publisher/tests/test_event_publisher.py#L121)

- Purity: no shared.status import, no DynamoDB client ever built.
  [`test_event_publisher.py:333`](../../lambdas/event_publisher/tests/test_event_publisher.py#L333)

- Docs: state machine section, ad-hoc StartExecution, floci platform facts.
  [`README.md`](../../README.md)
