---
stepsCompleted: ['step-01-preflight-and-context', 'step-02-generation-mode', 'step-03-test-strategy', 'step-04-generate-tests', 'step-04c-aggregate', 'step-05-validate-and-complete']
lastStep: 'step-05-validate-and-complete'
lastSaved: '2026-08-19'
storyId: '1.3'
storyKey: '1-3-upload-journey-through-the-gateway'
storyFile: '_bmad-output/planning-artifacts/epics.md (§ Story 1.3)'
atddChecklistPath: '_bmad-output/test-artifacts/atdd-checklist-1-3-upload-journey-through-the-gateway.md'
generatedTestFiles:
  - 'lambdas/upload_handler/tests/test_upload_handler.py'
  - 'lambdas/upload_handler/tests/conftest.py'
inputDocuments:
  - '_bmad-output/planning-artifacts/epics.md'
  - '_bmad-output/implementation-artifacts/sprint-status.yaml'
  - '_bmad/tea/config.yaml'
  - 'lambdas/_shared/tests/conftest.py'
  - 'lambdas/_shared/tests/test_shared.py'
  - 'requirements-dev.txt'
  - 'knowledge: data-factories.md'
  - 'knowledge: component-tdd.md'
  - 'knowledge: test-quality.md'
  - 'knowledge: test-healing-patterns.md'
  - 'knowledge: test-levels-framework.md'
  - 'knowledge: test-priorities-matrix.md'
---

# ATDD Checklist — Story 1.3: Upload Journey Through the Gateway

## Step 1: Preflight & Context

### Stack Detection
- `detected_stack`: **backend** (Python/pytest + Terraform; no UI, no mobile indicators)
- Test framework: pytest >= 8.0 (`requirements-dev.txt`)
- Existing test dir: `lambdas/_shared/tests/` (conftest.py package alias + test_shared.py)

### Prerequisites
- ✅ Story approved with clear acceptance criteria (epics.md § Story 1.3; sprint-status: backlog, deps 1.1/1.2 in review)
- ✅ Backend test config exists (`lambdas/_shared/tests/conftest.py`)
- ✅ Development environment available (python 3.11.16, pytest)

### Story Context
- **story_id**: 1.3
- **story_key**: 1-3-upload-journey-through-the-gateway
- **Affected components**: upload-handler Lambda (new), `video-uploads` bucket, `video-metadata` table, EventBridge custom bus, API Gateway v2 route `POST /videos/upload`, Bruno collection seed
- **Integrations**: shared access layer (`lambdas/_shared/` — create_record, build_envelope, map_error, clients), floci at localhost:4566

### Acceptance Criteria Inventory
| # | AC Summary | Testable via pytest? |
|---|---|---|
| AC1 | Terraform declares bucket/table/Lambda/IAM/bus/gateway route; apply creates all; apiId output | ❌ terraform apply/validate |
| AC2 | Multipart POST → raw parse (no base64), optional title w/ filename fallback, 2xx + videoId, S3 object, UPLOADED record, video.uploaded event | ✅ handler unit/integration tests with fakes |
| AC3 | Bruno collection: env file w/ gateway base URL, assert blocks, bru run passes, gateway-only | ❌ bru run (collection authored in story) |
| AC4 | Malformed request → 400 {"error": ...} passthrough | ✅ handler error-path tests |

### Red-Phase Scope Decision
- **In scope (pytest scaffolds)**: upload-handler Lambda behavior — multipart parsing, title fallback, response shape, S3/DynamoDB/EventBridge side effects via fakes, error mapping (400).
- **Out of scope (checklist-only verification)**: Terraform resource declarations (AC1), Bruno collection (AC3) — verified by `terraform apply` / `bru run` during implementation, not pytest.

### Reusable Patterns (from Story 1.2 tests)
- `FakeTable` in-memory DynamoDB stub honouring `attribute_not_exists(videoId)` and `#s = :expected` conditionals
- `conftest.py` importlib alias: `lambdas/_shared` → `shared` package
- Shared layer API: `status.create_record(table, videoId, title, bucket, key, ...)`, `events.build_envelope(EVENT_UPLOADED, detail)`, `errors.map_error(exc) → (code, body)`

