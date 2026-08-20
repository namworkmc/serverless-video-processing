---
title: 'Story 1.3: Upload Journey Through the Gateway'
type: 'feature'
created: '2026-08-19'
status: 'done'
review_loop_iteration: 1
baseline_commit: 'a5858cee5297906e367eaf4270f7bfa91a4469f8'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The lab has substrate (Story 1.1) and the shared enforcement layer (Story 1.2) but no client journey — nothing enters the pipeline yet. The first leg (HTTP multipart upload → S3 object + UPLOADED record + `video.uploaded` event) must work end-to-end through the gateway before Epic 2 can consume it.

**Approach:** Implement the `upload-handler` Lambda on top of the shared layer (raw multipart parse, mint videoId once, S3 put, idempotent record create, deterministic event publish), declare the upload leg in Terraform (`video-uploads` bucket, EventBridge bus, function + role, API Gateway v2 with `POST /videos/upload`, `apiId` output), and found the Bruno collection with the upload request. Red-phase ATDD scaffolds from the TEA run go green task-by-task.

## Boundaries & Constraints

**Always:**
- Python 3.11, stdlib-only handler code; all service access through the shared layer (`shared.status.create_record`, `shared.events.uploaded_detail`/`build_envelope`, `shared.errors.map_error`, `shared.clients` factories) — no hand-written boto3 calls or status writes
- Config-not-code: bucket/table/bus names and endpoint strictly from Terraform-set env vars (`UPLOADS_BUCKET`, `METADATA_TABLE`, `EVENT_BUS_NAME`, `AWS_ENDPOINT_URL`); nothing string-typed in runtime code (NFR-4)
- Parse the multipart body RAW (`isBase64Encoded: false` from floci's gateway) — never assume base64
- `videoId` = fresh UUID4 minted exactly once per request; the same id appears in the response, the S3 key, the record, and the event (FR-2)
- Optional `title` form field; absent/empty → fall back to the uploaded filename; stored in the record (FR-10)
- Side-effect order: S3 put → `create_record` → `put_events` (envelope via `build_envelope(EVENT_UPLOADED, uploaded_detail(...))`, `DetailType=video.uploaded`, `EventBusName` from env)
- Response 200 with JSON body carrying the minted `videoId`; errors via `map_error` → 400/500 with body `{"error": ...}` (NFR-3)
- Reuse `aws_dynamodb_table.video_metadata` from `terraform/smoke.tf` — do NOT redeclare the table
- Zip layout mirrors `smoke.tf`: hand-maintained `source` blocks putting `_shared` at zip root as `shared/` plus the handler package
- Bruno collection targets ONLY the gateway data-plane URL (`http://localhost:4566/_aws/execute-api/{apiId}/{stage}/...`) — never backend endpoints (FR-22)
- Keep the CI pipeline green (ruff E,F on `lambdas/`, `terraform fmt`, pytest, `terraform validate`, smoke apply)

**Ask First:**
- Any change to the `video.uploaded` detail shape, envelope contract, or shared-layer API
- Any Terraform resource beyond: bucket, bus, archive, role + policy, function, API GW v2 (API/integration/route/stage), Lambda invoke permission, outputs
- Any provider `endpoints{}` change
- Any change to the red-phase tests' expected values (assertions encode the ACs — fix the code, not the expectation; only the documented fixture-wiring adjustments are pre-approved)

**Never:**
- Declare SQS queues/rules, Step Functions, the processing leg, history/search tables or functions (Epic 2+)
- `aws` CLI in setup/teardown (ad-hoc inspection only — and the local CLI shim is broken, use local boto3 against `localhost:4566` for inspection, as Story 1.2 did)
- Runtime dependencies beyond stdlib
- Modify `_bmad-output/` planning artifacts
- Base64-decode the body as the default path

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy upload | multipart with file part (+ optional title) | 200 `{"videoId": <uuid4>}`; object in `video-uploads` under key containing videoId; record UPLOADED with timestamps, title, contentType, sizeBytes; one `video.uploaded` event with deterministic eventId | N/A |
| Title fallback | no title field | record title = uploaded filename | N/A |
| Missing file part | multipart without file part | 400 `{"error": ...}` | MalformedInputError → map_error |
| Empty / garbage body | `""` or non-multipart bytes | 400 `{"error": ...}` | MalformedInputError → map_error |
| No content-type header | event lacks content-type | 400 `{"error": ...}` | MalformedInputError → map_error |
| Duplicate filename | same filename twice | distinct S3 keys (distinct videoIds) | N/A |
| Downstream failure | S3/DDB/event error mid-flight | 500 `{"error": ...}`; orphaned partial state accepted (ingest-leg reconciliation deferred) | map_error |

</frozen-after-approval>

## Code Map

- `lambdas/upload_handler/handler.py` -- NEW; `handler(event, context)` entry; module-level client accessors `_s3_client()`, `_dynamo_table()`, `_events_client()` wrapping `shared.clients` with env names (the red-phase `deps` fixture monkeypatches exactly these three names)
- `lambdas/upload_handler/__init__.py` -- NEW; empty package marker
- `lambdas/upload_handler/tests/test_upload_handler.py` -- 21 red-phase scaffolds (10 classes); activation contract: remove `pytest.importorskip` + per-class `@pytest.mark.skip`; assertions are the AC encoding — do not weaken
- `lambdas/upload_handler/tests/conftest.py` -- path wiring already correct (`lambdas/` on sys.path, `shared` alias); no change expected
- `lambdas/_shared/status.py:72` -- `create_record(table, video_id, title, bucket, original_key, content_type=None, size_bytes=None)` — idempotent create, mints UPLOADED + timestamps
- `lambdas/_shared/events.py:69` -- `uploaded_detail(video_id, bucket, key)` fixes `{videoId, status, bucket, key}`; `build_envelope` (events.py:43) derives eventId + schemaVersion
- `lambdas/_shared/errors.py:44` -- `map_error(exc) → (status, {"error": msg})`; raise `MalformedInputError` for all parse failures
- `lambdas/_shared/clients.py:93` -- `s3_client()` / `events_client()` / `dynamodb_table(name)` factories (boto3 confirmed in runtime)
- `terraform/upload.tf` -- NEW; bucket `video-uploads`, bus, archive (copy smoke.tf:12-45 source-block pattern + `upload_handler/` package), role + policy (logs, s3:PutObject on bucket, dynamodb Get/Put/Update on table, events:PutEvents on bus), `aws_lambda_function.upload_handler` (python3.11, env vars incl. `local.lambda_endpoint_url`), API GW v2 API + `AWS_PROXY` integration + `POST /videos/upload` route + stage + `aws_lambda_permission`, outputs `api_id` + gateway base URL string
- `terraform/smoke.tf:12` -- zip source-block pattern to copy; `terraform/smoke.tf:47` -- the table to REUSE; `terraform/locals.tf:9` -- `local.lambda_endpoint_url`
- `bruno/` -- NEW collection: `bruno.json`, environment file with `gatewayBaseUrl` var, upload request (multipart file + optional title, assert 200 + videoId), malformed-request variant (assert 400 + error body)
- `README.md`, `lambdas/README.md` -- Status + upload-journey docs (gateway data-plane URL form, Bruno usage)
- `.github/workflows/ci.yml` -- READ-ONLY; pytest runs `lambdas/` (activated tests run here), smoke stage applies all new resources — they must apply cleanly in floci

## Tasks & Acceptance

**Execution:**
- [x] `lambdas/upload_handler/__init__.py`, `lambdas/upload_handler/handler.py` -- implement handler per Code Map/I-O matrix -- the upload leg (FR-1..4)
- [x] `lambdas/upload_handler/tests/test_upload_handler.py` -- activate red-phase scaffolds (remove importorskip + skip markers; adjust `deps` fixture wiring only if factory names differ) -- green phase of the ATDD run
- [x] `terraform/upload.tf` -- declare bucket, bus, zip, role, function, gateway API/integration/route/stage, invoke permission, `api_id` + base-URL outputs -- FR-23, AC1
- [x] `bruno/` -- found the collection: env file with gateway base URL, upload request with asserts, malformed variant -- FR-22 seed
- [x] `README.md`, `lambdas/README.md` -- document upload journey, `_aws/execute-api` URL form, Bruno usage; update Status -- keep docs truthful
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- sync `1-3-upload-journey-through-the-gateway` per workflow sprint-sync step (done by workflow at in-progress)

**Acceptance Criteria:**
- Given floci running and `terraform apply`, when the upload leg is inspected, then bucket, bus, function, role, gateway API + `POST /videos/upload` route + stage all exist and `api_id` is a Terraform output (FR-23)
- Given the gateway running, when a multipart video is POSTed to `/videos/upload` via the `_aws/execute-api` URL, then the response is 2xx with the minted videoId; the object exists in `video-uploads` under a key containing that videoId; the `video-metadata` record is UPLOADED with timestamps; a `video.uploaded` event with deterministic eventId is on the bus (FR-1..4)
- Given the Bruno collection, when run against a fresh apply, then every request passes its assertions and no request targets anything but the gateway base URL (FR-22 seed)
- Given a malformed request (missing file / unparseable body), when POSTed, then the gateway returns 400 with `{"error": ...}` passed through unchanged (NFR-3, FR-21)
- Given the activated test suite, when pytest runs locally and in CI, then all upload-handler tests pass and the 27 shared-layer tests still pass

## Spec Change Log

## Design Notes

- **Dir name `upload_handler` (underscore):** the architecture's `upload-handler` name is not Python-importable; the committed ATDD scaffolds already fix the `upload_handler` package and the zip-root layout. Terraform `function_name` stays `upload-handler`.
- **Multipart parsing:** stdlib only — encode the raw body `latin-1` to bytes and parse with `email.parser.BytesParser` + `email.policy.HTTP` (or equivalent cgi-free approach); the part with a `filename` in Content-Disposition is the video, plain fields supply `title`. Missing boundary/content-type/file part ⇒ MalformedInputError.
- **S3 key shape:** `{videoId}/{filename}` — contains the videoId, preserves the filename, distinct per upload (satisfies the test assertions).
- **Event entry:** `put_events(Entries=[{Source: <handler source string>, DetailType: "video.uploaded", Detail: json.dumps(envelope), EventBusName: env}])` — envelope = `build_envelope(EVENT_UPLOADED, uploaded_detail(...))`.
- **API GW v2 wiring:** `aws_apigatewayv2_api` (HTTP) + `aws_apigatewayv2_integration` (AWS_PROXY, Lambda invoke URI) + route `POST /videos/upload` + `aws_apigatewayv2_stage` + `aws_lambda_permission` for apigateway. Stage name is a local choice; the base-URL output embeds it in the `_aws/execute-api` form.
- **Bruno env var:** `gatewayBaseUrl` must be set from the `api_id` Terraform output after apply (documented in README); the collection itself stores only the variable placeholder.
- **Verification deviation sanctioned:** ad-hoc inspection uses local boto3 against `localhost:4566` (the machine's `aws` CLI shim is broken — Story 1.2 precedent).

## Verification

**Commands:**
- `cd terraform && terraform init -input=false && terraform apply -auto-approve -input=false` -- expected: Apply complete; new resources = bucket, bus, archive, role, policy, function, API, integration, route, stage, permission (+ outputs); existing smoke resources untouched
- `python -m pytest lambdas/ -q` (venv with `requirements-dev.txt`) -- expected: all tests pass (21 upload-handler activated + 27 shared)
- `curl -s -X POST "http://localhost:4566/_aws/execute-api/<apiId>/<stage>/videos/upload" -F "file=@<sample>" -F "title=My Video"` -- expected: 200 + `{"videoId": ...}`
- ad-hoc via local boto3 against `localhost:4566` -- expected: object in `video-uploads`, record UPLOADED with title/timestamps, `video.uploaded` event traceable (bus rule targets arrive in Epic 2; inspect via floci event records/logs)
- `curl` malformed variant (no file part) -- expected: 400 + `{"error": ...}`
- `uv run --with ruff ruff check lambdas/ --select E,F` and `terraform fmt -check -recursive` (in `terraform/`) -- expected: clean (CI parity)
- `bru run` in `bruno/` if the Bruno CLI is installed -- expected: all requests pass; if unavailable, the curl commands above are the substitute and the collection is authored for later runs

**Manual checks (if no CLI):**
- `curl -sf http://localhost:4566/_localstack/health` returns 200 before apply
- Terraform output `api_id` matches the URL used in verification

## Suggested Review Order

**Entry point — the upload leg**

- The whole design in one function: parse → mint videoId once → S3 → record → event.
  [`handler.py:179`](../../lambdas/upload_handler/handler.py#L179)

**Input hardening (review-loop fixes)**

- Raw multipart parse; latin-1 round-trip with undecodable-body rejection (400, not 500).
  [`handler.py:88`](../../lambdas/upload_handler/handler.py#L88)
- Zero-byte file part and non-text form field rejected at parse time.
  [`handler.py:142`](../../lambdas/upload_handler/handler.py#L142)
- Filename sanitization: path components and control chars stripped before the S3 key.
  [`handler.py:164`](../../lambdas/upload_handler/handler.py#L164)

**Side-effect integrity**

- EventBridge `FailedEntryCount` checked — a dropped event no longer masquerades as 200.
  [`handler.py:231`](../../lambdas/upload_handler/handler.py#L231)
- Structured logging: videoId/key/size on success, status/error on rejection.
  [`handler.py:236`](../../lambdas/upload_handler/handler.py#L236)

**Terraform — resource declarations**

- Bucket with `force_destroy` (lab teardown) and the custom EventBridge bus.
  [`upload.tf:55`](../../terraform/upload.tf#L55)
- Least-privilege IAM: `dynamodb:PutItem` only; invoke permission scoped to the upload route.
  [`upload.tf:102`](../../terraform/upload.tf#L102)
- Lambda sizing (`memory_size = 256` for body buffering) and config-not-code env vars.
  [`upload.tf:115`](../../terraform/upload.tf#L115)
- Gateway wiring: AWS_PROXY integration, `POST /videos/upload` route, `api_id` output.
  [`upload.tf:151`](../../terraform/upload.tf#L151)

**Tests — ACs executable**

- Activated red-phase scaffolds; assertions unchanged from the TEA run.
  [`test_upload_handler.py:173`](../../lambdas/upload_handler/tests/test_upload_handler.py#L173)
- Review-loop guard tests: unicode body, empty file, traversal filename, publish failure.
  [`test_upload_handler.py:555`](../../lambdas/upload_handler/tests/test_upload_handler.py#L555)

**Peripherals**

- Known limits documented: 6 MB ceiling, partial-failure semantics, event Detail shape.
  [`README.md:63`](../../README.md#L63)
- Bruno collection seed (gateway-URL-only requests, fake sample.mp4 stub).
  [`upload-video.bru:1`](../../bruno/upload-video.bru#L1)
