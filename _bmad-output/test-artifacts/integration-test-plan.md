# Integration Test Suite — Solution & Smoke Retirement Plan

Date: 2026-08-22 · Branch: `bmad/integration-test-plan` · Status: proposal
Roundtable: Winston (architect), Murat (test architect), Amelia (dev), John (PM scope)
Rebased 2026-08-22 after the floci 1.7.0 bump merged (PR #18): D6 flipped to a
binary fixture, §7 text-fixture limitation retired.

## 1. Problem

CI stage 5 gates on the `smoke` Lambda, invoked through floci's **Lambda API**
(`POST /2015-03-31/functions/smoke/invocations`). That path bypasses API
Gateway entirely, so the gateway route, request/response mapping, and the
`_aws/execute-api` data plane are never exercised by CI. The Bruno collection
covers the gateway path but is manual-only. We replace the smoke gate with an
automated pytest suite that drives the system through real API calls and real
AWS-API side-effect reads, then retire the smoke Lambda.

## 2. Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **pytest + requests** (user choice), run via `uv run --with` | pytest already in CI toolchain (stage 3); no new framework, no lockfile |
| D2 | Tests live in **`tests/integration/`** at repo root, not under `lambdas/` | Unit gate `pytest lambdas/` stays pure; integration needs a live stack, unit must not |
| D3 | **Zero new infrastructure.** Observation reuses what exists | Capture queue (`smoke-capture-queue`, rule on `video.processed`) is the event-count observation point; SFN execution name `eb-{eventId}` proves the `video.uploaded` leg; `status-history` table proves the history leg |
| D4 | `smoke.tf` → **`integration.tf`**: strip smoke function/role, keep capture queue + rule at identical resource addresses | File rename is invisible to Terraform state; no migration, no recreate |
| D5 | Gateway base URL from **`terraform output -raw gateway_base_url`** (env override `GATEWAY_BASE_URL` honored) | Output already exists (`upload.tf`); kills the Bruno-style `REPLACE_WITH_API_ID` dance |
| D6 | **Binary fixture, generated in conftest** (`bytes(range(256)) * 4` — all byte values, deterministic, no fixture file) | floci 1.7.0 delivers binary multipart base64 + handler decodes (PR #18) — the upload test must exercise real binary bytes or it re-creates epic-1's F1 verification gap |
| D7 | Bruno stays as the **manual/dev** tool; the pytest suite becomes the CI source of truth | User scope; Bruno's two requests map 1:1 to tests T1/T2 |
| D8 | **Two-step retirement**: swap the gate first, delete smoke after one green CI run | Cold floci container starts make polling suites flaky-risky; swap with the safety net still up, then delete |

## 3. Coverage matrix — every shipped feature

| Story | Feature | Smoke scenario today | Replacement test |
|-------|---------|---------------------|------------------|
| 1.2 | Shared access layer (transitions, idempotency, envelope) | create, create-idempotent, transition-legal, transition-illegal, reassert, envelope | **None — retired.** Already covered by the 27 shared-layer unit tests; smoke's copies were runtime duplicates |
| 1.3 | Upload journey through the gateway | *(not covered — gateway bypassed)* | T1, T2 |
| 2.1 | Transcode worker (pure S3 in/out) | transcode | T5 |
| 2.2 | State machine + event publisher | state-machine | T6, T7 |
| 2.3 | Trigger leg (rule → queue → shim → SFN) | trigger-leg | T3, T4 |
| 3.1 | History consumer | *(deferred-work item, never built)* | T8, T9, T10 |

## 4. Test list

All tests use a fresh `uuid4` videoId; cleanup deletes their records/objects.
Polling helper with generous timeout (floci cold Lambda containers are slow —
180 s for end-to-end journeys).

**`tests/integration/test_upload_api.py`** (Story 1.3, mirrors Bruno)
- **T1 happy path** — POST multipart (binary fixture, all 256 byte values, +
  title) to `{gateway}/videos/upload` → 200 with `videoId`; object exists in
  `video-uploads` under key containing videoId **byte-identical to the upload**
  (round-trip check — the epic-1 F1 gap); `video-metadata` record is UPLOADED
  with timestamps.
- **T2 malformed** — multipart without file part → 400 with `{"error": ...}`
  passed through unchanged (NFR-3, FR-21).

**`tests/integration/test_processing_journey.py`** (Stories 2.2, 2.3)
- **T3 end-to-end auto-processing** — upload via gateway → poll record to
  PROCESSED → processed object in `video-processed` → capture queue holds
  exactly one `video.processed` message with deterministic eventId
  (UUID5(videoId, PROCESSED)) → SFN execution named `eb-{uploaded-eventId}`
  exists. Proves the full path: handler → rule → queue → shim → SFN →
  transcode → publisher.
- **T4 redelivered uploaded event is a no-op** — republish the same
  `video.uploaded` via boto3 `events:put_events` → wait → still exactly one
  execution, status still PROCESSED, no second processed event
  (`ExecutionAlreadyExists` acked, FR-9).

**`tests/integration/test_state_machine.py`** (Story 2.2)
- **T5 ad-hoc StartExecution** — seed fixture object + UPLOADED record via
  boto3 → StartExecution with domain payload `{videoId, status, bucket, key}`
  → poll to PROCESSED with `processedKey` set → processed object exists →
  exactly one processed event.
- **T6 rerun fails without regression** — StartExecution again (fresh name,
  record already PROCESSED) → execution fails at MarkProcessing → status stays
  PROCESSED, no second event (FR-11 via ASL).

**`tests/integration/test_transcode.py`** (Story 2.1)
- **T7 ad-hoc transcode invoke** — seed fixture object + record → invoke
  deployed `transcode` zip via floci Lambda REST with
  `{videoId, originalKey}` → processed object exists → record still UPLOADED →
  no event published (FR-6, AD-4). Backstops deployed zip/handler/env wiring.

**`tests/integration/test_history_leg.py`** (Story 3.1)
- **T8 history entry written** — upload via gateway → poll `status-history` →
  exactly one entry `{eventId, videoId, status: PROCESSED, timestamp}` with
  the deterministic eventId (FR-14).
- **T9 duplicate processed event deduped** — republish `video.processed` via
  `put_events` → wait → still exactly one entry for that eventId (NFR-1).
- **T10 unknown videoId dropped** — publish `video.processed` with fabricated
  eventId + unknown videoId → wait → no table entry, message acked (FR-15).

**`conftest.py`** (single file, ~80 lines): boto3 clients pinned to
`http://localhost:4566` with dummy creds; `gateway_base_url` from
`terraform output -raw` (or `GATEWAY_BASE_URL` env); fixed resource names
(`video-metadata`, `video-uploads`, `video-processed`, `video-bus`,
`status-history` — Terraform-set constants); `poll_until(fn, timeout)`;
capture-queue drain scoped to the test's own eventId; per-test cleanup.

Capture-queue hygiene: CI runs serially; each journey test drains the queue at
start and asserts only on messages matching its own eventId.

## 5. CI rewiring

Stage 5 in `.github/workflows/ci.yml` and `scripts/ci-local.sh` — same
docker/terraform preamble, invoke line replaced:

```bash
docker compose up -d --wait
(cd terraform && terraform init -input=false >/dev/null && terraform apply -auto-approve)
GATEWAY_BASE_URL="$(cd terraform && terraform output -raw gateway_base_url)" \
  uv run --with 'pytest>=8.0' --with requests --with boto3 \
  pytest tests/integration/ -q
```

`terraform destroy` with `if: always()` unchanged. `docs/ci.md` stage table
updated accordingly.

## 6. Smoke retirement plan

**Step 1 — swap the gate** (one PR):
1. Add `tests/integration/` (conftest + 5 files, T1–T10).
2. `mv terraform/smoke.tf terraform/integration.tf`; delete the smoke
   function, role, and archive resources; keep `aws_sqs_queue.smoke_capture`,
   its policy, rule, and target at identical addresses (rename inline comments).
3. Rewire stage 5 (both `ci.yml` and `ci-local.sh`) per §5.
4. Update `docs/ci.md`, README smoke references.

**Gate: one green CI run on the PR.**

**Step 2 — delete smoke** (small follow-up PR):
1. Delete `lambdas/smoke/` and its tests.
2. Remove any remaining smoke references (AGENTS.md pitfalls, README, docs).
3. Close deferred-work items: history-leg smoke scenario (superseded by T8);
   transcode/state-machine/trigger-leg smoke-scenario items (already built in
   smoke, now superseded by T3–T7 — mark superseded, not done).

## 7. Known limitations

- **Polling, not event-driven waits** — floci has no bus-subscribe API for
  external processes; queues and tables are the observable surfaces.
- **No schemathesis/fuzz layer** — needs an OpenAPI spec we don't maintain;
  revisit if one is ever exported.
- `updatedAt` precision drift (ASL ms vs shared-layer seconds, deferred-work
  item) is out of scope; no test asserts cross-writer timestamp format.

## 8. Out of scope

Bruno collection changes (stays manual tool), contract tests, load tests,
real-AWS deployment. (floci version bump was listed here; it merged on main
as PR #18 before this plan was implemented.)
