---
title: 'Epic-4 retro AI-15 + AI-17 — admin-only guard underscore hole & read-lambda truncation warnings'
type: 'bugfix'
created: '2026-08-26'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'a0fe408cad82631edce71fd96213d3052f82192d'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two epic-4 retro findings (`epic-4-retro-2026-08-26.md`, F1/F2): (AI-15) FR-19's automated proof `test_no_other_tf_file_references_the_function` greps only the hyphenated literal `"search-rebuild"`, so a cross-file Terraform reference in address form (`aws_lambda_function.search_rebuild.arn`) would pass CI while violating admin-only; (AI-17) `search_query` and `history_query` silently drop `LastEvaluatedKey`, so once a table exceeds one Scan page (~1 MB) clients get partial 200s while the newer rebuild convention (`search_rebuild/handler.py:87-91`) warns loudly.

**Approach:** Extend the guard to assert BOTH `"search-rebuild"` AND `"search_rebuild"` are absent from every other `.tf` (comment-stripped), and add one `logger.warning` after each read lambda's Scan plus one unit pin per handler, mirroring the rebuild handler's established pattern. NFR-7 single-scan semantics stay everywhere.

## Boundaries & Constraints

**Always:**
- Config-not-code: no names/endpoints introduced; handlers untouched in that regard.
- Mirror the existing rebuild idiom verbatim in style: warning text contains "truncated", mentions `LastEvaluatedKey`, cites NFR-7 lab scale.
- Both query handlers keep their exact HTTP contracts (200 partial results on truncation; status codes, body shapes, sort order unchanged).

**Ask First:**
- If the new underscore assertion fails against the current tree (a real cross-file reference exists): HALT — that is an FR-19 violation for the human to route, not something to fix silently here.

**Never:**
- No pagination plumbing (`ExclusiveStartKey` loops), no GSI, no DLQ/redrive, no `truncated` flag in API responses or rebuild summary (F8 is separately deferred).
- No edits to `terraform/*.tf` resource logic; this change is Python tests + two handler log lines only.
- No new dependencies; no changes to shared layer.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Address-form ref elsewhere | `aws_lambda_function.search_rebuild.arn` in any other `.tf` outside comments | Guard test FAILS naming file + form | CI red = intended |
| Current tree | No other `.tf` mentions either form (verified via rg) | Guard passes green | N/A |
| Truncated index scan | FakeIndexTable scan result carries `LastEvaluatedKey` | WARNING logged containing "truncated"; still 200 with that page's matches; exactly 1 scan call | N/A |
| Single-page scan | No `LastEvaluatedKey` in result | No warning record; normal 200 | N/A |
| Truncated history scan | FakeHistoryTable scan result carries `LastEvaluatedKey` (videoId known) | Same as truncated index scan, entries shape preserved | N/A |

</frozen-after-approval>

## Code Map

- `lambdas/search_rebuild/tests/test_terraform_admin_only.py:93-104` -- TARGET AI-15: `test_no_other_tf_file_references_the_function`; loops comment-stripped bodies of every `.tf` except `REBUILD_FILE`; asserts only `"search-rebuild"`. Module docstring lines 12-16 describe check (b) — update wording too. Reuse `_strip_hcl_comments` (line 45) as-is.
- `terraform/*.tf` -- READ-ONLY evidence: rg shows every `search[-_]rebuild` occurrence inside `search-rebuild.tf` only; extension must pass unmodified tree.
- `lambdas/search_rebuild/handler.py:84-91` -- PATTERN for AI-17: post-scan `if resp.get("LastEvaluatedKey"): logger.warning(... %d ... LastEvaluatedKey ... partial ... NFR-7 ...)`.
- `lambdas/search_rebuild/tests/test_search_rebuild.py:288-306` -- PIN PATTERN: wrap original fake `.scan` with a function that injects `LastEvaluatedKey`, `caplog.at_level(logging.WARNING)`, assert summary/body intact + "truncated" in some record + `len(scan_calls) == 1`.
- `lambdas/search_query/handler.py:71-74` -- TARGET AI-17: insert warning right after `resp = _index_table().scan(...)`; item count via `len(resp.get("Items", []))`. Logger ready at line 33.
- `lambdas/search_query/tests/test_search_query.py` -- add pin class here; `deps` fixture patches `_index_table`; caplog precedent at lines 201-205; FakeIndexTable.scan returns `{"Items": [...]}` dict (safe to mutate in wrapper).
- `lambdas/history_query/handler.py:78-81` -- TARGET AI-17: same insertion after the history scan. Logger ready at line 30.
- `lambdas/history_query/tests/test_history_query.py` -- add pin class; `deps` fixture patches both accessors and `FakeMetadataTable` already knows VIDEO_ID (404 gate passes); FakeHistoryTable.scan returns mutable dict.
- `lambdas/README.md` -- conventions reference (packaging, tests layout); no change needed.

