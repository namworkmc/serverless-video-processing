---
title: 'Story 4.3 — Admin-Only Index Rebuild'
type: 'feature'
created: '2026-08-24'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'eed473ecaad5550dcff019413683155abdab031e'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The `search-index` table is derived and disposable (AD-3), but nothing can repopulate it if it is lost or cleared — Story 4.1's consumer only indexes events flowing after the fact. FR-19 requires the index be rebuildable from `video-metadata`, with the rebuild trigger strictly admin-only: no client-facing surface of any kind.

**Approach:** A `search-rebuild` Lambda reachable ONLY by direct invoke (ad-hoc admin via local boto3 / floci's Lambda REST — permitted inspection/admin, never setup): it scans `video-metadata` for `status = PROCESSED` records and upserts each into `SEARCH_INDEX_TABLE` as `{videoId, title, processedKey, indexedAt}` — the exact entry shape Story 4.1 writes. Terraform declares the function in ONE new file containing NO gateway route, NO queue, NO rule, NO event-source mapping — the admin-only constraint holds structurally, not by convention. The Bruno collection gains nothing. RED suite first (ATDD checklist convention), GREEN implementation, live verification on floci: clear the index, invoke, search returns through the gateway again.

## Boundaries & Constraints

**Always:**
- RED before GREEN: mint `atdd-checklist-4-3` (following the `atdd-checklist-4-2` conventions) and write the full failing unit suite before the handler exists.
- PROCESSED-only selection: Scan on `METADATA_TABLE` with a `FilterExpression` binding status to `shared.status.PROCESSED`; FAILED / UPLOADED / PROCESSING records are never indexed (FR-19 consequence, mirrors FR-17).
- Upsert-only repopulation: plain PutItem keyed by videoId — the PK IS the dedupe; re-invocation overwrites, never duplicates, never deletes. Entry shape exactly `{videoId, title, processedKey, indexedAt}` (the 4.1 shape; title stripped).
- Unusable PROCESSED records (missing/empty/non-string `title` or `processedKey`) are counted `skipped`, logged, and do not abort the rebuild — one corrupt record must not take down an admin batch job.
- Not client-facing: no HTTP response mapping, no `map_error` tail — transient errors propagate raw so the invocation fails loudly (transcode/event-publisher precedent); the event payload itself is ignored entirely.
- Config-not-code: `METADATA_TABLE`, `SEARCH_INDEX_TABLE`, `AWS_ENDPOINT_URL` come from Terraform-set env vars only; module-level `_metadata_table()` / `_index_table()` accessors are the monkeypatch targets (project convention).
- Reuse `lambdas/_shared`: `from shared import clients, status`; purity — this Lambda builds ONLY DynamoDB table handles (no S3, no events, no states, no SQS).
- Terraform wiring lives in ONE new file (`terraform/search-rebuild.tf`); every pre-existing `.tf` file stays byte-unchanged, and the Bruno collection stays byte-unchanged (prove via `git diff main -- terraform/ bruno/`). Tables referenced by name (`aws_dynamodb_table.video_metadata` / `.search_index`) — never redeclared.
- Automated pipeline coverage: `tests/integration/test_search_rebuild.py` clears the index, invokes the DEPLOYED function through floci's Lambda REST API, polls the index oracle, and asserts the gateway search surfaces the rebuilt entries — picked up by ci-local stage 5 (same verification-gap closure as Story 4.2's review run 1).

**Ask First:**
- Any modification to an existing `.tf` file or any Bruno file (breaks the byte-unchanged structural proof).
- Stale-entry sweeping (deleting index entries whose videoId no longer has a PROCESSED metadata record) — the ACs scope the rebuild to repopulation; sync-style reconciliation is out of scope.
- Any HTTP-ish response envelope (statusCode/body) or error mapping for this function.
- Paginating the metadata Scan (lab scale, NFR-7 — single Scan stands until someone asks).

