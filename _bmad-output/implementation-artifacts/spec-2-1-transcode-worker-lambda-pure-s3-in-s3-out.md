---
title: 'Story 2.1: Transcode Worker Lambda (pure S3 in → S3 out)'
type: 'feature'
created: '2026-08-20'
status: 'done'
baseline_commit: 'b4769f50801881327820c1ebb6eafd003975af8c'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Epic 2's processing state machine needs a pure transcode worker to invoke — nothing exists yet. The worker must be exactly how real pipeline workers look: S3 object in → S3 object out, no status writes, no events (AD-4).

**Approach:** Implement the `transcode` Lambda (demo-mode copy fallback — no ffmpeg) on the shared layer's client factories, declare it in Terraform (`video-processed` bucket, zip, role, function), and verify by invoking it ad-hoc against a real upload from Epic 1's gateway journey.

## Boundaries & Constraints

**Always:**
- Python 3.11, stdlib-only handler code; all S3 access through `shared.clients.s3_client()` — bucket names strictly from Terraform-set env vars (`UPLOADS_BUCKET`, `PROCESSED_BUCKET`), endpoint from `AWS_ENDPOINT_URL` (NFR-4)
- Pure worker (AD-4): the handler performs **no** DynamoDB writes and publishes **no** events — it must not even import `shared.status` or `shared.events`
- Input contract: domain payload carrying `videoId` and `originalKey` (the shape Story 2.2's ASL will pass); extra fields are tolerated, missing/empty required fields are malformed input
- Processed key is tied to the same `videoId` (FR-6); the handler returns the domain payload for the ASL result: `{videoId, originalKey, processedKey, sizeBytes}`
- Demo-mode copy: stream the object body from `video-uploads` to `video-processed` (no ffmpeg; real transcoding is a documented future extension)
- Structured logging (videoId, keys, size) so the invocation is traceable in CloudWatch Logs (NFR-5)
- Zip layout mirrors `terraform/upload.tf`: hand-maintained `source` blocks putting `_shared` at zip root as `shared/` plus the `transcode/` package
- Keep the CI pipeline green (ruff E,F on `lambdas/`, `terraform fmt`, pytest, `terraform validate`, smoke apply)

**Ask First:**
- Any change to the input/output payload contract (Story 2.2's ASL consumes it)
- Any Terraform resource beyond: `video-processed` bucket, archive, role + policy, function
- Any change to `lambdas/_shared/` or to existing Terraform resources

**Never:**
- Status writes, event publishing, or DynamoDB/EventBridge access of any kind in this function
- Declare SQS queues/rules, Step Functions, the event-publisher, or the trigger leg (Stories 2.2/2.3)
- `aws` CLI in setup/teardown (the local CLI shim is broken — ad-hoc inspection/invoke via local boto3 against `localhost:4566`, per Story 1.2/1.3 precedent)
- Runtime dependencies beyond stdlib
- Modify `_bmad-output/` planning artifacts

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy transcode | `{videoId, originalKey}`; source object exists in `video-uploads` | object copied to `video-processed` under a key containing videoId; returns `{videoId, originalKey, processedKey, sizeBytes}`; log line emitted | N/A |
| Missing videoId | payload without `videoId` (or empty) | raises `MalformedInputError` | shared.errors |
| Missing originalKey | payload without `originalKey` (or empty) | raises `MalformedInputError` | shared.errors |
| Unknown source object | `originalKey` not in `video-uploads` | S3 error propagates uncaught (task failure — the ASL fails the execution in Story 2.2) | N/A |
| Redelivery / re-invoke | same payload twice | second run overwrites the same processed key — idempotent, no side effects beyond S3 | N/A |

</frozen-after-approval>

## Code Map

- `lambdas/transcode/__init__.py` -- NEW; empty package marker
- `lambdas/transcode/handler.py` -- NEW; `handler(event, context)` entry; module-level `_s3_client()` accessor wrapping `shared.clients.s3_client()` (test monkeypatch point, mirrors `upload_handler/handler.py` pattern)
- `lambdas/transcode/tests/conftest.py` -- NEW; copy `lambdas/upload_handler/tests/conftest.py` verbatim (adds `lambdas/` to sys.path, registers `_shared` as `shared`)
- `lambdas/transcode/tests/test_transcode.py` -- NEW; unit tests with a fake S3 client (no moto/boto3 needed); assertions encode the I/O matrix
- `lambdas/_shared/clients.py:93` -- `s3_client()` factory (boto3 confirmed in floci runtime); REUSE, do not modify
- `lambdas/_shared/errors.py:22` -- `MalformedInputError` for malformed payloads; REUSE
- `terraform/transcode.tf` -- NEW; `video-processed` bucket (`force_destroy = true`, lab teardown), archive (copy `upload.tf:15-53` source-block pattern + `transcode/` package), role + policy (logs; `s3:GetObject` on `video-uploads`/*; `s3:PutObject` on `video-processed`/* — nothing else), `aws_lambda_function.transcode` (python3.11, env `UPLOADS_BUCKET`/`PROCESSED_BUCKET`/`AWS_ENDPOINT_URL` = `local.lambda_endpoint_url`)
- `terraform/upload.tf:15` -- zip source-block pattern to copy; `terraform/upload.tf:55` -- `aws_s3_bucket.video_uploads` to reference for GetObject; `terraform/locals.tf:9` -- `local.lambda_endpoint_url`
- `lambdas/README.md`, `README.md` -- document the transcode worker (pure-worker contract, demo-mode copy, ad-hoc invoke); update Status
- `.github/workflows/ci.yml` -- READ-ONLY; pytest runs `lambdas/` (new tests run here), smoke stage applies the new resources — they must apply cleanly in floci

## Tasks & Acceptance

**Execution:**
- [x] `lambdas/transcode/__init__.py`, `lambdas/transcode/handler.py` -- implement the pure worker per Code Map/I-O matrix -- FR-6, AD-4
- [x] `lambdas/transcode/tests/` -- conftest (copy upload_handler's) + unit tests covering the I/O matrix incl. the no-status-writes/no-events guarantee -- green ATDD
- [x] `terraform/transcode.tf` -- declare bucket, archive, role, function -- FR-23, AC1
- [x] `lambdas/README.md`, `README.md` -- document the transcode worker + ad-hoc invoke; update Status -- keep docs truthful
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- sync `2-1-transcode-worker-lambda-pure-s3-in-s3-out` per workflow sprint-sync step (done by workflow at in-progress)

**Acceptance Criteria:**
- Given floci running and `terraform apply`, when the environment is inspected, then the `transcode` function, its role, and the `video-processed` bucket exist (FR-23)
- Given a video uploaded through the gateway (Epic 1), when the transcode Lambda is invoked ad-hoc with `{videoId, originalKey}`, then the processed object exists in `video-processed` under a key containing the videoId, the metadata record is unchanged (still UPLOADED), and no event was published (FR-6, AD-4)
- Given the invocation, when CloudWatch Logs are inspected, then the transcode run is visible with videoId/keys (NFR-5)
- Given the test suite, when pytest runs locally and in CI, then all transcode tests pass and the existing 54 tests still pass

## Design Notes

- **Processed key shape:** `processed/{videoId}/{basename}` where basename is the last path segment of `originalKey` (upload key shape is `{videoId}/{filename}`). Tied to the videoId, human-inspectable, deterministic.
- **Payload contract with Story 2.2:** the ASL will invoke with the domain payload derived from the `video.uploaded` detail — the handler requires only `videoId` + `originalKey` and ignores anything else, so the ASL can pass the full detail unchanged. The returned `{videoId, originalKey, processedKey, sizeBytes}` becomes the ASL input to the event-publisher task (publisher builds `processed_detail(videoId, bucket, originalKey, processedKey)` from it).
- **Copy mechanics:** `get_object` → stream `Body` into `put_object` (no temp file; lab objects are small). Preserve `ContentType` from the source object when present.
- **Error semantics:** this is not a client-facing function — no HTTP response mapping. Malformed payload raises `MalformedInputError`; S3 failures propagate raw. Either way the invocation fails, which is exactly what the ASL task needs (Story 2.2 fails the execution).
- **Terraform function name** `transcode`; dir/package `transcode` (already importable — no underscore rename needed, unlike `upload_handler`).

## Verification

**Commands:**
- `cd terraform && terraform init -input=false && terraform apply -auto-approve -input=false` -- expected: Apply complete; new resources = bucket, archive, role, policy, function; existing resources untouched
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: all tests pass (new transcode tests + existing 54)
- ad-hoc via local boto3 against `localhost:4566`: upload through the gateway (`curl -F` per README), then `lambda.invoke(FunctionName='transcode', Payload={"videoId": ..., "originalKey": ...})` -- expected: 200-style payload with `processedKey`; `head_object` confirms the object in `video-processed`; the metadata record is still UPLOADED; no new event on `video-bus`
- CloudWatch: inspect the transcode log group via boto3 `logs` client -- expected: invocation log lines with videoId/keys
- `bash scripts/ci-local.sh` -- expected: all stages green (gitleaks, ruff, terraform fmt, pytest, tf validate, smoke)

**Manual checks:**
- `curl -sf http://localhost:4566/_localstack/health` returns 200 before apply
- `terraform state list` shows no changes to upload-leg or smoke resources after apply

## Suggested Review Order

**Entry point — the pure worker**

- The whole function: validate payload → stream copy → return domain result.
  [`handler.py:94`](../../lambdas/transcode/handler.py#L94)

- Validation returns the stripped value so padded fields can't leak into keys.
  [`handler.py:67`](../../lambdas/transcode/handler.py#L67)

- Deterministic processed key `processed/{videoId}/{basename}` (FR-6).
  [`handler.py:79`](../../lambdas/transcode/handler.py#L79)

**Purity guarantee (AD-4)**

- AST import check + client-factory recorder: no status/events, no DDB/EventBridge client ever built.
  [`test_transcode.py:361`](../../lambdas/transcode/tests/test_transcode.py#L361)

**Terraform**

- Least-privilege role (GetObject on uploads, PutObject on processed, logs only).
  [`transcode.tf:70`](../../terraform/transcode.tf#L70)

- Function wiring: python3.11, config-not-code env vars, zip from hand-maintained source blocks.
  [`transcode.tf:102`](../../terraform/transcode.tf#L102)

- Zip layout mirrors `upload.tf`: `_shared` at root as `shared/` + `transcode/` package.
  [`transcode.tf:12`](../../terraform/transcode.tf#L12)

**Peripherals**

- I/O-matrix ATDD suite incl. review-patch branches (strip, non-dict, zero-byte, unset env).
  [`test_transcode.py:154`](../../lambdas/transcode/tests/test_transcode.py#L154)

- `video-processed` bucket with `force_destroy` for lab teardown.
  [`transcode.tf:51`](../../terraform/transcode.tf#L51)

- Docs: transcode worker section, ad-hoc invoke, updated Status.
  [`README.md`](../../README.md)