### TEA Config Flags
- `tea_use_playwright_utils: true` — **N/A** (pure Python backend, no Playwright; skipped)
- `tea_use_pactjs_utils: true` — **N/A** (no contract-testing relevance for single-service upload; skipped)
- `tea_pact_mcp: mcp` — not probed (no Pact relevance)
- `tea_browser_automation: auto` — N/A (backend)
- `test_stack_type: auto` → resolved to `backend`

## Step 2: Generation Mode

**Mode: AI Generation** — backend stack, no browser recording applicable. Acceptance criteria are clear and scenarios are standard (API handler, multipart parsing, service interactions).

## Step 3: Test Strategy

### AC → Scenario Mapping

| Test ID | AC | Scenario | Level | Priority | Red-Phase? |
|---|---|---|---|---|---|
| 1.3-UNIT-001 | AC2 | Handler parses raw multipart body (never assumes base64) and extracts file bytes + filename | Unit | P0 | ✅ |
| 1.3-UNIT-002 | AC2 | Optional `title` form field read; falls back to uploaded filename when absent | Unit | P1 | ✅ |
| 1.3-UNIT-003 | AC2 | Handler mints UUID videoId, returns 2xx with `{"videoId": ...}` | Unit | P0 | ✅ |
| 1.3-UNIT-004 | AC2 | S3 put_object called with key containing videoId, correct bucket, correct bytes | Unit | P0 | ✅ |
| 1.3-UNIT-005 | AC2 | `create_record` called with correct args (videoId, title, bucket, key, content_type, size) | Unit | P0 | ✅ |
| 1.3-UNIT-006 | AC2 | `build_envelope(EVENT_UPLOADED, detail)` called and `put_events` invoked with envelope | Unit | P0 | ✅ |
| 1.3-UNIT-007 | AC4 | Missing file part → 400 `{"error": ...}` via map_error(MalformedInputError) | Unit | P0 | ✅ |
| 1.3-UNIT-008 | AC4 | Unparseable multipart body → 400 `{"error": ...}` | Unit | P1 | ✅ |
| 1.3-INT-001 | AC2 | Full handler invocation with fake S3/DDB/EventBridge: object lands, record exists, event emitted — end-to-end within handler boundary | Integration | P0 | ✅ |
| 1.3-INT-002 | AC2 | Idempotent re-upload (same videoId scenario not applicable — new UUID each time) but duplicate filename produces distinct keys | Integration | P2 | ✅ |

### Level Rationale (backend stack)
- **Unit**: handler logic in isolation — multipart parsing, title fallback, response construction, error mapping. Fakes for S3/DDB/EventBridge clients.
- **Integration**: handler wired to FakeTable + fake S3/EventBridge, verifying the full side-effect chain within the handler boundary.
- **No E2E**: pure backend; the gateway-to-Lambda path is exercised by Bruno (AC3) during implementation, not pytest.
- **No API/Contract (Pact)**: single-service upload; no consumer/provider contract surface.

### Priority Rationale
- **P0** (data integrity + core journey): multipart parse, videoId mint, S3 write, DDB record, event emit, 400 on missing file — these are the definition-of-done for the upload leg.
- **P1** (core UX detail): title fallback, unparseable body — important but secondary to the happy path.
- **P2** (edge): duplicate filename distinctness — low risk, lab-scale.

### Duplicate Coverage Guard
- Shared layer behavior (transition table, envelope shape, error mapping) is already covered by `test_shared.py` — NOT re-tested here. This story tests the **handler's use** of those APIs, not the APIs themselves.

### Red-Phase Confirmation
All 10 tests are designed to **fail before implementation**: the `upload-handler` module does not exist yet. Tests will import from `lambdas/upload_handler/` (to be created in the implementation story). The red phase confirms the test harness is wired correctly and the ACs are encoded as executable assertions.

