Conduct a review of CONTENT.
Look for what's missing, not only what's wrong.
Find at least ten issues to fix or improve.
Output a Markdown list of findings only — no severity, priority, or ranking.
If the content is empty, stop and say so.
If you have zero findings, re-check and keep thinking; do not stop with an empty list.

CONTENT:
diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
index d2ac3a8..265ee54 100644
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -9,9 +9,10 @@
 #                      never blocks the pipeline)
 #   unit-test          pytest on lambdas/ (shared access layer suite)
 #   terraform-validate terraform init -backend=false + validate
-#   smoke              floci via docker compose -> terraform apply ->
-#                      invoke smoke Lambda through floci's Lambda API ->
-#                      assert all_pass -> terraform destroy (always)
+#   integration        floci via docker compose -> terraform apply ->
+#                      pytest tests/integration/ (T1-T10: gateway upload,
+#                      auto-processing, state machine, transcode, history)
+#                      -> terraform destroy (always)
 #
 # Deliberate omissions (backend-only stack, see progress doc):
 #   - no sharding (27 tests, single process)
@@ -133,14 +134,18 @@ jobs:
         working-directory: terraform
         run: terraform validate
 
-  smoke:
-    name: Smoke (floci + terraform apply + Lambda invoke)
+  integration:
+    name: Integration (floci + terraform apply + pytest suite)
     runs-on: ubuntu-latest
     timeout-minutes: 30
     needs: [unit-test, terraform-validate]
     steps:
       - uses: actions/checkout@v5
 
+      - uses: astral-sh/setup-uv@v7
+        with:
+          python-version: "3.11"
+
       - uses: hashicorp/setup-terraform@v4
         with:
           terraform_version: 1.6.1
@@ -156,12 +161,11 @@ jobs:
         working-directory: terraform
         run: terraform apply -auto-approve
 
-      - name: Invoke smoke Lambda via floci
+      - name: Run integration suite
         run: |
-          RESPONSE="$(curl -sS -X POST "http://localhost:4566/2015-03-31/functions/smoke/invocations" \
-            -H 'Content-Type: application/json' -d '{"scenario":"all"}')"
-          echo "$RESPONSE"
-          echo "$RESPONSE" | python3 -c "import json,sys; r=json.load(sys.stdin); sys.exit(0 if r['statusCode']==200 and r['body']['all_pass'] else 1)"
+          GATEWAY_BASE_URL="$(cd terraform && terraform output -raw gateway_base_url)" \
+            uv run --with 'pytest>=8.0' --with requests --with boto3 \
+            pytest tests/integration/ -q
 
       - name: Terraform destroy
         if: always()
@@ -176,6 +180,6 @@ jobs:
         if: failure()
         uses: actions/upload-artifact@v5
         with:
-          name: smoke-failure
+          name: integration-failure
           path: floci-logs.txt
           retention-days: 30
diff --git a/README.md b/README.md
index 967bc26..55e0881 100644
--- a/README.md
+++ b/README.md
@@ -269,6 +269,7 @@ retried, since a deterministic poison message would retry forever; real
 docker-compose.yaml # floci emulator
 terraform/          # all AWS resources (buckets, bus, tables, lambdas, gateway)
 lambdas/            # Lambda function source code (one dir per function)