**Never:**
- No gateway route, no `aws_apigatewayv2_integration`/`route`/`aws_lambda_permission`, no SQS queue, no EventBridge rule/target, no `aws_lambda_event_source_mapping` ANYWHERE in the new file — the function is direct-invoke-only (FR-19; absence is the acceptance evidence, checked line-by-line in the X-range).
- No Bruno request for the rebuild — the collection must not expose it (FR-19).
- No writes to `video-metadata` (Scan is its only granted action); no deletes against `search-index`.
- No auth, no new dependencies, no DLQ, no retries beyond what a failed invocation already gives.
- No `aws` CLI for provisioning — Terraform only; ad-hoc invoke goes through local boto3 / floci REST (see `lambdas/README.md`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy rebuild | metadata holds ≥1 `PROCESSED` record (mixed among UPLOADED/PROCESSING/FAILED) | one index upsert per PROCESSED record, entry exactly `{videoId, title(stripped), processedKey, indexedAt}`; summary `{"scanned": n, "indexed": n, "skipped": 0}` | N/A |
| Selection semantics | statuses UPLOADED / PROCESSING / PROCESSED / FAILED seeded together | only the PROCESSED record(s) indexed — FAILED never enters the index | N/A |
| Empty source | zero PROCESSED records (empty table or none terminal-OK) | no writes issued; summary `{"scanned": 0, "indexed": 0, "skipped": 0}` | N/A |
| Idempotent re-invoke | same PROCESSED record rebuilt twice | second write overwrites the same videoId PK — no duplicates, count stays 1 | N/A |
| Unusable record | PROCESSED record missing/blank/non-string `title` or `processedKey` | counted `skipped`, warning logged, OTHER records still indexed | skip, not abort |
| Transient failure | scan or put raises any non-skip exception | invocation fails loudly — raw exception propagates (FunctionError), nothing silently swallowed | raised |

</frozen-after-approval>

## Code Map

- `lambdas/search_consumer/handler.py:67-78` -- module-level env-var accessor template (`_metadata_table()` / `_index_table()` raising RuntimeError on unset) to clone; `handler.py:127` shows the PROCESSED constant use; `handler.py:157-164` pins the upsert Item shape this story reproduces.
- `lambdas/search_consumer/tests/conftest.py` + `lambdas/search_query/tests/test_search_query.py` -- RED-suite harness templates: sys.path + `shared` alias conftest (copy as-is), FakeTable pattern (here: a metadata fake EVALUATING the `#s = :st` FilterExpression and rejecting any other expression shape, plus an index fake recording PutItems), `ClientFactoryRecorder` purity probe, `deps` fixture monkeypatching accessors + env.
- `lambdas/_shared/status.py:23-28` -- `status.PROCESSED` (bind the FilterExpression to the shared constant, never a string literal); `status.py:67` `_now_iso()` for `indexedAt` (same use as search_consumer).
- `lambdas/_shared/clients.py:87` -- `dynamodb_table(name)`; endpoint from `AWS_ENDPOINT_URL`.
- `terraform/search.tf:87-200` -- wiring template to clone into the new file: hand-maintained `archive_file` zip source blocks (ALL FIVE shared modules + the function package), IAM role + logs policy, `aws_lambda_function` (python3.11, timeout 30, memory 128, env trio). DELIBERATE OMISSION vs that file: queue, queue policy, rule, target, ESM. DELIBERATE OMISSION vs `terraform/search-query.tf:115-141`: integration/route/permission trio. What the new file must NOT contain is as load-bearing as what it must.
- `terraform/search.tf:216-218` / `integration.tf` -- `aws_dynamodb_table.search_index` / `.video_metadata` declarations — reference `.name`/`.arn`, never redeclare.
- `tests/integration/conftest.py:317-345` -- `Stack.invoke_transcode` (floci Lambda REST invoke template for `invoke_search_rebuild`) and `Stack.search_entries` (direct-table oracle); `conftest.py:349-366` cleanup parity pattern for the index-clearing helper.
- `tests/integration/test_search_query.py` -- integration test template: seed journey → act → poll-with-timeout → gateway assertion.
- `scripts/ci-local.sh` -- validation gate (5 stages); pins `COMPOSE_PROJECT_NAME`.

## Tasks & Acceptance

**Execution:**
- [x] `_bmad-output/test-artifacts/atdd-checklist-4-3-admin-index-rebuild.md` -- mint during this workflow from the 4-2 checklist conventions: T-numbered unit list from the I/O matrix, X-range terraform checks (including the structural-absence checks), L-range live checks, B-range negative Bruno check, gate section -- RED-phase contract.
- [x] `lambdas/search_rebuild/tests/conftest.py` -- create: copy search_consumer conftest (sys.path + shared alias).
- [x] `lambdas/search_rebuild/tests/test_search_rebuild.py` -- create RED suite covering every I/O-matrix row + filter-binding + purity probe + config-not-code accessor tests; run → fails on import (handler absent) -- proves RED before GREEN.
- [x] `lambdas/search_rebuild/__init__.py` + `lambdas/search_rebuild/handler.py` -- create: minimal GREEN implementation per the matrix until the suite passes.
- [x] `terraform/search-rebuild.tf` -- create (ONLY new .tf): zip blocks (5 shared modules + `search_rebuild/`), IAM (logs + `dynamodb:Scan` on video-metadata ARN + `dynamodb:PutItem` on search-index ARN ONLY), Lambda env `METADATA_TABLE`/`SEARCH_INDEX_TABLE`/`AWS_ENDPOINT_URL`, output `search_rebuild_function` -- and NOTHING else: no route/integration/permission, no queue, no rule/target, no ESM.
- [x] `tests/integration/test_search_rebuild.py` (+ conftest helpers `invoke_search_rebuild` / index-clearing) -- create: clear index → invoke DEPLOYED function via floci REST → poll index oracle → gateway `GET /videos/search` returns rebuilt entries; runs in ci-local stage 5 so dead wiring cannot ship green-broken.
- [x] Live verification on floci (reuse running stack; copy main checkout's `terraform/terraform.tfstate*` into the worktree BEFORE any plan/apply) -- upload → PROCESSED → ad-hoc clear search-index → gateway search 200 `[]` → direct invoke search-rebuild → gateway search returns the video; NEW upload still auto-indexes (consumer path regression-free).
- [x] Checklist boxes + sprint-status `4-3-admin-only-index-rebuild` updated with evidence.

**Acceptance Criteria:**
- Given `terraform apply`, when the declared surface is inspected, then the `search-rebuild` function exists reachable only by direct invoke and NO gateway route, rule, or queue references it (structural: the new file contains none, and `git diff main -- terraform/` shows only that file added).
- Given the `search-index` table cleared ad-hoc and PROCESSED videos present in `video-metadata`, when `search-rebuild` is invoked directly (boto3 / floci REST, ad-hoc admin), then it scans `video-metadata`, repopulates the index with PROCESSED videos only, and a subsequent gateway search returns them (FR-19).
- Given the Bruno collection and the gateway route table, when inspected, then no request or route exposes the rebuild (`git diff main -- bruno/` EMPTY — the constraint holds structurally, FR-19).
- Given the full suite, when `bash scripts/ci-local.sh` runs, then all 5 stages green (integration stage exercises the deployed rebuild end-to-end).

## Spec Change Log

- **2026-08-24 — review run 1 patches (P1–P8), no frozen-intent change.**
  Triggering findings: (1) handler stored `videoId`/`processedKey` exactly as read while the 4.1 consumer strips both — parity gap; (2) FR-19's structural absence had no AUTOMATED proof (X5/X6 were one-off manual checks); (3) integration helpers too lenient (`invoke_search_rebuild` could return an error envelope as a summary, `clear_search_index` swallowed failures and didn't paginate). Amended: handler now strips/stores all three string fields consumer-style with bad-videoId cases added to T4; truncation warning added after the Scan; new pure-pytest structural test parses resource blocks from comment-stripped HCL across `terraform/*.tf`; integration helper + setup hardened; rebuilt-`processedKey` value asserted, live summary counts made delta-tolerant, post-invoke reads poll; README section added. **KEEP:** single-scan semantics stay (truncation warns, never paginates — NFR-7); X5/X6 structural-absence checks stay in the checklist AND are now re-proven automatically by `test_terraform_admin_only.py`; `FakeMetadataTable` expression-shape rejection stays (selection-in-query discipline).

## Design Notes

- Response body pinned for testability: summary dict with EXACTLY the keys `{scanned, indexed, skipped}` — `scanned` counts PROCESSED rows the filtered Scan returned, `indexed` the successful upserts, `skipped` the unusable ones. Direct-invoke callers read the summary; nothing else is promised.
- FilterExpression binds the reserved-word attribute via `ExpressionAttributeNames {"#s": "status"}` and value `{"": status.PROCESSED}` — the fake must evaluate that shape and reject any other, so an unfiltered-scan-plus-post-filter handler fails RED tests (selection happens IN the query, mirroring the sibling query lambda's sanctioned pattern).
- No `map_error`: unlike the query lambdas this function has no HTTP contract; importing `shared.errors` would invite HTTP-shaped drift. Transient errors propagate raw — the invoke caller sees FunctionError, which is the correct loud failure for an admin tool.
- `indexedAt` is stamped at rebuild time (now), NOT copied from any source field — the index entry describes the projection state, same semantics as the consumer's write.
- floci caveat: the ad-hoc invoke is inspection/admin (sanctioned by epic context); the README documents the boto3 invoke pattern — no `aws` CLI anywhere in setup/teardown.
- State caveat (session-specific): Terraform state sits in the MAIN checkout's `terraform/terraform.tfstate*`; copy it into the worktree `terraform/` before any plan/apply or apply tries to recreate the whole populated stack.

## Verification

**Commands:**
- `uv run --with ruff ruff check lambdas/ --select E,F` -- expected: no errors
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: RED fails first, then all pass (new suite + all existing)
- `(cd terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate)` -- expected: green
- `git diff main -- terraform/ bruno/` -- expected: EMPTY tracked diff except the single new `terraform/search-rebuild.tf` (untracked pre-commit; `git status` complements)
- `bash scripts/ci-local.sh` -- expected: 5 stages green
- `(cd terraform && terraform apply -input=false)` -- expected: green apply adding only search-rebuild resources
- Local boto3 invoke of `search-rebuild` (pattern in `lambdas/README.md`) -- expected: summary JSON; gateway search before/after per L-range

## Suggested Review Order

**Handler contract (entry point)**

- Filtered Scan binds `#s = :st` to the shared PROCESSED constant — selection happens IN the query
  [`handler.py:80`](../../lambdas/search_rebuild/handler.py#L80)

- Truncation WARNING on LastEvaluatedKey — observability without abandoning single-scan scope
  [`handler.py:87`](../../lambdas/search_rebuild/handler.py#L87)

- Skip-not-abort: unusable videoId/title/processedKey counted skipped, batch continues
  [`handler.py:104`](../../lambdas/search_rebuild/handler.py#L104)

- Upsert in the exact 4.1 entry shape, all fields stripped; PK is the dedupe, no deletes
  [`handler.py:125`](../../lambdas/search_rebuild/handler.py#L125)

- Raw error propagation by design — no map_error tail for a function with no HTTP contract
  [`handler.py:142`](../../lambdas/search_rebuild/handler.py#L142)

**Wiring — the absences ARE the feature (FR-19)**

- Least privilege: logs + Scan(metadata ARN) + PutItem(index ARN) ONLY
  [`search-rebuild.tf:80`](../../terraform/search-rebuild.tf#L80)

- Direct-invoke-only Lambda: no integration/route/permission/queue/rule/ESM anywhere in the file
  [`search-rebuild.tf:116`](../../terraform/search-rebuild.tf#L116)

- Byte-unchanged proofs: `git diff main -- terraform/ bruno/` shows ONLY this file added

**Unit suite (matrix → evidence)**

- FakeMetadataTable EVALUATES the pinned expression shape and rejects any other — post-filter handlers fail loudly
  [`test_search_rebuild.py:43`](../../lambdas/search_rebuild/tests/test_search_rebuild.py#L43)

- Mixed-status seeding proves FAILED/UPLOADED never reach the index
  [`test_search_rebuild.py:202`](../../lambdas/search_rebuild/tests/test_search_rebuild.py#L202)

- Unusable-record parametrization incl. bad-videoId cases (review run 1)
  [`test_search_rebuild.py:349`](../../lambdas/search_rebuild/tests/test_search_rebuild.py#L349)

- Re-invoke overwrites the same PK, never duplicates, no delete calls ever
  [`test_search_rebuild.py:396`](../../lambdas/search_rebuild/tests/test_search_rebuild.py#L396)

- Purity probe + config-not-code accessors (only dynamodb handles; unset env raises)
  [`test_search_rebuild.py:456`](../../lambdas/search_rebuild/tests/test_search_rebuild.py#L456)

**Structural FR-19 proof (review run 1 — verification-gap closure)**

- Automated admin-only check: forbidden resource types absent here, function referenced by no other .tf
  [`test_terraform_admin_only.py:80`](../../lambdas/search_rebuild/tests/test_terraform_admin_only.py#L80)

**Integration coverage (ci-local stage 5)**

- Clear index → invoke DEPLOYED function → poll oracle → gateway search restored, processedKey value asserted
  [`test_search_rebuild.py:18`](../../tests/integration/test_search_rebuild.py#L18)

- Helpers hardened in review run 1: paginated loud clear, error-envelope detection on invoke
  [`conftest.py:335`](../../tests/integration/conftest.py#L335)