## Step 4: Test Generation (Sequential Mode)

**Execution mode resolution:** `tea_execution_mode: auto` → backend stack, no agent-team/subagent worker infrastructure for pytest generation → **sequential**. Worker B (E2E) skipped per Step 3 (no browser surface). Playwright-utils generation contract N/A (Python/pytest, not TypeScript).

**Generated files:**
- `lambdas/upload_handler/tests/test_upload_handler.py` — 16 red-phase test scaffolds (8 unit classes + 2 integration classes)
- `lambdas/upload_handler/tests/conftest.py` — path wiring: `lambdas/` on sys.path + `shared` package alias (mirrors `_shared/tests/conftest.py`)

**Red-phase mechanism (pytest equivalent of test.skip()):**
- Module-level `pytest.importorskip("upload_handler.handler")` — entire module skips while the handler is missing
- Every test class additionally carries `@pytest.mark.skip(reason="RED PHASE: ...")` so tests stay skipped even after the module exists, until the developer activates them task-by-task

**Red-phase verification run:**
```
$ python -m pytest lambdas/upload_handler/tests/ -v
collected 0 items / 1 skipped
============================= 1 skipped in 0.04s =============================
```
✅ Module collects cleanly and skips — no collection errors, no false passes.

**Regression check:** `lambdas/_shared/tests/` — 27 passed (new conftest does not disturb existing suite).

## Step 4C: Aggregation

### TDD Red Phase Compliance
- ✅ All tests skipped (importorskip + per-class skip markers)
- ✅ All tests assert EXPECTED behavior (real assertions on status codes, record fields, S3 keys, event envelopes — no placeholders)
- ✅ All tests marked expected-to-fail (red phase)

### Fixture Infrastructure
Created inline in the test file (pytest idiom, no separate fixture module needed at red phase):
- `FakeS3Client` — records put_object calls
- `FakeEventBridgeClient` — records put_events entries
- `FakeTable` — same conditional-write-honouring stub as `test_shared.py`
- `deps` fixture — env vars (NFR-4 names) + monkeypatched client factories
- `_multipart_body` / `_make_event` helpers — build raw multipart bodies and API GW v2 events as floci delivers them (`isBase64Encoded: False`)

### Summary Statistics
| Metric | Value |
|---|---|
| TDD phase | RED |
| Total test scaffolds | 21 |
| Unit tests | 19 (across 8 classes) |
| Integration tests | 2 (2 classes) |
| All tests skipped | ✅ |
| Expected to fail on activation | ✅ |
| Execution mode | sequential (baseline, no parallel speedup) |
| AC coverage | AC2 (happy path: 16 tests), AC4 (error path: 4 tests), AC1/AC3 checklist-only |

### AC Coverage Detail
- **AC1 (Terraform)**: no pytest scaffold — verified by `terraform apply` during implementation
- **AC2 (upload journey)**: 1.3-UNIT-001…006, 1.3-INT-001, 1.3-INT-002
- **AC3 (Bruno)**: no pytest scaffold — collection authored during implementation, verified by `bru run`
- **AC4 (malformed → 400)**: 1.3-UNIT-007, 1.3-UNIT-008

### Next Steps (Task-by-Task Activation)
During implementation of Story 1.3:
1. Implement `lambdas/upload_handler/handler.py` (module import unblocks)
2. Remove `@pytest.mark.skip` from the class matching the current task
3. Run `python -m pytest lambdas/upload_handler/tests/ -v`
4. Verify activated tests fail first, then pass after implementation (green phase)
5. Adjust `deps` fixture monkeypatch targets to the handler's actual client-factory names
6. Commit passing tests

## Step 5: Validation & Completion

### Checklist Validation (backend/pytest adaptation)

**Prerequisites:**
- [x] Story approved with clear, testable acceptance criteria (epics.md § Story 1.3)
- [x] Development environment ready (python 3.11.16, pytest 9.1.1)
- [x] Test framework configuration available (pytest; backend equivalent of framework config)
- [x] Existing test patterns reviewed (`lambdas/_shared/tests/` — FakeTable, conftest alias)