+tests/              # integration test suite (CI stage 5, live stack required)
 bruno/              # Bruno API collection (gateway data plane only)
 _bmad-output/       # BMAD planning artifacts (PRD, architecture, epics)
 ```
@@ -329,10 +330,12 @@ enforced via DynamoDB conditional writes — `UpdateItem` +
 `errors.py` (conflict→409, unknown→404, malformed→400, else 500), and
 `clients.py` (env-driven boto3 factories — `AWS_ENDPOINT_URL`, no
 hardcoded names). The `video-metadata` table and a `smoke` Lambda fixture
-(`terraform/smoke.tf`) run the layer inside floci's real Docker runtime —
-smoke confirmed boto3 present in the floci 1.6.0 image, every scenario
-passes against the real table, and the fixture cleans up after itself.
-27 unit tests.
+ran the layer inside floci's real Docker runtime — smoke confirmed boto3
+present in the floci image, every scenario passed against the real table,
+and the fixture cleaned up after itself. The smoke gate has since been
+replaced by the `tests/integration/` pytest suite (CI stage 5); the table
+and the `video.processed` capture queue now live in
+`terraform/integration.tf`. 27 unit tests.
 
 ✅ **Story 1.1 complete** — the lab substrate is reproducible: floci
 pinned to `1.6.0` in `docker-compose.yaml` (with the Docker socket
diff --git a/_bmad-output/implementation-artifacts/spec-integration-tests-step-1-gate-swap.md b/_bmad-output/implementation-artifacts/spec-integration-tests-step-1-gate-swap.md
new file mode 100644
index 0000000..3eee4c0
--- /dev/null
+++ b/_bmad-output/implementation-artifacts/spec-integration-tests-step-1-gate-swap.md
@@ -0,0 +1,98 @@
+---
+title: 'Integration test suite — Step 1: swap the CI smoke gate'
+type: 'feature'
+created: '2026-08-22'
+status: 'in-progress' # draft | ready-for-dev | in-progress | in-review | done
+review_loop_iteration: 0
+baseline_commit: 'b96a2a8a8ab3c551c01afbcfb7e0844c83f571e1'
+context:
+  - '{project-root}/_bmad-output/test-artifacts/integration-test-plan.md'
+---
+
+<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">
+
+## Intent
+
+**Problem:** CI stage 5 gates on the `smoke` Lambda invoked through floci's Lambda API, bypassing API Gateway entirely — the gateway route, request/response mapping, and the `_aws/execute-api` data plane are never exercised by CI (plan §1).
+
+**Approach:** Step 1 of `_bmad-output/test-artifacts/integration-test-plan.md`: add a pytest integration suite (`tests/integration/`, T1–T10) that drives the deployed stack through real gateway calls and AWS-API side-effect reads; rename `terraform/smoke.tf` → `integration.tf` stripping only the smoke function/role/archive; rewire stage 5 in both CI entry points; update docs. `lambdas/smoke/` survives — Step 2 deletes it after one green CI run (D8).
+
+## Boundaries & Constraints
+
+**Always:**
+- Follow integration-test-plan.md decisions D1–D8; scope is §6 Step 1 items 1–4 exactly.
+- Keep capture queue + policy + rule + target at IDENTICAL Terraform resource addresses (`aws_sqs_queue.smoke_capture`, `aws_sqs_queue_policy.smoke_capture`, `aws_cloudwatch_event_rule.video_processed_capture`, `aws_cloudwatch_event_target.smoke_capture_queue`) — no state migration, no recreate (D4).
+- T1 uses the D6 binary fixture (`bytes(range(256)) * 4`, generated in conftest — no fixture file) and asserts byte-identical S3 round-trip.
+- Work in the existing worktree `.worktrees/integration-test-plan` (branch `bmad/integration-test-plan`).
+- `bash scripts/ci-local.sh` fully green before committing.
+
+**Ask First:**
+- Any deviation from plan decisions D1–D8 or from the T1–T10 assertions in plan §4.
+
+**Never:**
+- Do not delete or modify `lambdas/smoke/` (Step 2).
+- Do not touch AGENTS.md or `lambdas/README.md` smoke references (Step 2 items).
+- No new infrastructure beyond what exists; no `aws` CLI anywhere; no new Python deps beyond pytest/requests/boto3 via `uv run --with`.
+- Do not rename or redeclare the `video-metadata` table (it stays in the renamed file).
+
+## I/O & Edge-Case Matrix
+
+| Scenario | Input / State | Expected Output / Behavior | Error Handling |
+|----------|--------------|---------------------------|----------------|
+| T1 happy upload | multipart: binary fixture (all 256 byte values) + title → `{gateway}/videos/upload` | 200 `{"videoId"}`; `video-uploads` object at `{videoId}/{filename}` byte-identical to fixture; `video-metadata` record UPLOADED with createdAt/updatedAt | N/A |
+| T2 malformed | multipart without file part | 400 with `{"error": ...}` body passed through unchanged | N/A |
+| T3 journey | T1 upload | record → PROCESSED (poll ≤180 s); `video-processed` object `processed/{videoId}/{filename}`; capture queue exactly 1 `video.processed` for eventId=UUID5(videoId, PROCESSED); SFN execution `eb-{UUID5(videoId, UPLOADED)}` exists | poll timeout fails test |
+| T4 redelivery | republish same `video.uploaded` via `events:put_events` | still exactly 1 execution, record still PROCESSED, no 2nd processed event (ExecutionAlreadyExists acked) | wait, then assert |
+| T5 ad-hoc SFN | seeded S3 object + UPLOADED record; StartExecution `{videoId, status, bucket, key}` | PROCESSED with `processedKey`; processed object exists; exactly 1 processed event | N/A |
+| T6 rerun | StartExecution again (fresh name, record PROCESSED) | execution FAILED at MarkProcessing; status stays PROCESSED; no 2nd event | N/A |
+| T7 ad-hoc transcode | seeded object + record; invoke deployed `transcode` via floci Lambda REST `{videoId, originalKey}` | processed object exists; record still UPLOADED; no event published | N/A |
+| T8 history | T1 upload | exactly one `status-history` entry `{eventId, videoId, status: PROCESSED, timestamp}` with deterministic eventId | poll |
+| T9 history dedupe | republish `video.processed` | still exactly one entry for that eventId | N/A |
+| T10 poison | `video.processed` with fabricated eventId + unknown videoId | no table entry; message acked (no retry storm) | N/A |
+
+</frozen-after-approval>
+
+## Code Map
+
+- `tests/integration/conftest.py` (NEW) — boto3 clients pinned to `http://localhost:4566` (creds `test`/`test`, `us-east-1`); `gateway_base_url` from `GATEWAY_BASE_URL` env, fallback `terraform output -raw gateway_base_url` (plan D5); constants `video-metadata`, `video-uploads`, `video-processed`, `video-bus`, `status-history`, `smoke-capture-queue`; binary fixture `bytes(range(256)) * 4` (D6); `poll_until(fn, timeout=180)`; capture-queue drain scoped to own eventId; per-test cleanup (metadata + history items, objects in both buckets).
+- `tests/integration/test_upload_api.py` (NEW) — T1, T2.
+- `tests/integration/test_processing_journey.py` (NEW) — T3, T4.
+- `tests/integration/test_state_machine.py` (NEW) — T5, T6.
+- `tests/integration/test_transcode.py` (NEW) — T7.
+- `tests/integration/test_history_leg.py` (NEW) — T8, T9, T10.
+- `terraform/smoke.tf` → `terraform/integration.tf` — keep `aws_dynamodb_table.video_metadata` (lines 47–57), `aws_sqs_queue.smoke_capture` (66–69), `aws_sqs_queue_policy.smoke_capture` (71–88), `aws_cloudwatch_event_rule.video_processed_capture` (90–97), `aws_cloudwatch_event_target.smoke_capture_queue` (99–103); delete `data.archive_file.smoke_zip`, `aws_iam_role.smoke`, `aws_iam_role_policy.smoke`, `aws_lambda_function.smoke`, `output "smoke_function"`; rewrite header comment.
+- `.github/workflows/ci.yml` — `smoke` job (lines 136–181): rename job/steps, replace invoke step per plan §5; keep destroy `if: always()`, failure logs, artifact (rename `smoke-failure` → `integration-failure`); update header comment (lines 12–14).
+- `scripts/ci-local.sh` — stage 5 (lines 24–30) same replacement.
+- `docs/ci.md` — stage table row 17, job graph 19, secrets para 55, "Smoke stage details" section 70–81, troubleshooting row 95.
+- `README.md` — smoke references at lines 331–333, 339.
+- Contracts (read-only): upload key `{videoId}/{filename}` (`lambdas/upload_handler/handler.py:194`); processed key `processed/{videoId}/{basename}` (`lambdas/transcode/handler.py:68-76`); eventId `uuid5(ns 99881bbf-…, "{videoId}:{status}")` (`lambdas/_shared/events.py:24,38-40`); wire Detail = flat `{**envelope, **envelope["detail"]}` (`upload_handler/handler.py:228`, `event_publisher/handler.py:101`); execution name `eb-{eventId}` (`lambdas/sfn_trigger_shim/handler.py:48,131`); transcode REST invoke `POST http://localhost:4566/2015-03-31/functions/transcode/invocations` — floci 1.7 wraps result as `{Payload, StatusCode}`; history item `{eventId(PK), videoId, status, timestamp}` (`lambdas/history_consumer/handler.py:150-158`); ASL fails at MarkProcessing when record ≠ UPLOADED (`terraform/processing.asl.json:5-24`).
+
+## Tasks & Acceptance
+
+**Execution:**
+- [x] `tests/integration/conftest.py` — create fixtures/helpers per Code Map.
+- [x] `tests/integration/test_upload_api.py`, `test_processing_journey.py`, `test_state_machine.py`, `test_transcode.py`, `test_history_leg.py` — implement T1–T10 per I/O matrix and plan §4.
+- [x] `terraform/smoke.tf` → `terraform/integration.tf` — strip smoke resources, keep kept-resources at identical addresses, rewrite header comment.
+- [x] `.github/workflows/ci.yml` — rewire stage 5 per plan §5.
+- [x] `scripts/ci-local.sh` — rewire stage 5 per plan §5. (+ `COMPOSE_PROJECT_NAME` pin so worktree checkouts reuse the running floci instead of spawning a second one on 4566.)
+- [x] `docs/ci.md` + `README.md` — update smoke references to the integration stage.
+
+**Acceptance Criteria:**
+- Given the stack applied from the renamed `integration.tf`, when `bash scripts/ci-local.sh` runs, then all 5 stages are green with stage 5 running `pytest tests/integration/ -q` (10 tests pass).
+- Given an existing tfstate containing the smoke resources, when `terraform apply` runs after the rename, then the plan destroys only `data.archive_file.smoke_zip`, `aws_iam_role.smoke`, `aws_iam_role_policy.smoke`, `aws_lambda_function.smoke`, `output "smoke_function"` — every other resource shows no change (identical addresses).
+- Given T1 runs, when the uploaded object is read back from `video-uploads`, then its bytes equal the binary fixture exactly.
+- Given the commit is made, when `lambdas/smoke/` is inspected, then it is untouched.
+
+## Design Notes
+
+- eventId derivation is re-derived in conftest with `uuid.UUID("99881bbf-05eb-5ec6-8f3a-490d7496e518")` + `uuid5(ns, f"{video_id}:{status}")` — the namespace/derivation is a frozen wire contract (`_shared/events.py:24`), and importing `lambdas/_shared` would drag package layout into the integration suite.
+- Capture-queue hygiene (plan §4): each journey test drains the queue at start; assertions count only messages whose detail `eventId` matches the test's own videoId — CI runs serially, residue from earlier tests is ignored, not assumed away.
+- T5/T7 seed via direct boto3 S3 put + `status.create_record`-shaped PutItem (plain boto3 `put_item`, not the shared layer) — isolates the state-machine/transcode legs from the upload path.
+- T4/T9 republish with the SAME deterministic eventId the real producer used — the dedupe under test is the deterministic-id collision, so a fresh fabricated id would test nothing.
+
+## Verification
+
+**Commands:**
+- `bash scripts/ci-local.sh` — expected: all 5 stages green (the commit gate).
+- `(cd terraform && terraform plan)` against the pre-change state — expected: destroys limited to the five smoke resources; zero other changes.
+- `GATEWAY_BASE_URL=$(cd terraform && terraform output -raw gateway_base_url) uv run --with 'pytest>=8.0' --with requests --with boto3 pytest tests/integration/ -q` — expected: 10 passed.
diff --git a/docs/ci.md b/docs/ci.md
index 9151bbf..ccd5705 100644
--- a/docs/ci.md
+++ b/docs/ci.md
@@ -14,9 +14,9 @@ GitHub Actions pipeline for the serverless-video-processing floci lab.
 | `lint` | `ruff check lambdas/ --select E,F` + `terraform fmt -check -recursive` | both must pass |
 | `unit-test` | `pytest lambdas/` — shared access layer suite (27 tests) | 100% pass |
 | `terraform-validate` | `terraform init -backend=false` + `terraform validate` | valid config |
