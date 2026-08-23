---
workflowType: 'testarch-atdd'
stepsCompleted: ['step-01-preflight-and-context', 'step-02-generation-mode', 'step-03-test-strategy', 'step-04-generate-tests', 'step-04c-aggregate', 'step-05-validate-and-complete']
lastStep: 'step-05-validate-and-complete'
lastSaved: '2026-08-23'
storyId: '3.2'
storyKey: '3-2-history-query-through-the-gateway'
storyFile: '_bmad-output/implementation-artifacts/spec-3-2-history-query-through-the-gateway.md'
atddChecklistPath: '_bmad-output/test-artifacts/atdd-checklist-3-2-history-query-through-the-gateway.md'
generatedTestFiles:
  - 'lambdas/history_query/tests/conftest.py'
  - 'lambdas/history_query/tests/test_history_query.py'
  - 'tests/integration/test_history_query.py'
inputDocuments:
  - '_bmad-output/implementation-artifacts/spec-3-2-history-query-through-the-gateway.md'
  - '_bmad-output/implementation-artifacts/epic-3-context.md'
  - '_bmad-output/test-artifacts/atdd-checklist-3-1-history-consumer-recording-terminal-events.md'
  - 'lambdas/history_consumer/tests/conftest.py'
  - 'lambdas/history_consumer/tests/test_history_consumer.py'
  - 'lambdas/_shared/errors.py'
  - 'lambdas/_shared/status.py'
  - 'lambdas/upload_handler/handler.py'
  - 'tests/integration/conftest.py'
  - 'tests/integration/test_history_leg.py'
primaryLevel: 'unit (pytest) + live floci integration'
---

# ATDD Checklist — Epic 3, Story 3.2: History Query Through the Gateway

**Date:** 2026-08-23
**Author:** Murat (Test Architect) for Kygor
**Primary Test Level:** Unit (pytest with fakes) — integration backstop via live floci + CI mirror

## Step 1 — Preflight & Context

- **Stack detection:** `backend` (Python 3.11 Lambdas + pytest; no package.json / no JS runner). Playwright/Pact.js mandates out of scope (scope discipline: they bind JS/TS runners only).
- **Prerequisites:** spec approved (`status: ready-for-dev`, frozen intent block, I/O matrix present); test framework configured (pytest, per-Lambda `conftest.py` registering `_shared` as `shared`); dev environment = floci on `localhost:4566` + Terraform.
- **Story inputs:** spec I/O matrix (5 rows), 5 acceptance criteria, Code Map naming every file.
- **Patterns to mirror:** `lambdas/history_consumer/tests/` (fakes + monkeypatched module accessors + purity probe), `tests/integration/test_history_leg.py` (upload → poll → assert → cleanup), `tests/integration/conftest.py` (`Stack.history_entries()` is the direct-table oracle).

## Step 2 — Generation Mode

**AI generation** (backend stack — recording mode not applicable). Clear ACs, standard API scenario.

## Step 3 — Test Strategy

**AC → level mapping:**

| AC | Level | Test | Priority |
|----|-------|------|----------|
| AC1 route/integration/permission/function exist, existing resources untouched | Integration (terraform) | `terraform apply` resource diff + `terraform state list` before/after | P1 |
| AC2 upload → PROCESSED → GET history via gateway = 200, entries match direct-table oracle | Integration (live floci) | `tests/integration/test_history_query.py::test_history_query_returns_entries_via_gateway` | P0 |
| AC3 unknown videoId → gateway passes 404 + `{"error"}` through unchanged | Integration (live floci) | `tests/integration/test_history_query.py::test_history_query_unknown_video_id_404` | P0 |
| AC4 Bruno history request passes after upload journey (poll-with-timeout, gateway URL only) | Live (Bruno) | `bru run --env Local` | P1 |
| AC5 all new + existing tests pass, CI mirror green | Gate | ruff + pytest + `bash scripts/ci-local.sh` | P0 |

**Unit level (primary):** handler I/O matrix — 5 rows → red suite in `lambdas/history_query/tests/test_history_query.py`. Plus: known-video-empty-entries 200 (the async-leg distinction the whole poll design depends on — P0 edge), sort order, entry field projection, 404-gate-before-scan ordering, purity probe, config-not-code.

**Duplicate-coverage guard:** unit covers handler logic (sort/filter/error mapping); integration covers the gateway wiring (route, pathParameters delivery, permission, response passthrough). No overlap beyond the shared happy path, which is defense-in-depth on the FR-16 client journey.

**Red phase:** suite written against a nonexistent `history_query.handler` → collection error is the RED evidence (Story 3.1 precedent; project convention is red-suite-then-implement, no committed skip markers).