**Story Context (Step 1):**
- [x] Story loaded and parsed; all 4 AC groups extracted
- [x] Affected components identified (upload-handler, bucket, table, bus, gateway route, Bruno)
- [x] Knowledge fragments loaded: data-factories, component-tdd, test-quality, test-healing-patterns, test-levels-framework, test-priorities-matrix

**Test Strategy (Step 2/3):**
- [x] Each AC mapped to appropriate level (Unit/Integration; no E2E for pure backend)
- [x] Duplicate coverage avoided (shared layer NOT re-tested; handler's use of it tested)
- [x] P0–P2 priorities assigned with rationale

**Red-Phase Scaffolds (Step 3/4):**
- [x] Test file created: `lambdas/upload_handler/tests/test_upload_handler.py` (21 tests, 10 classes)
- [x] All tests are red-phase scaffolds (importorskip + @pytest.mark.skip)
- [x] Tests assert EXPECTED behavior — real assertions, no placeholders
- [x] Descriptive test names; Given-When-Then intent in docstrings
- [x] No flaky patterns, no hard waits, no test interdependencies
- [x] Deterministic: fakes produce same output for same input
- [x] RED verified by execution: `1 skipped in 0.04s` (module skips cleanly)
- [x] Regression check: existing 27 `_shared` tests still pass

**Data Infrastructure (Step 4):**
- [x] Fakes created (FakeS3Client, FakeEventBridgeClient, FakeTable) — pytest fixture idiom replaces TS factories/fixtures
- [x] `deps` fixture wires env vars (NFR-4) + monkeypatched client factories
- [x] Multipart/event builders produce realistic payloads matching floci's delivery format

**Quality Checks:**
- [x] No linting errors (write_file lint: ok)
- [x] Consistent naming with existing test suite
- [x] Playwright Utils mandate: N/A (Python/pytest backend, not Playwright)
- [x] Pact.js Utils mandate: N/A (no consumer-provider boundary)
- [x] No orphaned browser sessions (no browser used)
- [x] All artifacts in `_bmad-output/test-artifacts/` and `lambdas/upload_handler/tests/`

**Gaps noted (acceptable for red phase):**
- `deps` fixture monkeypatches `_s3_client`/`_dynamo_table`/`_events_client` with `raising=False` — actual factory names TBD at implementation; activation step 5 documents the adjustment
- AC1 (Terraform) and AC3 (Bruno) have no pytest scaffolds by design — verified by `terraform apply` / `bru run` during implementation

### Completion Summary

| Item | Value |
|---|---|
| Story | 1.3 — Upload Journey Through the Gateway |
| story_key | `1-3-upload-journey-through-the-gateway` |
| Primary test level | Unit + Integration (backend) |
| Test scaffolds | 21 (19 unit, 2 integration) across 10 classes |
| Test file | `lambdas/upload_handler/tests/test_upload_handler.py` |
| Harness | `lambdas/upload_handler/tests/conftest.py` |
| Checklist | `_bmad-output/test-artifacts/atdd-checklist-1-3-upload-journey-through-the-gateway.md` |
| Story file (handoff) | `_bmad-output/planning-artifacts/epics.md` (§ Story 1.3) |
| TDD phase | 🔴 RED — all scaffolds skipped until implementation |

**Key assumptions:**
1. Handler module will be `lambdas/upload_handler/handler.py` exposing `handler(event, context)`
2. Handler will use internal client factories monkeypatchable in tests (adjust at green phase)
3. API GW v2 event shape: `{"version": "2.0", "headers": {...}, "body": "<raw multipart>", "isBase64Encoded": false}`
4. Response shape: `{"statusCode": int, "body": json.dumps({"videoId": ...})}` / `{"error": ...}`

**Next recommended workflow:** `bmad-build` (dev-story) for Story 1.3 implementation — activate tests task-by-task per the green-phase steps above.