-| `smoke` | floci via `docker compose up -d --wait` → `terraform apply` → invoke `smoke` Lambda through floci's Lambda API → assert `statusCode==200 && body.all_pass` → `terraform destroy` (always) | smoke report all_pass |
+| `integration` | floci via `docker compose up -d --wait` → `terraform apply` → `pytest tests/integration/` (T1–T10: gateway upload with binary round-trip, auto-processing journey, state machine, transcode, history leg) → `terraform destroy` (always) | 10 integration tests pass |
 
-Job graph: `gitleaks → lint → {unit-test, terraform-validate} → smoke`.
+Job graph: `gitleaks → lint → {unit-test, terraform-validate} → integration`.
 
 ## Secrets scanning (gitleaks)
 
@@ -52,7 +52,7 @@ Concurrency: one run per ref, in-progress runs cancelled.
 ## Secrets
 
 **None required.** floci uses dummy credentials (`test`/`test`), Terraform state
-is local, and the smoke invoke goes through floci's unauthenticated Lambda API.
+is local, and the integration suite talks to floci's unauthenticated APIs.
 The `gitleaks` job passes the auto-provided `GITHUB_TOKEN` (mandatory for
 scanning pull requests) — no repository secret to configure.
 If real-AWS deployment is added later, that gets its own workflow with
@@ -67,17 +67,24 @@ never hardcoded.
 - Actions are on Node-24 majors (`checkout@v5`, `setup-uv@v7`, `setup-terraform@v4`, `upload-artifact@v5`) — GitHub deprecated the Node 20 runner runtime (Sept 2025) and warns on older majors
 - ruff and pytest are pulled ad-hoc by `uv run --with` — no lockfile needed yet
 
-## Smoke stage details
-
-The smoke Lambda (`lambdas/smoke/handler.py`, declared in `terraform/smoke.tf`)
-exercises the shared access layer inside floci's real Docker runtime:
-create, idempotent create, legal/illegal transitions, re-assertion, envelope
-determinism, not-found. It self-cleans its fixed test record, so reruns are
-safe. The CI invoke uses floci's Lambda REST API directly
-(`POST http://localhost:4566/2015-03-31/functions/smoke/invocations`) — no
-`aws` CLI in the pipeline, matching the lab's Terraform-only rule.
-
-`terraform destroy` runs with `if: always()` so a failed smoke never leaves
+## Integration stage details
+
+The pytest suite in `tests/integration/` drives the DEPLOYED stack through
+real API Gateway calls (`POST /videos/upload` via floci's
+`_aws/execute-api` data plane) and real AWS-API side-effect reads (S3,
+DynamoDB, SQS, EventBridge, Step Functions). Coverage: binary upload
+round-trip (byte-identical), malformed-request 400s, the full
+auto-processing journey (handler → rule → queue → shim → SFN → transcode →
+publisher), redelivery dedupe, ad-hoc state-machine and transcode invokes,
+and the history leg (recorded / deduped / poison-dropped). Design record:
+`_bmad-output/test-artifacts/integration-test-plan.md`. The gateway base
+URL comes from `terraform output -raw gateway_base_url` (env override
+`GATEWAY_BASE_URL` honored); the capture queue (`smoke-capture-queue`,
+declared in `terraform/integration.tf`) is the `video.processed`
+observation point. No `aws` CLI in the pipeline, matching the lab's
+Terraform-only rule.
+
+`terraform destroy` runs with `if: always()` so a failed suite never leaves
 state behind on the runner. On failure, floci logs are captured and uploaded.
 
 ## Deliberate omissions
@@ -92,7 +99,7 @@ state behind on the runner. On failure, floci logs are captured and uploaded.
 | Symptom | Likely cause / fix |
 | --- | --- |
 | `docker compose up -d --wait` times out | floci image pull slow on cold runner cache; rerun. Locally: check Docker Desktop is running. |
-| Smoke invoke returns 500 with `all_pass: false` | Read the scenario report in the response body — it names the failing check. Pull `smoke-failure` artifact for floci logs. |
+| Integration test fails or times out | Read the failing test's assertion — it names the resource/state that diverged. Polling timeouts are generous (180 s) for cold floci Lambda containers; rerun once on cold-cache flakes. Pull `integration-failure` artifact for floci logs. |
 | `terraform apply` fails with `InvalidClientTokenId` | A service endpoint is missing from the provider `endpoints{}` block (spine AD-8 fact 3). Add it to `terraform/providers.tf`. |
 | Lambda invoke hangs / times out | floci needs the Docker socket to spawn Lambda containers; on a self-hosted runner verify `/var/run/docker.sock` access. GitHub-hosted `ubuntu-latest` provides it. |
 | Ruff fails on a new file | Run `uv run --with ruff ruff check lambdas/ --select E,F` locally and fix. |
diff --git a/scripts/ci-local.sh b/scripts/ci-local.sh
index e7ab6bc..0327cf6 100644
--- a/scripts/ci-local.sh
+++ b/scripts/ci-local.sh
@@ -21,12 +21,14 @@ uv run --with 'pytest>=8.0' pytest lambdas/ -q
 echo "==> [4/5] terraform-validate"
 (cd terraform && terraform init -backend=false -input=false >/dev/null && terraform validate)
 
-echo "==> [5/5] smoke (requires Docker; reuses running floci if healthy)"
-docker compose up -d --wait
+echo "==> [5/5] integration (requires Docker; reuses running floci if healthy)"
+# Pin the compose project name: it defaults to the directory name, so a
+# git-worktree checkout would otherwise try to start a SECOND floci on the
+# same port instead of reusing the healthy one.
+COMPOSE_PROJECT_NAME=serverless-video-processing docker compose up -d --wait
 (cd terraform && terraform init -input=false >/dev/null && terraform apply -auto-approve)
-RESPONSE="$(curl -sS -X POST "http://localhost:4566/2015-03-31/functions/smoke/invocations" \
-  -H 'Content-Type: application/json' -d '{"scenario":"all"}')"
-echo "$RESPONSE"
-echo "$RESPONSE" | python -c "import json,sys; r=json.load(sys.stdin); sys.exit(0 if r['statusCode']==200 and r['body']['all_pass'] else 1)"
+GATEWAY_BASE_URL="$(cd terraform && terraform output -raw gateway_base_url)" \
+  uv run --with 'pytest>=8.0' --with requests --with boto3 \
+  pytest tests/integration/ -q
 
 echo "==> CI mirror: all stages green"