## Tasks & Acceptance

**Execution:**
- [x] `lambdas/search_rebuild/tests/test_terraform_admin_only.py` -- extend `test_no_other_tf_file_references_the_function` to assert `"search_rebuild"` absent from each other-file body alongside the existing hyphen check; failure message names file and which form hit; update module docstring check-(b) paragraph to say both literal forms -- closes the address-form bypass (AI-15/F1).
- [x] `lambdas/search_query/handler.py` -- after the scan, warn when `resp.get("LastEvaluatedKey")`: "search-index scan truncated after %d items (LastEvaluatedKey present) — results may be partial; pagination is out of scope per NFR-7 lab scale" -- makes partial results observable (AI-17/F2).
- [x] `lambdas/history_query/handler.py` -- same insertion with history wording ("status-history scan truncated after %d items … entries may be partial …") -- parity across both read lambdas.
- [x] `lambdas/search_query/tests/test_search_query.py` -- add `TestScanTruncationObservability`: truncated-fake pin (warning + 200 + page items + single call) and a no-warning control on a normal result -- mirrors rebuild pin.
- [x] `lambdas/history_query/tests/test_history_query.py` -- same pair of tests against the history handler -- mirrors rebuild pin.
- [x] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- flip `epic-4-retro-item-15-close-admin-only-guard-underscore-hole` and `epic-4-retro-item-17-query-truncation-warning-parity` to `status: done` once everything above is green -- user-directed closure of the retro loop.

**Acceptance Criteria:**
- Given a hypothetical `aws_lambda_function.search_rebuild.arn` reference pasted into another `.tf`, when the guard suite runs, then `test_no_other_tf_file_references_the_function` fails naming that file and the offending form.
- Given the unmodified tree, when `pytest lambdas/search_rebuild/tests/test_terraform_admin_only.py` runs, then all tests pass.
- Given fake tables returning one page plus `LastEvaluatedKey`, when each query handler is invoked, then a WARNING record mentioning "truncated" is logged, the HTTP response stays 200 with that page's projected items, and `scan_calls` length is exactly 1.
- Given normal single-page results, when each query handler is invoked, then no truncation warning is recorded.
- Given all edits, when `bash scripts/ci-local.sh` (or ruff+pytest floor if Docker/floci unavailable) runs, then every stage is green before commit.

## Test Scenarios (TEA — from [test-design-epic-ai-15-ai-17.md](../test-artifacts/test-design-epic-ai-15-ai-17.md))

All Unit-level (duplicate-coverage guard: source-text checks + log lines gain nothing from API/E2E). Every pin follows ATDD RED→GREEN per Design Notes; RED failure output is the recorded evidence for risk R3 (vacuous-pin false assurance).

| ID | Scenario (Given / When / Then) | Priority | Covers |
|----|--------------------------------|----------|--------|
| AI15-UNIT-001 | Given another `.tf` contains `aws_lambda_function.search_rebuild.arn` outside comments (scratch probe file, created+removed in one try/finally), when the guard runs, then it FAILS naming file + form | P0 | AC-1 |
| AI15-UNIT-002 | Given the unmodified tree, when the guard suite runs, then all pass — added underscore literal causes no false positive | P0 | AC-2 |
| AI15-UNIT-003 | Given a durable in-suite scratch probe `.tf` (`_probe_ai15_scratch.tf`) carrying the address form written into the terraform dir, when the guard test is invoked inside `pytest.raises(AssertionError)` (probe removed in finally), then the failure message names both the probe filename and the `search_rebuild` form | P0 | AC-1 |
| AI17-UNIT-001 | Given FakeIndexTable.scan result injected with `LastEvaluatedKey` (wrap the real scan), when search-query handles a request, then WARNING record containing "truncated" is emitted, response is 200 with that page's projected+sorted items, and `scan_calls == 1` | P1 | AC-3 |
| AI17-UNIT-002 | Given normal single-page result, when search-query handles a request, then no truncation warning record exists | P1 | AC-4 |
| AI17-UNIT-003 | Given FakeHistoryTable.scan result injected with `LastEvaluatedKey` (videoId known so the 404 gate passes), when history-query handles a request, then same contract as AI17-UNIT-001 with entries shape preserved; `scan_calls == 1` proves gate ordering | P1 | AC-3 |
| AI17-UNIT-004 | Given normal single-page result, when history-query handles a request, then no truncation warning record exists | P1 | AC-4 |