## Step 4 — Red-Phase Generation (sequential inline)

Execution mode: `tea_execution_mode: auto` → capability probe: subagent delegation unreliable on this deployment (5xx/false-done history) → **sequential, inline**. Worker B (E2E/browser): N/A — backend stack, no browser surface.

## Risk Assessment

| Risk | Impact | Likelihood | Test response |
|------|--------|-----------|---------------|
| 404-on-empty design breaks the async poll (Bruno + integration both depend on 200+empty) | High (FR-16 journey untestable without races) | Medium | Unit T2 explicit: known videoId, zero entries → 200 `entries: []`, NOT 404 |
| Gateway does not deliver `pathParameters` per v2 payload format 2.0 on floci | High (route dead) | Medium (spec Ask-First halt) | Integration AC3 asserts the requested videoId appears in the 404 error body — proves delivery end-to-end |
| Scan returns other videos' entries (filter forgotten/misbound) | High (audit-trail leak across videos) | Medium | Unit T6: fake scan evaluates the FilterExpression; other-video entries seeded, must be excluded; binding value asserted |
| Sort order wrong (handler returns scan order) | Medium (FR-16 contract) | Medium | Unit T7: seeded out of order, asserted ascending |
| Metadata fields leak into response (Never boundary) | Medium | Low | Unit T1/T8: body keys exactly `{videoId, entries}`; entry keys exactly `{status, eventId, timestamp}` |
| Terraform wiring gap (zip source blocks, route, invoke permission) | High (nothing runs) | Medium (hand-maintained zip blocks are a known failure class) | Live AC1: apply + state diff; integration tests exercise the route |
| Upload-leg / Story 3.1 resources disturbed | Medium (regresses Epics 1–3.1) | Low | Live AC1: `terraform state list` before/after |

## Unit Test Checklist (RED phase — `lambdas/history_query/tests/test_history_query.py`)

Fakes: `FakeMetadataTable` (get_item, configurable known/error), `FakeHistoryTable` (scan that EVALUATES a simple `attr = :placeholder` FilterExpression against ExpressionAttributeValues — a handler that forgets the filter fails T6 instead of silently passing; no-filter scan returns everything). Monkeypatch module accessors `_metadata_table()` / `_history_table()` (project convention).

I/O matrix coverage (one test class per row minimum):

- [ ] T1 happy path: known videoId + 2 entries → 200, body `{videoId, entries}`, body keys exactly those two, Content-Type json, structured log with videoId + count
- [ ] T2 known videoId, no entries → 200 `"entries": []` — NOT 404 (the async-leg distinction)
- [ ] T3 unknown videoId → 404 `{"error"}` naming the videoId; gate runs BEFORE the scan (get_calls recorded, scan_calls empty)
- [ ] T4 missing/empty videoId (parametrize: None event / non-dict / no pathParameters / None / {} / empty / whitespace / non-string) → 400 via MalformedInputError; no scan performed
- [ ] T5 transient errors → 500 via map_error (metadata get AND scan, both mapped)
- [ ] T6 filtering: other-video entries excluded; scan ExpressionAttributeValues bound to the requested videoId
- [ ] T7 sort: entries seeded out of order → returned timestamp ascending
- [ ] T8 projection: entry keys exactly `{status, eventId, timestamp}` with correct values (eventId = deterministic UUID5)
- [ ] T9 purity probe: client-recorder — only a `dynamodb` resource is ever constructed; never s3/events/states/sqs (patches only `h.clients`, real accessors run)
- [ ] T10 config-not-code: missing `METADATA_TABLE` / `HISTORY_TABLE` env → RuntimeError from the real accessors

Fixture discipline: history items built via `shared.events.event_id(videoId, status)` — the consumer's real key derivation, never hand-typed UUIDs. Gateway events use the v2 payload format 2.0 shape (`version`, `routeKey`, `rawPath`, `pathParameters`).

## Integration Test Checklist (RED phase — `tests/integration/test_history_query.py`)

- [ ] I1 (AC2) upload via gateway → poll `status-history` to entry → GET `/videos/{vid}/history` via gateway → 200, `videoId` matches, entries equal the direct-table oracle (`stack.history_entries()`, sorted) projected to `{status, eventId, timestamp}`; cleanup in finally
- [ ] I2 (AC3) unknown videoId → GET via gateway → 404, `{"error"}` present, error names the requested videoId (proves pathParameters delivery + passthrough)

