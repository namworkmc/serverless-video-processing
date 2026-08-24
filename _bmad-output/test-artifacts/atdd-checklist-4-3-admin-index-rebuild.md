---
workflowType: 'testarch-atdd'
storyId: '4.3'
storyKey: '4-3-admin-only-index-rebuild'
storyFile: '_bmad-output/implementation-artifacts/spec-4-3-admin-index-rebuild.md'
atddChecklistPath: '_bmad-output/test-artifacts/atdd-checklist-4-3-admin-index-rebuild.md'
generatedTestFiles:
  - 'lambdas/search_rebuild/tests/conftest.py'
  - 'lambdas/search_rebuild/tests/test_search_rebuild.py'
inputDocuments:
  - '_bmad-output/implementation-artifacts/spec-4-3-admin-index-rebuild.md'
  - '_bmad-output/implementation-artifacts/epic-4-context.md'
  - '_bmad-output/test-artifacts/atdd-checklist-4-2-title-search-through-the-gateway.md'
  - 'lambdas/search_consumer/handler.py (upsert shape + accessor template)'
  - 'lambdas/search_consumer/tests/, lambdas/search_query/tests/ (RED-suite harness templates)'
  - 'lambdas/_shared/status.py, clients.py'
  - 'terraform/search.tf (wiring template), search-query.tf (the trio this story must NOT contain)'
  - 'tests/integration/conftest.py (invoke_transcode + search_entries oracle templates)'
primaryLevel: 'unit (pytest) + live floci integration'
---

# ATDD Checklist — Epic 4, Story 4.3: Admin-Only Index Rebuild

**Date:** 2026-08-24
**Author:** Murat (Test Architect) for Kygor
**Primary Test Level:** Unit (pytest with fakes) — live floci verification + CI mirror backstop

## Story Summary

A `search-rebuild` Lambda reachable ONLY by direct invoke (ad-hoc admin via local boto3 / floci Lambda REST — never setup, never client-facing): scans `METADATA_TABLE` with a `FilterExpression` binding status to `shared.status.PROCESSED` and upserts each hit into `SEARCH_INDEX_TABLE` as `{videoId, title(stripped), processedKey, indexedAt}` — the exact 4.1 entry shape, PK-is-dedupe, no deletes. Unusable PROCESSED records are counted `skipped` without aborting; transient errors propagate raw (no HTTP mapping — transcode precedent). Returns summary `{scanned, indexed, skipped}` exactly. Terraform declares it in ONE new file structurally containing NO gateway route/integration/permission, NO queue, NO rule/target, NO event-source mapping — and the Bruno collection stays byte-unchanged.

## Design Notes

- **Selection must happen IN the Scan.** The metadata fake EVALUATES the `{"#s": "status"} / {":st": PROCESSED}` FilterExpression shape against seeded items and raises AssertionError on any other expression — an unfiltered-scan-plus-post-filter handler fails RED tests, same discipline as 4.2's contains()-evaluating fake.
- **Absence is the evidence for FR-19.** No unit test can prove Terraform absence — that lives in the X-range (`git diff main` proofs + line-by-line resource scan of the new file) and the B-range negative Bruno check. Do not invent a tf-text-parsing unit test.
- **No `map_error`, deliberately**: importing `shared.errors` would invite HTTP-shaped drift into a function with no HTTP contract; the purity probe should tolerate-only dynamodb handles and the import set stays `clients, status`.
- **Idempotency = PK overwrite**, not ConditionExpression: the index fake records puts per videoId; re-invoke asserts last-write-wins on the same PK.
- **Terraform state caveat (session-specific)**: copy the MAIN checkout's `terraform/terraform.tfstate*` (+ `.terraform/`) into the worktree BEFORE any plan/apply or apply would try to recreate the whole populated stack.

## Risk Assessment