Pin requirements (R3/R4 mitigations): assert records from the handler module's own logger via `caplog.at_level(logging.WARNING)` + message-content match on "truncated"; include the no-warn control so always-warn implementations fail; never stub `scan` wholesale — wrap it so expression-shape evaluation still runs.

## Design Notes

Warning placement mirrors rebuild exactly: immediately after the `scan()` call, before projection/sort, so the count logged is the number of FilterExpression-matching items returned in the first page (post-filter), consistent with `len(resp.get("Items", []))`. Use `%d` lazy formatting consistent with surrounding log calls. In the truncation pins, wrap (do not replace) the fake's real `scan` so expression-shape evaluation still runs — same technique as `test_truncated_scan_warns_loudly_still_single_scan`.

Human-approved sequencing (ATDD): write each failing pin first and observe RED (handler warning absent; guard extension RED proven by temporarily injecting an address-form reference into a scratch other-file `.tf`, reverted before commit), then implement the handler/guard change to GREEN.

## Verification

**Commands:**
- `uv run --with ruff ruff check lambdas/ --select E,F` -- expected: no violations
- `(cd terraform && terraform fmt -check -recursive)` -- expected: no formatting diffs
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: all pass incl. 3 new/extended tests (2 pins + guard)
- `gitleaks detect --no-banner --config .gitleaks.toml` -- expected: clean
- `bash scripts/ci-local.sh` -- expected: all stages green (integration needs Docker+floci; else document ruff+pytest+gitleaks floor)

**Manual checks (if no CLI):**
- None required beyond commands above.

## Spec Change Log

- **2026-08-26 — step-04 review findings** (count-semantics mislabel + verification-list gap).
  - Amended: Design Notes count wording corrected (logged count is the first-page post-filter item count, `len(resp.get("Items", []))`, not the raw page size); Verification gains `(cd terraform && terraform fmt -check -recursive)`; Test Scenarios gain AI15-UNIT-003 — a durable in-suite address-form guard probe pin.
  - Known-bad state avoided: proof-of-detection resting on unreproducible one-time manual RED evidence, and misdocumented count semantics.
  - KEEP: wrap-the-real-fake technique; no-warn controls; ATDD RED→GREEN sequencing.

## Suggested Review Order

**FR-19 guard closure (AI-15) — the underscore hole**

- Entry point: both literal forms now checked per other-file body; failure names file + form
  [	est_terraform_admin_only.py:108](../../lambdas/search_rebuild/tests/test_terraform_admin_only.py#L108)

- Durable positive-direction pin: scratch probe proves detection fires, self-cleaning
  [	est_terraform_admin_only.py:114](../../lambdas/search_rebuild/tests/test_terraform_admin_only.py#L114)

- Contract wording for check (b) updated to name both reference forms
  [	est_terraform_admin_only.py:14](../../lambdas/search_rebuild/tests/test_terraform_admin_only.py#L14)

**Truncation observability (AI-17) — parity with rebuild**

- search-query warns post-scan, pre-projection; count is first-page post-filter items
  [handler.py:78](../../lambdas/search_query/handler.py#L78)

- history-query mirrors the idiom after its own scan (past the 404 gate)
  [handler.py:85](../../lambdas/history_query/handler.py#L85)

**Truncation pins — vacuous-pass defenses**

- Truncated pin: wrap-the-real-fake, logger-name + WARNING-level + "after 2 items" count asserted, single call
  [	est_search_query.py:391](../../lambdas/search_query/tests/test_search_query.py#L391)

- No-warn control kills always-warn implementations
  [	est_search_query.py:408](../../lambdas/search_query/tests/test_search_query.py#L408)

- History twin with entries-shape and gate-ordering proof
  [	est_history_query.py:398](../../lambdas/history_query/tests/test_history_query.py#L398)

**Tracking closure**

- Retro items 15/17 flipped done after gates green
  [sprint-status.yaml:171](../implementation-artifacts/sprint-status.yaml#L171)

- Change-log entry records review amendments + KEEP instructions
  [spec-ai-15-ai-17-retro-guard-truncation.md -- Spec Change Log](spec-ai-15-ai-17-retro-guard-truncation.md)