Both collect cleanly today (2 tests); they fail at runtime until the route exists (route 404 from the gateway ≠ handler 404 body — I2's error-body assertion distinguishes them).

## Terraform Checklist (`terraform/history.tf` — implementation phase)

- [ ] X1 zip source blocks: `shared/` (all 5 modules) + `history_query/` package — hand-maintained set complete, mirrors the consumer's blocks (history.tf:87-125)
- [ ] X2 IAM: logs + `dynamodb:GetItem` on video-metadata only + `dynamodb:Scan` on status-history only (least privilege)
- [ ] X3 env: `METADATA_TABLE`, `HISTORY_TABLE`, `AWS_ENDPOINT_URL` — no names in code (NFR-4)
- [ ] X4 handler string `history_query.handler.handler`; Python 3.11
- [ ] X5 `aws_apigatewayv2_integration` + `aws_apigatewayv2_route` (`GET /videos/{videoId}/history`) on the EXISTING gateway + `aws_lambda_permission` scoped to the new route
- [ ] X6 upload-leg resources (upload.tf) and Story 3.1 resources byte-unchanged
- [ ] X7 output for the new route; `terraform fmt -check` + `terraform validate` green

## Bruno Checklist (implementation phase)

- [ ] B1 `bruno/history-query.bru`: GET `{{gatewayBaseUrl}}/videos/{{videoId}}/history`, assert blocks, `script:pre-request` poll-with-timeout (~2 s sleep loop, ~120 s deadline) — no fixed sleeps in the request itself (FR-22)
- [ ] B2 `bruno/upload-video.bru`: `script:test` captures `res.body.videoId` into a collection var so the history request chains
- [ ] B3 `bru run --env Local` green after the upload journey

## Live Verification Checklist (floci — implementation phase)

- [ ] L1 floci healthy (`curl -sf http://localhost:4566/_localstack/health`)
- [ ] L2 `terraform apply` — only the new history-query resources created; `terraform state list` diff confirms no churn on existing resources (worktree state gap: spec Design Notes import procedure if EntityAlreadyExists)
- [ ] L3 upload via gateway → PROCESSED → `curl {gateway_base_url}/videos/{videoId}/history` → 200 with the entry `{status: PROCESSED, eventId: UUID5(videoId, PROCESSED), timestamp}`
- [ ] L4 `curl {gateway_base_url}/videos/unknown-id/history` → 404 with `{"error": ...}`

## Gate

- [ ] G1 `uv run --with ruff ruff check lambdas/ --select E,F`
- [ ] G2 `uv run --with 'pytest>=8.0' pytest lambdas/ -q` — all new + all existing pass
- [ ] G3 `bash scripts/ci-local.sh` — 5 stages green

## Red-Green Workflow

1. RED (done — this workflow): conftest + full T1–T10 suite + integration I1–I2 → suite fails on import
2. GREEN: implement `history_query/handler.py` minimally until T1–T10 pass
3. Terraform: history.tf → fmt/validate → apply → L1–L4
4. Bruno: B1–B3
5. Gate G1–G3, then mark checklist items with evidence (counts, key lines)

## RED Phase Evidence (2026-08-23)

- `pytest lambdas/history_query/ -q` → `ModuleNotFoundError: No module named 'history_query.handler'` (collection error — RED, same shape as Story 3.1)
- `ruff check lambdas/history_query/ tests/integration/test_history_query.py --select E,F` → All checks passed!
- `pytest tests/integration/test_history_query.py --collect-only -q` → 2 tests collected (I1, I2)

## Deviations

- Playwright/E2E/`test.skip()` scaffold machinery: N/A — backend pytest stack; project ATDD convention (Story 3.1 precedent) is a real red suite whose import failure IS the red phase, no committed skip markers. Playwright/Pact.js mandates out of scope (they bind JS/TS runners only).
- Subagent dispatch (step 4A/4B): replaced by sequential inline generation — delegation unreliable on this deployment; step files explicitly permit the inline fallback.
- Story-file `### ATDD Artifacts` backlink: skipped — the spec's frozen-after-approval block and Code Map already name every test file; editing the frozen spec for a pointer adds churn, not information.

## Completion Summary

- **Generated (RED):** `lambdas/history_query/tests/conftest.py`, `lambdas/history_query/tests/test_history_query.py` (T1–T10, ~24 tests), `tests/integration/test_history_query.py` (I1–I2)
- **Checklist:** `_bmad-output/test-artifacts/atdd-checklist-3-2-history-query-through-the-gateway.md`
- **Handoff:** story file `_bmad-output/implementation-artifacts/spec-3-2-history-query-through-the-gateway.md` (ready-for-dev) → next workflow: `bmad-build` for Story 3.2 implementation
- **Key assumption:** floci delivers `pathParameters` per API GW v2 payload format 2.0 — if not, spec says HALT and report (Ask First), do not invent a workaround