diff --git a/terraform/integration.tf b/terraform/integration.tf
new file mode 100644
index 0000000..e1dca36
--- /dev/null
+++ b/terraform/integration.tf
@@ -0,0 +1,65 @@
+# Integration test infrastructure (formerly smoke.tf — the smoke Lambda,
+# its role, and its zip were retired when CI stage 5 switched to the
+# tests/integration/ pytest suite; see
+# _bmad-output/test-artifacts/integration-test-plan.md).
+#
+# Declares the video-metadata table (the shared layer's enforcement target;
+# Story 1.3 REUSES this table, it does not redeclare it) and the capture
+# queue the integration suite uses as its video.processed observation point.
+
+resource "aws_dynamodb_table" "video_metadata" {
+  name         = "video-metadata"
+  billing_mode = "PAY_PER_REQUEST"
+
+  hash_key = "videoId"
+
+  attribute {
+    name = "videoId"
+    type = "S"
+  }
+}
+
+# --- video.processed capture queue -----------------------------------------
+#
+# Capture queue: the video.processed rule targets it so the integration
+# suite can assert "exactly one event with the deterministic eventId".
+# The pytest suite drains it; backlog is test residue. Epic 3's history
+# queue is a SEPARATE consumer of the same event (AD-1 pattern).
+resource "aws_sqs_queue" "smoke_capture" {
+  name                       = "smoke-capture-queue"
+  visibility_timeout_seconds = 60
+}
+
+resource "aws_sqs_queue_policy" "smoke_capture" {
+  queue_url = aws_sqs_queue.smoke_capture.id
+
+  policy = jsonencode({
+    Version = "2012-10-17"
+    Statement = [{
+      Effect    = "Allow"
+      Principal = { Service = "events.amazonaws.com" }
+      Action    = "sqs:SendMessage"
+      Resource  = aws_sqs_queue.smoke_capture.arn
+      Condition = {
+        ArnEquals = {
+          "aws:SourceArn" = aws_cloudwatch_event_rule.video_processed_capture.arn
+        }
+      }
+    }]
+  })
+}
+
+resource "aws_cloudwatch_event_rule" "video_processed_capture" {
+  name           = "video-processed-to-smoke-capture"
+  event_bus_name = aws_cloudwatch_event_bus.video_bus.name
+
+  event_pattern = jsonencode({
+    detail-type = ["video.processed"]
+  })
+}
+
+resource "aws_cloudwatch_event_target" "smoke_capture_queue" {
+  rule           = aws_cloudwatch_event_rule.video_processed_capture.name
+  event_bus_name = aws_cloudwatch_event_bus.video_bus.name
+  arn            = aws_sqs_queue.smoke_capture.arn
+}
diff --git a/terraform/smoke.tf b/terraform/smoke.tf
deleted file mode 100644
index 759bf35..0000000
--- a/terraform/smoke.tf
+++ /dev/null
@@ -1,217 +0,0 @@
-# Story 1.2 — Shared Access Layer smoke fixture.
-#
-# Declares the video-metadata table (the shared layer's enforcement target;
-# Story 1.3 REUSES this table, it does not redeclare it) and a smoke Lambda
-# that exercises the shared layer inside floci's real Docker runtime.
-#
-# These resources stay declared after verification as a re-runnable lab
-# fixture. Invoke ad-hoc:
-#   aws lambda invoke --endpoint-url http://localhost:4566 \
-#     --function-name smoke --payload '{"scenario":"all"}' out.json
-
-data "archive_file" "smoke_zip" {
-  type = "zip"
-  # _shared package at zip root (importable as `shared`) + smoke handler.
-  # NOTE: these source blocks are maintained BY HAND because the local dir
-  # is `_shared/` but the zip package must be `shared/` — archive_file's
-  # source_dir cannot rename. Adding a module to lambdas/_shared/ REQUIRES
-  # a matching source block here (and in every later function's zip); the
-  # smoke invoke fails loudly on a missing module (ImportError).
-  source {
-    content  = file("${path.module}/../lambdas/_shared/__init__.py")
-    filename = "shared/__init__.py"
-  }
-  source {
-    content  = file("${path.module}/../lambdas/_shared/status.py")
-    filename = "shared/status.py"
-  }
-  source {
-    content  = file("${path.module}/../lambdas/_shared/events.py")
-    filename = "shared/events.py"
-  }
-  source {
-    content  = file("${path.module}/../lambdas/_shared/errors.py")
-    filename = "shared/errors.py"
-  }
-  source {
-    content  = file("${path.module}/../lambdas/_shared/clients.py")
-    filename = "shared/clients.py"
-  }
-  source {
-    content  = file("${path.module}/../lambdas/smoke/handler.py")
-    filename = "handler.py"
-  }
-  output_path = "${path.module}/smoke.zip"
-}
-
-resource "aws_dynamodb_table" "video_metadata" {
-  name         = "video-metadata"
-  billing_mode = "PAY_PER_REQUEST"
-
-  hash_key = "videoId"
-
-  attribute {
-    name = "videoId"
-    type = "S"
-  }
-}
-
-# --- Runtime-scenario fixtures (retro action item: backstop deployed
-# epic-2 wiring in ci-local.sh stage 5) ------------------------------------
-
-# Capture queue: the video.processed rule targets it so the state-machine
-# smoke scenario can assert "exactly one event with the deterministic
-# eventId". The smoke Lambda drains it; backlog is test residue. Epic 3's
-# history queue is a SEPARATE consumer of the same event (AD-1 pattern).
-resource "aws_sqs_queue" "smoke_capture" {
-  name                       = "smoke-capture-queue"
-  visibility_timeout_seconds = 60
-}
-
-resource "aws_sqs_queue_policy" "smoke_capture" {
-  queue_url = aws_sqs_queue.smoke_capture.id
-
-  policy = jsonencode({
-    Version = "2012-10-17"
-    Statement = [{
-      Effect    = "Allow"
-      Principal = { Service = "events.amazonaws.com" }
-      Action    = "sqs:SendMessage"
-      Resource  = aws_sqs_queue.smoke_capture.arn
-      Condition = {
-        ArnEquals = {
-          "aws:SourceArn" = aws_cloudwatch_event_rule.video_processed_capture.arn
-        }
-      }
-    }]
-  })
-}
-
-resource "aws_cloudwatch_event_rule" "video_processed_capture" {
-  name           = "video-processed-to-smoke-capture"
-  event_bus_name = aws_cloudwatch_event_bus.video_bus.name
-
-  event_pattern = jsonencode({
-    detail-type = ["video.processed"]
-  })
-}
-
-resource "aws_cloudwatch_event_target" "smoke_capture_queue" {
-  rule           = aws_cloudwatch_event_rule.video_processed_capture.name
-  event_bus_name = aws_cloudwatch_event_bus.video_bus.name
-  arn            = aws_sqs_queue.smoke_capture.arn
-}
-
-resource "aws_iam_role" "smoke" {
-  name = "smoke-lambda-role"
-
-  assume_role_policy = jsonencode({
-    Version = "2012-10-17"
-    Statement = [{
-      Effect    = "Allow"
-      Principal = { Service = "lambda.amazonaws.com" }
-      Action    = "sts:AssumeRole"
-    }]
-  })
-}
-
-resource "aws_iam_role_policy" "smoke" {
-  name = "smoke-lambda-policy"
-  role = aws_iam_role.smoke.id
-
-  policy = jsonencode({
-    Version = "2012-10-17"
-    Statement = [
-      {
-        Effect = "Allow"
-        Action = [
-          "logs:CreateLogGroup",
-          "logs:CreateLogStream",
-          "logs:PutLogEvents",
-        ]
-        Resource = "*"
-      },
-      {
-        Effect = "Allow"
-        Action = [
-          "dynamodb:GetItem",
-          "dynamodb:PutItem",
-          "dynamodb:UpdateItem",
-          "dynamodb:DeleteItem",
-        ]
-        Resource = aws_dynamodb_table.video_metadata.arn
-      },
-      # Runtime scenarios: seed/cleanup fixture objects in both buckets.
-      {
-        Effect = "Allow"
-        Action = [
-          "s3:GetObject",
-          "s3:PutObject",
-          "s3:DeleteObject",
-        ]
-        Resource = [
-          "${aws_s3_bucket.video_uploads.arn}/*",
-          "${aws_s3_bucket.video_processed.arn}/*",
-        ]
-      },
-      # transcode scenario: invoke the deployed transcode zip.
-      {
-        Effect   = "Allow"
-        Action   = ["lambda:InvokeFunction"]
-        Resource = aws_lambda_function.transcode.arn
-      },
-      # state-machine scenario: drive the deployed state machine.
-      {
-        Effect = "Allow"
-        Action = [
-          "states:StartExecution",
-          "states:DescribeExecution",
-        ]
-        Resource = aws_sfn_state_machine.processing.arn
-      },
-      # trigger-leg scenario: publish video.uploaded on the bus.
-      {
-        Effect   = "Allow"
-        Action   = ["events:PutEvents"]
-        Resource = aws_cloudwatch_event_bus.video_bus.arn
-      },
-      # state-machine scenario: drain/read the capture queue.
-      {
-        Effect = "Allow"
-        Action = [
-          "sqs:ReceiveMessage",
-          "sqs:DeleteMessage",
-          "sqs:GetQueueAttributes",
-        ]
-        Resource = aws_sqs_queue.smoke_capture.arn
-      },
-    ]
-  })
-}
-
-resource "aws_lambda_function" "smoke" {
-  function_name    = "smoke"
-  role             = aws_iam_role.smoke.arn
-  runtime          = "python3.11"
-  handler          = "handler.lambda_handler"
-  filename         = data.archive_file.smoke_zip.output_path
-  source_code_hash = data.archive_file.smoke_zip.output_base64sha256
-  # Runtime scenarios poll the state machine (up to ~90s trigger leg).
-  timeout = 180
-
-  environment {
-    variables = {
-      TABLE_NAME        = aws_dynamodb_table.video_metadata.name
-      UPLOADS_BUCKET    = aws_s3_bucket.video_uploads.bucket
-      PROCESSED_BUCKET  = aws_s3_bucket.video_processed.bucket
-      STATE_MACHINE_ARN = aws_sfn_state_machine.processing.arn
-      EVENT_BUS_NAME    = aws_cloudwatch_event_bus.video_bus.name
-      CAPTURE_QUEUE_URL = aws_sqs_queue.smoke_capture.url
-      AWS_ENDPOINT_URL  = local.lambda_endpoint_url
-    }
-  }
-}
-
-output "smoke_function" {
-  value = aws_lambda_function.smoke.function_name
-}
diff --git a/tests/integration/conftest.py b/tests/integration/conftest.py
new file mode 100644
index 0000000..99dc44c
--- /dev/null
+++ b/tests/integration/conftest.py
@@ -0,0 +1,348 @@
+"""Integration test suite — shared fixtures and helpers.
+
+Drives the DEPLOYED stack through real API Gateway calls and real AWS-API
+side-effect reads (S3 / DynamoDB / SQS / EventBridge / Step Functions), per
+_bmad-output/test-artifacts/integration-test-plan.md. Requires a live,
+applied stack on floci (localhost:4566).
+
+Design decisions honored here (plan §2):
+- D5: gateway base URL from GATEWAY_BASE_URL env, fallback `terraform output`.
+- D6: binary fixture generated in-process (all 256 byte values, deterministic).
+- Capture-queue hygiene (plan §4): each journey test drains the queue at start
+  and asserts only on messages matching its own videoId/eventId.
+"""
+
+import json
+import os
+import subprocess
+import time
+import uuid
+from datetime import datetime, timezone
+from pathlib import Path
+
+import boto3
+import pytest
+import requests
+from boto3.dynamodb.conditions import Attr
+
+ENDPOINT_URL = "http://localhost:4566"
+REGION = "us-east-1"
+CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}
+
+# Terraform-set resource names (config-not-code; fixed in terraform/*.tf).
+METADATA_TABLE = "video-metadata"
+UPLOADS_BUCKET = "video-uploads"
+PROCESSED_BUCKET = "video-processed"
+EVENT_BUS = "video-bus"
+HISTORY_TABLE = "status-history"
+CAPTURE_QUEUE = "smoke-capture-queue"
+STATE_MACHINE_NAME = "processing-state-machine"
+TRANSCODE_FUNCTION = "transcode"
+
+# Frozen wire contract (lambdas/_shared/events.py:24,38-40). Re-derived here
+# rather than imported so the suite stays independent of the zip package layout.
+EVENT_ID_NAMESPACE = uuid.UUID("99881bbf-05eb-5ec6-8f3a-490d7496e518")
+SCHEMA_VERSION = "1"
+EVENT_UPLOADED = "video.uploaded"
+EVENT_PROCESSED = "video.processed"
+
+# floci cold Lambda containers are slow — generous end-to-end timeout (plan §4).
+JOURNEY_TIMEOUT = 180
+
+REPO_ROOT = Path(__file__).resolve().parents[2]
+
+
+def event_id(video_id, status):
+    """Deterministic eventId for (videoId, status) — mirrors shared layer."""
+    return str(uuid.uuid5(EVENT_ID_NAMESPACE, f"{video_id}:{status}"))
+
+
+def _now_iso():
+    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
+        "+00:00", "Z")
+
+
+def poll_until(fn, timeout=JOURNEY_TIMEOUT, interval=2):
+    """Call fn() until it returns a truthy value; return it. Raise on timeout."""
+    deadline = time.time() + timeout
+    last = None
+    while time.time() < deadline:
+        last = fn()
+        if last:
+            return last
+        time.sleep(interval)
+    raise TimeoutError(
+        f"condition not met within {timeout}s (last={last!r})")
+
+
+class Stack:
+    """Bundles boto3 clients + the operations the integration tests share."""
+
+    def __init__(self):
+        self.s3 = boto3.client(
+            "s3", endpoint_url=ENDPOINT_URL, region_name=REGION, **CREDS)
+        self.dynamodb = boto3.resource(
+            "dynamodb", endpoint_url=ENDPOINT_URL, region_name=REGION, **CREDS)
+        self.sqs = boto3.client(
+            "sqs", endpoint_url=ENDPOINT_URL, region_name=REGION, **CREDS)
+        self.events = boto3.client(
+            "events", endpoint_url=ENDPOINT_URL, region_name=REGION, **CREDS)
+        self.sfn = boto3.client(
+            "stepfunctions", endpoint_url=ENDPOINT_URL, region_name=REGION,
+            **CREDS)
+        self._capture_queue_url = None
+        self._state_machine_arn = None
+
+    # --- Resource lookups ---------------------------------------------------
+
+    @property
+    def capture_queue_url(self):
+        if self._capture_queue_url is None:
+            self._capture_queue_url = self.sqs.get_queue_url(
+                QueueName=CAPTURE_QUEUE)["QueueUrl"]
+        return self._capture_queue_url
+
+    @property
+    def state_machine_arn(self):
+        if self._state_machine_arn is None:
+            resp = self.sfn.list_state_machines()
+            for sm in resp["stateMachines"]:
+                if sm["name"] == STATE_MACHINE_NAME:
+                    self._state_machine_arn = sm["stateMachineArn"]
+                    break
+            else:
+                raise RuntimeError(
+                    f"state machine {STATE_MACHINE_NAME} not found")
+        return self._state_machine_arn
+
+    # --- Gateway upload -----------------------------------------------------
+
+    def upload(self, gateway_base_url, body, title="Integration Fixture",
+               filename="fixture.bin"):
+        """POST multipart to the gateway upload route; return the response."""
+        return requests.post(
+            f"{gateway_base_url}/videos/upload",
+            files={"file": (filename, body, "application/octet-stream")},
+            data={"title": title},
+            timeout=60)
+
+    # --- Metadata table -----------------------------------------------------
+
+    def get_record(self, video_id):
+        resp = self.dynamodb.Table(METADATA_TABLE).get_item(
+            Key={"videoId": video_id})
+        return resp.get("Item")
+
+    def wait_status(self, video_id, status, timeout=JOURNEY_TIMEOUT):
+        return poll_until(
+            lambda: (lambda r: r if r and r.get("status") == status else None)(
+                self.get_record(video_id)),
+            timeout=timeout)
+
+    # --- Capture queue (video.processed observation point) ------------------
+
+    def _receive_capture(self, max_messages=100):
+        envelopes = []
+        while len(envelopes) < max_messages:
+            resp = self.sqs.receive_message(
+                QueueUrl=self.capture_queue_url, MaxNumberOfMessages=10,
+                WaitTimeSeconds=0)
+            messages = resp.get("Messages") or []
+            if not messages:
+                break
+            for msg in messages:
+                try:
+                    envelopes.append(json.loads(msg["Body"]))
+                except ValueError:
+                    pass
+                self.sqs.delete_message(
+                    QueueUrl=self.capture_queue_url,
+                    ReceiptHandle=msg["ReceiptHandle"])
+        return envelopes
+
+    def drain_capture_queue(self):
+        while self._receive_capture():
+            pass
+
+    @staticmethod
+    def _detail_of(envelope):
+        detail = envelope.get("detail")
+        if isinstance(detail, str):
+            try:
+                detail = json.loads(detail)
+            except ValueError:
+                return None
+        return detail if isinstance(detail, dict) else None
+
+    def collect_processed_events(self, video_id, timeout=60):
+        """video.processed details for this videoId arriving within the window."""
+        found = []
+        deadline = time.time() + timeout
+        while time.time() < deadline:
+            for envelope in self._receive_capture():
+                detail = self._detail_of(envelope)
+                if detail and detail.get("videoId") == video_id:
+                    found.append(detail)
+            if found:
+                # Give redeliveries/duplicates a moment to show up, then stop.
+                time.sleep(3)
+                for envelope in self._receive_capture():
+                    detail = self._detail_of(envelope)
+                    if detail and detail.get("videoId") == video_id:
+                        found.append(detail)
+                break
+            time.sleep(1)
+        return found
+
+    # --- Event publishing ---------------------------------------------------
+
+    def publish(self, detail_type, detail_payload):
+        put_resp = self.events.put_events(Entries=[{
+            "Source": "integration-test",
+            "DetailType": detail_type,
+            "Detail": json.dumps(detail_payload),
+            "EventBusName": EVENT_BUS,
+        }])
+        assert not put_resp.get("FailedEntryCount"), (
+            f"put_events rejected: {put_resp}")
+
+    @staticmethod
+    def uploaded_payload(video_id, bucket, key):
+        detail = {"videoId": video_id, "status": "UPLOADED",
+                  "bucket": bucket, "key": key}
+        envelope = {"eventId": event_id(video_id, "UPLOADED"),
+                    "schemaVersion": SCHEMA_VERSION, "detail": detail}
+        return {**envelope, **envelope["detail"]}
+
+    @staticmethod
+    def processed_payload(video_id, bucket, original_key, processed_key,
+                          eid=None):
+        detail = {"videoId": video_id, "status": "PROCESSED", "bucket": bucket,
+                  "originalKey": original_key, "processedKey": processed_key}
+        envelope = {"eventId": eid or event_id(video_id, "PROCESSED"),
+                    "schemaVersion": SCHEMA_VERSION, "detail": detail}
+        return {**envelope, **envelope["detail"]}
+
+    # --- Seeding (T5/T6/T7: isolate legs from the upload path) --------------
+
+    def seed_video(self, video_id, body, filename="fixture.mp4"):
+        """Fixture object in video-uploads + an UPLOADED metadata record."""
+        key = f"{video_id}/{filename}"
+        self.s3.put_object(
+            Bucket=UPLOADS_BUCKET, Key=key, Body=body, ContentType="video/mp4")
+        now = _now_iso()
+        self.dynamodb.Table(METADATA_TABLE).put_item(Item={
+            "videoId": video_id, "title": filename, "status": "UPLOADED",
+            "bucket": UPLOADS_BUCKET, "originalKey": key,
+            "createdAt": now, "updatedAt": now,
+            "contentType": "video/mp4", "sizeBytes": len(body),
+        })
+        return key
+
+    # --- Step Functions -----------------------------------------------------
+
+    def start_execution(self, name, asl_input):
+        return self.sfn.start_execution(
+            stateMachineArn=self.state_machine_arn,
+            name=name, input=json.dumps(asl_input))
+
+    def wait_execution(self, execution_arn, timeout=JOURNEY_TIMEOUT):
+        deadline = time.time() + timeout
+        while time.time() < deadline:
+            desc = self.sfn.describe_execution(executionArn=execution_arn)
+            if desc["status"] in ("SUCCEEDED", "FAILED", "TIMED_OUT",
+                                  "ABORTED"):
+                return desc
+            time.sleep(2)
+        raise TimeoutError(
+            f"execution {execution_arn} still running after {timeout}s")
+
+    def find_execution_by_name(self, name):
+        next_token = None
+        while True:
+            kwargs = {"stateMachineArn": self.state_machine_arn}
+            if next_token:
+                kwargs["nextToken"] = next_token
+            resp = self.sfn.list_executions(**kwargs)
+            for ex in resp["executions"]:
+                if ex["name"] == name:
+                    return ex
+            next_token = resp.get("nextToken")
+            if not next_token:
+                return None
+
+    # --- Transcode (ad-hoc invoke through floci's Lambda REST API) ----------
+
+    def invoke_transcode(self, payload):
+        resp = requests.post(
+            f"{ENDPOINT_URL}/2015-03-31/functions/{TRANSCODE_FUNCTION}"
+            "/invocations",
+            json=payload, timeout=60)
+        body = resp.json()
+        # floci may wrap the result as {Payload, StatusCode}; unwrap if so.
+        if isinstance(body, dict) and "Payload" in body:
+            body = body["Payload"]
+            if isinstance(body, str):
+                body = json.loads(body)
+        return body
+
+    # --- status-history -----------------------------------------------------
+
+    def history_entries(self, video_id):
+        resp = self.dynamodb.Table(HISTORY_TABLE).scan(
+            FilterExpression=Attr("videoId").eq(video_id))
+        return resp.get("Items", [])
+
+    # --- Cleanup ------------------------------------------------------------
+
+    def cleanup_video(self, video_id):
+        try:
+            self.dynamodb.Table(METADATA_TABLE).delete_item(
+                Key={"videoId": video_id})
+        except Exception:  # noqa: BLE001 - cleanup must never fail the run
+            pass
+        try:
+            table = self.dynamodb.Table(HISTORY_TABLE)
+            for item in self.history_entries(video_id):
+                table.delete_item(Key={"eventId": item["eventId"]})
+        except Exception:  # noqa: BLE001
+            pass
+        for bucket, prefix in (
+                (UPLOADS_BUCKET, f"{video_id}/"),
+                (PROCESSED_BUCKET, f"processed/{video_id}/")):
+            try:
+                resp = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
+                for obj in resp.get("Contents", []):
+                    self.s3.delete_object(Bucket=bucket, Key=obj["Key"])
+            except Exception:  # noqa: BLE001
+                pass
+
+
+@pytest.fixture(scope="session")
+def stack():
+    return Stack()
+
+
+@pytest.fixture(scope="session")
+def gateway_base_url():
+    url = os.environ.get("GATEWAY_BASE_URL")
+    if url:
+        return url.rstrip("/")
+    out = subprocess.run(
+        ["terraform", "output", "-raw", "gateway_base_url"],
+        cwd=REPO_ROOT / "terraform",
+        capture_output=True, text=True, check=True)
+    return out.stdout.strip().rstrip("/")
+
+
+@pytest.fixture(scope="session")
+def binary_payload():
+    # D6: all 256 byte values, deterministic, no fixture file.
+    return bytes(range(256)) * 4
+
+
+@pytest.fixture()
+def video_id(stack):
+    vid = str(uuid.uuid4())
+    yield vid
+    stack.cleanup_video(vid)
diff --git a/tests/integration/test_history_leg.py b/tests/integration/test_history_leg.py
new file mode 100644
index 0000000..ba00acd
--- /dev/null
+++ b/tests/integration/test_history_leg.py
@@ -0,0 +1,69 @@
+"""T8–T10 — history leg (Story 3.1)."""
+
+import time
+import uuid
+
+from conftest import (EVENT_PROCESSED, PROCESSED_BUCKET, event_id,
+                      poll_until)
+
+
+def test_t8_history_entry_written(stack, gateway_base_url, binary_payload):
+    """Upload via gateway -> exactly one status-history entry
+    {eventId, videoId, status: PROCESSED, timestamp} with the deterministic
+    eventId (FR-14)."""
+    resp = stack.upload(gateway_base_url, binary_payload)
+    assert resp.status_code == 200, resp.text
+    vid = resp.json()["videoId"]
+    try:
+        expected_event_id = event_id(vid, "PROCESSED")
+        entries = poll_until(
+            lambda: stack.history_entries(vid) or None, timeout=180)
+        # Give duplicates a moment to show up, then assert the final state.
+        time.sleep(5)
+        entries = stack.history_entries(vid)
+        assert len(entries) == 1, f"expected 1 history entry, got {entries}"
+        entry = entries[0]
+        assert entry["eventId"] == expected_event_id, entry
+        assert entry["videoId"] == vid, entry
+        assert entry["status"] == "PROCESSED", entry
+        assert entry.get("timestamp"), entry
+    finally:
+        stack.cleanup_video(vid)
+
+
+def test_t9_duplicate_processed_event_deduped(
+        stack, gateway_base_url, binary_payload):
+    """Republish video.processed -> still exactly one entry for that eventId
+    (NFR-1)."""
+    resp = stack.upload(gateway_base_url, binary_payload)
+    assert resp.status_code == 200, resp.text
+    vid = resp.json()["videoId"]
+    try:
+        poll_until(lambda: stack.history_entries(vid) or None, timeout=180)
+        record = stack.get_record(vid)
+
+        # Republish with the SAME deterministic eventId the publisher used.
+        stack.publish(EVENT_PROCESSED, stack.processed_payload(
+            vid, PROCESSED_BUCKET, record["originalKey"],
+            record.get("processedKey", f"processed/{vid}/fixture.bin")))
+
+        time.sleep(15)
+        entries = stack.history_entries(vid)
+        assert len(entries) == 1, (
+            f"duplicate processed event created a second entry: {entries}")
+    finally:
+        stack.cleanup_video(vid)
+
+
+def test_t10_unknown_video_id_dropped(stack):
+    """Publish video.processed with fabricated eventId + unknown videoId ->
+    no table entry, message acked (FR-15)."""
+    unknown_vid = f"it-unknown-{uuid.uuid4()}"
+    stack.publish(EVENT_PROCESSED, stack.processed_payload(
+        unknown_vid, PROCESSED_BUCKET, f"{unknown_vid}/fixture.mp4",
+        f"processed/{unknown_vid}/fixture.mp4",
+        eid=f"it-fabricated-{uuid.uuid4()}"))
+
+    time.sleep(15)
+    assert stack.history_entries(unknown_vid) == [], (
+        "poison event produced a history entry")
diff --git a/tests/integration/test_processing_journey.py b/tests/integration/test_processing_journey.py
new file mode 100644
index 0000000..5c3d3a8
--- /dev/null
+++ b/tests/integration/test_processing_journey.py
@@ -0,0 +1,88 @@
+"""T3–T4 — end-to-end auto-processing and redelivery no-op (Stories 2.2, 2.3)."""
+
+import time
+
+from conftest import (EVENT_UPLOADED, PROCESSED_BUCKET, UPLOADS_BUCKET,
+                      event_id)
+
+
+def test_t3_end_to_end_auto_processing(
+        stack, gateway_base_url, binary_payload):
+    """Upload via gateway -> record walks to PROCESSED -> processed object
+    exists -> exactly one video.processed with the deterministic eventId ->
+    SFN execution eb-{uploaded-eventId} exists."""
+    stack.drain_capture_queue()
+    resp = stack.upload(gateway_base_url, binary_payload)
+    assert resp.status_code == 200, resp.text
+    vid = resp.json()["videoId"]
+    try:
+        record = stack.wait_status(vid, "PROCESSED")
+        assert record.get("processedKey"), record
+
+        obj = stack.s3.get_object(
+            Bucket=PROCESSED_BUCKET, Key=record["processedKey"])
+        assert obj["Body"].read() == binary_payload, (
+            "processed object is not byte-identical to the fixture")
+
+        events = stack.collect_processed_events(vid)
+        assert len(events) == 1, (
+            f"expected exactly 1 video.processed, got {len(events)}")
+        assert events[0].get("eventId") == event_id(vid, "PROCESSED"), (
+            f"eventId mismatch: {events[0].get('eventId')}")
+
+        execution = stack.find_execution_by_name(
+            f"eb-{event_id(vid, 'UPLOADED')}")
+        assert execution is not None, (
+            "no SFN execution named eb-{uploaded-eventId}")
+    finally:
+        stack.cleanup_video(vid)
+
+
+def test_t4_redelivered_uploaded_event_is_no_op(
+        stack, gateway_base_url, binary_payload):
+    """Republish the same video.uploaded -> still exactly one execution,
+    status still PROCESSED, no second processed event (FR-9)."""
+    stack.drain_capture_queue()
+    resp = stack.upload(gateway_base_url, binary_payload)
+    assert resp.status_code == 200, resp.text
+    vid = resp.json()["videoId"]
+    try:
+        stack.wait_status(vid, "PROCESSED")
+        # Let the first processed event land and be counted, then drain it
+        # so the republish window counts only new arrivals.
+        first = stack.collect_processed_events(vid)
+        assert len(first) == 1, (
+            f"expected 1 video.processed before republish, got {len(first)}")
+
+        key = stack.get_record(vid)["originalKey"]
+        stack.publish(
+            EVENT_UPLOADED,
+            stack.uploaded_payload(vid, UPLOADS_BUCKET, key))
+
+        # The shim acks ExecutionAlreadyExists; nothing new may happen.
+        # Wait out the async leg, then assert the steady state.
+        time.sleep(15)
+        assert stack.get_record(vid)["status"] == "PROCESSED"
+        assert stack.collect_processed_events(vid, timeout=10) == [], (
+            "republish produced a second processed event")
+        executions = [
+            ex for ex in _all_executions(stack)
+            if ex["name"] == f"eb-{event_id(vid, 'UPLOADED')}"]
+        assert len(executions) == 1, (
+            f"expected exactly 1 execution, got {len(executions)}")
+    finally:
+        stack.cleanup_video(vid)
+
+
+def _all_executions(stack):
+    executions = []
+    next_token = None
+    while True:
+        kwargs = {"stateMachineArn": stack.state_machine_arn}
+        if next_token:
+            kwargs["nextToken"] = next_token
+        resp = stack.sfn.list_executions(**kwargs)
+        executions.extend(resp["executions"])
+        next_token = resp.get("nextToken")
+        if not next_token:
+            return executions
diff --git a/tests/integration/test_state_machine.py b/tests/integration/test_state_machine.py
new file mode 100644
index 0000000..92f850d
--- /dev/null
+++ b/tests/integration/test_state_machine.py
@@ -0,0 +1,59 @@
+"""T5–T6 — ad-hoc state machine runs (Story 2.2)."""
+
+import uuid
+
+from conftest import PROCESSED_BUCKET, UPLOADS_BUCKET, event_id
+
+
+def test_t5_ad_hoc_start_execution(stack, binary_payload, video_id):
+    """Seed fixture object + UPLOADED record -> StartExecution with the
+    domain payload -> PROCESSED with processedKey -> processed object exists
+    -> exactly one processed event."""
+    stack.drain_capture_queue()
+    key = stack.seed_video(video_id, binary_payload)
+    processed_key = f"processed/{video_id}/fixture.mp4"
+
+    start = stack.start_execution(f"it-t5-{uuid.uuid4()}", {
+        "videoId": video_id, "status": "UPLOADED",
+        "bucket": UPLOADS_BUCKET, "key": key})
+    desc = stack.wait_execution(start["executionArn"])
+    assert desc["status"] == "SUCCEEDED", desc
+
+    record = stack.get_record(video_id)
+    assert record["status"] == "PROCESSED", record
+    assert record.get("processedKey") == processed_key, record
+
+    obj = stack.s3.get_object(Bucket=PROCESSED_BUCKET, Key=processed_key)
+    assert obj["Body"].read() == binary_payload, (
+        "processed object is not byte-identical to the fixture")
+
+    events = stack.collect_processed_events(video_id)
+    assert len(events) == 1, (
+        f"expected exactly 1 video.processed, got {len(events)}")
+    assert events[0].get("eventId") == event_id(video_id, "PROCESSED")
+
+
+def test_t6_rerun_fails_without_regression(stack, binary_payload, video_id):
+    """StartExecution again (fresh name, record already PROCESSED) ->
+    execution fails at MarkProcessing -> status stays PROCESSED, no second
+    event (FR-11 via ASL)."""
+    stack.drain_capture_queue()
+    key = stack.seed_video(video_id, binary_payload)
+
+    first = stack.start_execution(f"it-t6-{uuid.uuid4()}", {
+        "videoId": video_id, "status": "UPLOADED",
+        "bucket": UPLOADS_BUCKET, "key": key})
+    desc = stack.wait_execution(first["executionArn"])
+    assert desc["status"] == "SUCCEEDED", desc
+    first_events = stack.collect_processed_events(video_id)
+    assert len(first_events) == 1, first_events
+
+    rerun = stack.start_execution(f"it-t6-{uuid.uuid4()}", {
+        "videoId": video_id, "status": "UPLOADED",
+        "bucket": UPLOADS_BUCKET, "key": key})
+    rerun_desc = stack.wait_execution(rerun["executionArn"])
+    assert rerun_desc["status"] == "FAILED", rerun_desc
+
+    assert stack.get_record(video_id)["status"] == "PROCESSED"
+    assert stack.collect_processed_events(video_id, timeout=10) == [], (
+        "rerun published a second processed event")
diff --git a/tests/integration/test_transcode.py b/tests/integration/test_transcode.py
new file mode 100644
index 0000000..c83eae5
--- /dev/null
+++ b/tests/integration/test_transcode.py
@@ -0,0 +1,27 @@
+"""T7 — ad-hoc transcode invoke through floci's Lambda REST API (Story 2.1)."""
+
+from conftest import PROCESSED_BUCKET
+
+
+def test_t7_ad_hoc_transcode_invoke(stack, binary_payload, video_id):
+    """Seed fixture object + record -> invoke the deployed transcode zip with
+    {videoId, originalKey} -> processed object exists -> record still
+    UPLOADED -> no event published (FR-6, AD-4)."""
+    stack.drain_capture_queue()
+    key = stack.seed_video(video_id, binary_payload)
+    processed_key = f"processed/{video_id}/fixture.mp4"
+
+    result = stack.invoke_transcode(
+        {"videoId": video_id, "originalKey": key})
+    assert result.get("videoId") == video_id, result
+    assert result.get("processedKey") == processed_key, result
+    assert result.get("sizeBytes") == len(binary_payload), result
+
+    obj = stack.s3.get_object(Bucket=PROCESSED_BUCKET, Key=processed_key)
+    assert obj["Body"].read() == binary_payload, (
+        "processed object is not byte-identical to the fixture")
+
+    # Pure worker (AD-4): no status writes, no events.
+    assert stack.get_record(video_id)["status"] == "UPLOADED"
+    assert stack.collect_processed_events(video_id, timeout=10) == [], (
+        "transcode published an event")
diff --git a/tests/integration/test_upload_api.py b/tests/integration/test_upload_api.py
new file mode 100644
index 0000000..848456b
--- /dev/null
+++ b/tests/integration/test_upload_api.py
@@ -0,0 +1,50 @@
+"""T1–T2 — upload journey through the gateway (Story 1.3, mirrors Bruno)."""
+
+import requests
+
+from conftest import UPLOADS_BUCKET
+
+
+def test_t1_happy_path_binary_round_trip(
+        stack, gateway_base_url, binary_payload):
+    """POST multipart (binary fixture + title) -> 200 videoId; object in
+    video-uploads byte-identical to the upload (the epic-1 F1 gap); record
+    UPLOADED with timestamps."""
+    resp = stack.upload(gateway_base_url, binary_payload)
+    assert resp.status_code == 200, resp.text
+    body = resp.json()
+    assert "videoId" in body, body
+    vid = body["videoId"]
+    try:
+        # Object exists under a key containing the videoId, byte-identical.
+        listing = stack.s3.list_objects_v2(Bucket=UPLOADS_BUCKET,
+                                           Prefix=f"{vid}/")
+        contents = listing.get("Contents", [])
+        assert len(contents) == 1, f"expected 1 object, got {contents}"
+        obj = stack.s3.get_object(Bucket=UPLOADS_BUCKET,
+                                  Key=contents[0]["Key"])
+        assert obj["Body"].read() == binary_payload, (
+            "uploaded object is not byte-identical to the fixture")
+
+        # Metadata record UPLOADED with timestamps.
+        record = stack.get_record(vid)
+        assert record is not None, "video-metadata record missing"
+        assert record["status"] == "UPLOADED", record
+        assert record.get("createdAt"), record
+        assert record.get("updatedAt"), record
+    finally:
+        stack.cleanup_video(vid)
+
+
+def test_t2_malformed_missing_file_part(stack, gateway_base_url):
+    """Multipart without a file part -> 400 {"error": ...} passed through
+    unchanged (NFR-3, FR-21)."""
+    # files={(None, value)} sends a multipart form field with no file part.
+    resp = requests.post(
+        f"{gateway_base_url}/videos/upload",
+        files={"title": (None, "no file here")},
+        timeout=60)
+    assert resp.status_code == 400, resp.text
+    body = resp.json()
+    assert "error" in body, body
+    assert isinstance(body["error"], str) and body["error"], body


Do not invoke any skill. Return only the review result.