| Risk | Impact | Likelihood | Test response |
|------|--------|-----------|---------------|
| FAILED/UPLOADED records leak into the index | High (breaks FR-17's twin promise; FR-19 says PROCESSED only) | Medium (consumer filters per-event; rebuild filters per-record — easy mis-port) | Unit T2: mixed statuses → only PROCESSED indexed; fake enforces filtered Scan |
| Unfiltered scan + in-code post-filter | Medium (works, but diverges from sanctioned sibling pattern) | Medium | Design Note 1: fake rejects any other FilterExpression shape |
| Rebuild deletes or duplicates entries | High (destroys consumer-written entries / breaks dedupe) | Low | Unit T1c/T5: no delete calls recorded; re-invoke overwrites same PK |
| One corrupt record aborts the batch | Medium (admin tool dies on one bad row) | Medium (consumer raises on transient; rebuild must skip-not-abort) | Unit T4: poisoned record skipped, others still indexed |
| Gateway/route/queue/rule sneaks into the new .tf | High (FR-19 violated — client-facing rebuild surface) | Low (template files nearby all HAVE those blocks to mis-copy) | X5/X6 structural checks + B1 Bruno diff proof |
| HTTP error-mapping creep (statusCode bodies) | Low (contract drift, untestable surface) | Medium (query-lambda siblings all map errors) | Unit T6: transient failure propagates RAW (no dict body) |
| Hardcoded names / endpoint in handler | Medium (violates config-not-code NFR-4) | Low | Unit T7/T8: real accessors raise RuntimeError on unset env; purity probe |
| Wiring gap ships green-broken (zip blocks/env) | High (invoke fails ImportError) | Medium (hand-maintained zip source blocks are a known failure class) | X-range checklist + L5 live invoke + I1 automated integration test |

## Unit Test Checklist (RED phase — `lambdas/search_rebuild/tests/test_search_rebuild.py`)

Fakes: `FakeMetadataTable` (scan EVALUATES the `#s = :st` FilterExpression — equality against ExpressionAttributeValues bound to `status.PROCESSED`; raises AssertionError on any other expression shape; records scan calls). `FakeIndexTable` (records every put_item/delete_item call). `ClientFactoryRecorder` purity probe. Monkeypatch module accessors `_metadata_table()` / `_index_table()` (project convention).

I/O matrix coverage (one class per row minimum):

- [x] T1 happy rebuild: mixed-status metadata (UPLOADED/PROCESSING/PROCESSED/FAILED seeded together) → only the PROCESSED record(s) upserted; entry EXACTLY `{videoId, title(stripped), processedKey, indexedAt}`; summary keys EXACTLY `{scanned, indexed, skipped}` with correct counts; structured log carries counts
- [x] T2 filter binding: fake asserts the FilterExpression is the pinned `#s = :st` shape with value `status.PROCESSED` (constant, not literal); scan issued against METADATA_TABLE handle only
- [x] T3 empty source: zero PROCESSED records (empty items AND all-non-terminal variants) → zero puts issued, summary `{scanned: 0, indexed: 0, skipped: 0}`
- [x] T4 unusable record: PROCESSED record missing/blank/non-string `title` (and variant: bad `processedKey`) → counted skipped, warning logged, remaining valid records STILL indexed; summary reflects skip
- [x] T5 idempotent re-invoke: same PROCESSED record rebuilt twice → second put overwrites SAME videoId PK (last-write-wins), no duplicates, NO delete calls ever
- [x] T6 transient failure: scan raises non-skip exception → raw exception propagates (pytest.raises; result is NOT a statusCode/body dict); put raising likewise propagates
- [x] T7 purity probe: client-recorder — only `dynamodb` handles ever constructed; never s3/events/states/sqs (patches only `h.clients`, real accessors run)
- [x] T8 config-not-code: missing `METADATA_TABLE` env → RuntimeError from real `_metadata_table()`; missing `SEARCH_INDEX_TABLE` env → RuntimeError from real `_index_table()`

Fixture discipline: metadata items shaped exactly as the upload/state-machine legs write them (`{videoId, title, status, bucket, originalKey, processedKey, ...}`); handler invoked with an arbitrary dict payload (e.g. `{}`) — the event is ignored BY CONTRACT (assert same summary for `{}` and `None`-ish inputs where the contract allows).

## Terraform Checklist (`terraform/search-rebuild.tf` — ONE new file)

- [x] X1 zip source blocks: `shared/` (all FIVE modules — status/events/errors/clients/__init__) + `search_rebuild/` package, hand-maintained set complete
- [x] X2 IAM: logs (`Resource="*"` parity) + `dynamodb:Scan` on `aws_dynamodb_table.video_metadata.arn` + `dynamodb:PutItem` on `aws_dynamodb_table.search_index.arn` ONLY — nothing else (no DeleteItem, no GetItem)
- [x] X3 env: `METADATA_TABLE` + `SEARCH_INDEX_TABLE` (both by reference `.name`) + `AWS_ENDPOINT_URL` (= local.lambda_endpoint_url) — no names in code
- [x] X4 Lambda: python3.11, handler string `search_rebuild.handler.handler`, timeout 30, memory 128
- [x] X5 STRUCTURAL ABSENCE (FR-19): the file contains NO `aws_apigatewayv2_integration`, NO `aws_apigatewayv2_route`, NO `aws_lambda_permission`, NO `aws_sqs_queue*`, NO `aws_cloudwatch_event_rule`/`target`, NO `aws_lambda_event_source_mapping` — checked line-by-line
- [x] X6 EVERY pre-existing `.tf` byte-unchanged AND `bruno/` byte-unchanged — `git diff main -- terraform/ bruno/` shows ONLY the new file added
- [x] X7 output `search_rebuild_function`; `terraform fmt -check -recursive` + `terraform validate` green

## Live Verification Checklist (floci)

- [x] L1 floci healthy (`curl -sf http://localhost:4566/_localstack/health`)
- [x] L2 worktree state seeded from main checkout BEFORE plan/apply; `terraform apply` green adding ONLY search-rebuild resources; no churn on existing resources
- [x] L3 baseline journey: upload via gateway → PROCESSED → search-index auto-populated by the CONSUMER (hit visible through `GET /videos/search`)
- [x] L4 disposable proof: ad-hoc clear of search-index entries → gateway search returns 200 `[]` (index truly empty)
- [x] L5 rebuild proof: direct invoke of deployed `search-rebuild` (local boto3 / floci REST — admin inspection, NOT setup) → summary shows `indexed ≥ 1`; gateway search returns the video again with title from the rebuilt entry
- [x] L6 consumer-path regression: a NEW upload still auto-indexes WITHOUT any manual rebuild; history route unaffected

## Integration Checklist (`tests/integration/test_search_rebuild.py` — ci-local stage 5)

- [x] I1 clear index → invoke DEPLOYED search-rebuild via floci Lambda REST (`Stack.invoke_search_rebuild`) → poll `Stack.search_entries` oracle until repopulated → gateway `GET /videos/search?title=<substring>` returns the rebuilt video (dead wiring can no longer ship green-broken)
- [x] I2 conftest gains `invoke_search_rebuild` + index-clearing helper mirroring `invoke_transcode`/cleanup parity

## Bruno Checklist (negative — implementation phase)

- [x] B1 NO Bruno change: collection byte-unchanged, no rebuild request anywhere (`git diff main -- bruno/` EMPTY) — FR-19's "no client-facing surface" includes the test collection

## Gate

- [x] G1 `uv run --with ruff ruff check lambdas/ --select E,F`
- [x] G2 `uv run --with 'pytest>=8.0' pytest lambdas/ -q` — RED failed first, then all new + all existing pass
- [x] G3 `bash scripts/ci-local.sh` — 5 stages green (secrets-scan → lint → unit-test → tf-validate → integration)

## Red-Green Workflow

1. RED: mint this checklist → write conftest + full T1–T8 suite → suite fails on import (handler absent) — evidence recorded below
2. GREEN: implement `search_rebuild/handler.py` minimally until T1–T8 pass
3. Terraform: `search-rebuild.tf` (new file only) → fmt/validate → apply (state seeded first) → L1–L6
4. Integration: I1–I2
5. Bruno negative check B1
6. Gate G1–G3, then mark checklist items with evidence (counts, key lines)

## RED Phase Evidence (2026-08-24)

- `pytest lambdas/search_rebuild/ -q` → `ModuleNotFoundError: No module named 'search_rebuild.handler'` (collection error — RED, same shape as Stories 3.1/3.2/4.1/4.2)
- `ruff check lambdas/search_rebuild/ --select E,F` → All checks passed! (RED suite itself lint-clean)

## Build Evidence (bmad-build, 2026-08-24)

- **Files:** `lambdas/search_rebuild/{__init__,handler}.py` + `tests/{conftest,test_search_rebuild}.py`, `terraform/search-rebuild.tf` (ONLY new `.tf`), `tests/integration/test_search_rebuild.py` + `Stack.invoke_search_rebuild`/`clear_search_index` in the integration conftest. Bruno untouched (B1 below).
- **G2:** RED first (`ModuleNotFoundError` above; two RED-suite-only bugs fixed during GREEN — a parametrize helper that deleted an already-absent key, and a transient-error test asserting on a result that could only exist if the raise policy failed), then GREEN: `pytest lambdas/ -q` → **384 passed** (28 new search-rebuild cases + all 356 existing).
- **X5/X6 proofs:** forbidden resource types appear in `search-rebuild.tf` ONLY inside the explanatory comment block (lines 10–14) — zero declarations. `git diff main --stat -- terraform/ bruno/` → empty tracked diff; `git status terraform/` → only untracked `search-rebuild.tf`. Post-gate `terraform plan -detailed-exitcode` → exit 0, no drift.
- **L2:** worktree state seeded from the MAIN checkout (`terraform.tfstate*` + `.terraform/`) BEFORE any plan/apply per spec Design Notes. Pre-apply plan: **3 to add** (`aws_iam_role.search_rebuild`, `aws_iam_role_policy.search_rebuild`, `aws_lambda_function.search_rebuild`), 0 destroy. The one "update" was `aws_lambda_function.search_query` — PRE-EXISTING drift (deployed zip applied 08:08Z predates PR #24's merged sources, commit 09:03+07); apply reconciled it to HEAD. Apply green; post-apply plan exit 0.
- **L3–L6 live run** (ad-hoc script via local boto3 + requests): L3 upload `ba64e78d…` ("Rebuild Proof …") → PROCESSED → consumer auto-indexed → gateway hit with exactly `{videoId, title, processedKey, indexedAt}`; L4 cleared **32** index entries ad-hoc → gateway search 200 `[]`; L5 direct invoke `search-rebuild` → summary `{'scanned': 4, 'indexed': 4, 'skipped': 0}` → gateway search returns the video again; L5b re-invoke → same PK overwritten, entry count unchanged (no dupes); L6 NEW upload auto-indexed WITHOUT any rebuild + history route green.
- **I1/I2:** `tests/integration/test_search_rebuild.py` green standalone against the deployed stack, then in ci-local stage 5: integration suite **18 passed** (17 prior + 1 new).
- **G3:** `bash scripts/ci-local.sh` (git-bash; WSL has no bash on this box) → all 5 stages green: secrets-scan clean, lint clean, unit-test 384 passed, tf-validate Success!, integration 18/18.

## Deviations

- None against the frozen spec. Note for reviewers: the pre-existing `search-query` zip drift reconciled by the apply (see L2) touched a deployed function OUTSIDE this story's scope — sources were already committed to main by Story 4.2's merge; no wiring changed.

## Review Run 1 Evidence (2026-08-24)

Code-review patches P1–P8 applied, then re-verified:

- **P1 — consumer parity for stored fields:** `search_rebuild/handler.py` now strips `videoId` and `processedKey` (title already stripped); blank-after-strip or non-string is unusable → skipped, mirroring search_consumer field validation. T4 parametrize grew the four bad-videoId cases (`None`/`""`/whitespace/123) and a positive `test_padded_video_id_and_processed_key_stripped` pins that padded fields land on the SAME PK the consumer writes.
- **P2 — truncation observability:** after the metadata Scan a present `LastEvaluatedKey` logs a WARNING ("rebuilt index may be partial … pagination out of scope per NFR-7"); no pagination added. New unit `test_truncated_scan_warns_loudly_still_single_scan` asserts the warning fires AND scan_calls stays 1.
- **P3 — automated FR-19 structural proof:** new `lambdas/search_rebuild/tests/test_terraform_admin_only.py` (pure pytest, no shared import, no infra): parses resource BLOCKS from comment-stripped HCL (quote-aware) and asserts (a) `search-rebuild.tf` declares none of the 7 forbidden types, (b) NO other `terraform/*.tf` mentions `search-rebuild` outside comments, plus an anti-vacuous guard that the file still declares the lambda + role + policy. Skips if terraform dir absent.
- **P4 — helper hardening:** `Stack.invoke_search_rebuild` takes payload verbatim (`json=payload`, callers pass `{}` — no falsy coercion) and raises `RuntimeError` on ANY error envelope (`errorType` OR `errorMessage` key), mirroring `invoke_transcode`.
- **P5 — setup fails loudly:** `Stack.clear_search_index` now paginates via `ExclusiveStartKey` loop and no longer swallows exceptions — it is load-bearing setup; a partial clear must fail the run.
- **P6 — integration assertions:** rebuilt entry's `processedKey` VALUE asserted equal to `processed/{videoId}/fixture.bin` (fidelity, not just key-set); summary counts delta-tolerant against foreign shared-table rows (`indexed >= 1`, `skipped` no longer pinned to 0 live); post-re-invoke index read goes through `poll_until` instead of a bare immediate scan.
- **P7 — docs:** `lambdas/README.md` gained the `search_rebuild/` tree entry + a search-rebuild section (direct-invoke-only admin tool, env trio, least-privilege IAM, deliberate absences, boto3 invoke example).
- **P8 — cosmetic:** stale "TDD Phase: RED" removed from the unit-suite docstring (GREEN).

Re-verification: `ruff check lambdas/ --select E,F` green; `pytest lambdas/ -q` → **393 passed** (28→37 search-rebuild: +4 videoId params, +1 strip-parity, +1 truncation, +3 structural FR-19); one test bug fixed during patching (structural regex lacked `re.MULTILINE` — caught by its own anti-vacuous guard failing); full `ci-local.sh` all 5 stages green, integration **18 passed** including the hardened rebuild journey against the redeployed function.
