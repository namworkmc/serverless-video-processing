---
workflowType: 'testarch-atdd'
storyId: '4.2'
storyKey: '4-2-title-search-through-the-gateway'
storyFile: '_bmad-output/implementation-artifacts/spec-4-2-title-search-through-the-gateway.md'
atddChecklistPath: '_bmad-output/test-artifacts/atdd-checklist-4-2-title-search-through-the-gateway.md'
generatedTestFiles:
  - 'lambdas/search_query/tests/conftest.py'
  - 'lambdas/search_query/tests/test_search_query.py'
inputDocuments:
  - '_bmad-output/implementation-artifacts/spec-4-2-title-search-through-the-gateway.md'
  - '_bmad-output/implementation-artifacts/epic-4-context.md'
  - '_bmad-output/test-artifacts/atdd-checklist-4-1-search-consumer-indexing-processed-videos.md'
  - '_bmad-output/test-artifacts/atdd-checklist-3-2-history-query-through-the-gateway.md'
  - 'lambdas/history_query/handler.py (sibling QUERY-lambda template)'
  - 'lambdas/history_query/tests/ (RED-suite harness template)'
  - 'lambdas/_shared/errors.py, clients.py'
  - 'terraform/search.tf (search-index table — referenced, never redeclared)'
  - 'terraform/history.tf (gateway-wiring template)'
primaryLevel: 'unit (pytest) + live floci integration'
---

# ATDD Checklist — Epic 4, Story 4.2: Title Search Through the Gateway

**Date:** 2026-08-24
**Author:** Murat (Test Architect) for Kygor
**Primary Test Level:** Unit (pytest with fakes) — live floci verification + CI mirror backstop

## Story Summary

A `search-query` Lambda behind the EXISTING gateway route `GET /videos/search?title=` answers title-substring searches over `SEARCH_INDEX_TABLE`: plain DynamoDB Scan + `contains(title, :t)` FilterExpression (case-sensitive; lab scale per AD-3/NFR-7 — no GSI, no pagination). Match → 200 `{"title": <stripped query>, "results": [...]}` with each entry exactly `{videoId, title, processedKey, indexedAt}` sorted by videoId ascending; no match → 200 with an empty list (never an error); missing/empty/whitespace/non-string `title` → MalformedInputError → map_error → 400 `{"error": ...}` BEFORE any scan. Request-invoked only — no queue, rule, or event-source mapping. Terraform wiring in ONE new file joining the existing gateway/stage/table by reference.

## Design Notes

- **Matrix example literal vs case-sensitive constraint.** The I/O matrix's substring-semantics row uses query `"anch"` against `"Anchor A"` / `"Enchanted"` — but case-sensitive contains matches NEITHER (`"Anch"`, `"ench"`). The frozen intent block pins case-sensitive matching and forbids case-insensitivity (Ask First), so the test encodes the ROW'S SEMANTICS (both containing titles match, `"Other"` excluded) with a needle that actually satisfies them case-sensitively: `"nch"` ⊂ both `"Anchor A"` and `"Enchanted"`. A separate test pins case-sensitivity itself (`"anchor"` does not match `"Anchor A"`).
- **FakeTable must evaluate `contains`, not equality** (spec Design Notes): a handler that swaps in `title = :t` must FAIL the semantics tests, so the fake full-matches `contains(attr, :ph)` and raises AssertionError on any other FilterExpression shape.
- **No 404 gate, no metadata table** — search never consults `video-metadata`; there is no videoId in play. Purity probe asserts ONLY dynamodb handles are built (no s3/events/states/sqs).
- **Response body pinned**: exactly `{"title", "results"}`; entry projection exactly `{videoId, title, processedKey, indexedAt}` — internal fields cannot leak.
- **Deterministic order**: results sorted by videoId ascending regardless of Scan order — order-stable assertions for Bruno/live.
- **Terraform state caveat (session-specific)**: copy the MAIN checkout's `terraform/terraform.tfstate*` (+ `.terraform/`) into the worktree BEFORE any plan/apply or apply would try to recreate the whole populated stack.

## Risk Assessment

| Risk | Impact | Likelihood | Test response |
|------|--------|-----------|---------------|
| Equality filter instead of `contains` | High (exact-match search silently returns junk-empty results) | Medium (history sibling uses `videoId = :vid` — obvious template to mis-port) | Unit T2: fake evaluates SUBSTRING containment; equality handler fails |
| Case-insensitive creep | Medium (violates frozen intent; Ask First territory) | Low | Unit T2b: lowercase query misses capitalized title |
| No-match surfaced as error (404/500) | High (breaks FR-18 contract + Bruno flow) | Medium (history sibling HAS a 404 gate — easy mis-port) | Unit T3: unknown substring → 200 `[]`, scan succeeded |
| Bad input triggers a scan (NFR-3 violation) | Medium | Low | Unit T5: parametrized bad inputs assert `scan_calls == []` |
| Sort order unstable across items | Low (live/Bruno assertion flake) | Medium | Unit T4: seeded out of order → videoId ascending |
| Internal fields leak into entries | Medium | Low | Unit T1c: seeded extra attribute excluded from response |
| Terraform wiring gap (zip blocks/route/permission) | High (route dead) | Medium (hand-maintained zip source blocks are a known failure class) | X-range checklist + live L3–L6 |
| Existing wiring disturbed / whole-stack recreate | High (regresses Epics 1–4.1; state loss) | Medium (worktree has NO tfstate by default) | X6: `git diff main -- terraform/` shows only the new file; L2 preceded by state copy from main checkout |

## Unit Test Checklist (RED phase — `lambdas/search_query/tests/test_search_query.py`)

Fakes: `FakeIndexTable` (scan EVALUATES `contains(attr, :ph)` against ExpressionAttributeValues — substring, not equality; raises AssertionError on any other expression; records every scan call). `ClientFactoryRecorder` purity probe. Monkeypatch module accessor `_index_table()` (project convention).

I/O matrix coverage (one class per row minimum):

- [x] T1 happy match: indexed entry + `title=<substring>` → 200, body keys exactly `{title, results}`, `title` is the STRIPPED query, Content-Type json, structured log carries result count
- [x] T2 substring semantics: query `"nch"` vs `"Anchor A"`/`"Enchanted"`/`"Other"` → the two containing titles match, Other excluded (see Design Notes re matrix-literal deviation); case-sensitivity pinned separately; filter value bound to the stripped query
- [x] T3 no match: unknown substring → 200 `"results": []` — NOT an error (empty index too)
- [x] T4 multiple matches: several videos share the substring → all returned, videoId ascending regardless of seed order
- [x] T5 missing/empty title (parametrize: None event / non-dict / `{}` / queryStringParameters None / {} / other-key-only / `""` / whitespace / non-string) → 400 `{"error": ...}` via MalformedInputError; NO scan issued
- [x] T6 transient scan error → 500 `{"error": ...}` via map_error
- [x] T7 purity probe: client-recorder — only a `dynamodb` handle ever constructed; never s3/events/states/sqs (patches only `h.clients`, real accessors run)
- [x] T8 config-not-code: missing `SEARCH_INDEX_TABLE` env → RuntimeError from the real `_index_table()`

Fixture discipline: index items shaped exactly as Story 4.1 writes them (`{videoId, title, processedKey, indexedAt}`); gateway events use API GW v2 payload format 2.0 with `queryStringParameters`.

## Terraform Checklist (`terraform/search-query.tf` — ONE new file)

- [x] X1 zip source blocks: `shared/` (all FIVE modules — status/events/errors/clients/__init__) + `search_query/` package, hand-maintained set complete
- [x] X2 IAM: logs (`Resource="*"` parity) + `dynamodb:Scan` on `aws_dynamodb_table.search_index.arn` ONLY — nothing else
- [x] X3 env: `SEARCH_INDEX_TABLE` (= search_index.name by reference) + `AWS_ENDPOINT_URL` (= local.lambda_endpoint_url) — no names in code
- [x] X4 Lambda: python3.11, handler string `search_query.handler.handler`, timeout 30, memory 128
- [x] X5 gateway trio on the EXISTING api/stage by reference: AWS_PROXY integration payload 2.0, route key `GET /videos/search`, lambda permission `source_arn` scoped `${execution_arn}/${stage}/GET/videos/search`
- [x] X6 EVERY pre-existing `.tf` byte-unchanged — `git diff main -- terraform/` shows ONLY the new file added; no queue/rule/ESM anywhere in the new file
- [x] X7 output `search_query_function`; `terraform fmt -check -recursive` + `terraform validate` green

## Live Verification Checklist (floci)

- [x] L1 floci healthy (`curl -sf http://localhost:4566/_localstack/health`)
- [x] L2 worktree state seeded from main checkout BEFORE plan/apply; `terraform apply` green adding only search-query resources; no churn on existing resources
- [x] L3 upload via gateway → PROCESSED → `GET /videos/search?title=<substring>` → 200 with that videoId in `results`, title from the index
- [x] L4 unknown substring → 200 `{"title": ..., "results": []}`
- [x] L5 missing title param → 400 `{"error": ...}` through the gateway unchanged
- [x] L6 history route regression: `GET /videos/{videoId}/history` still works

## Bruno Checklist (implementation phase)

- [x] B1 `bruno/search-video.bru`: GET `{{gatewayBaseUrl}}/videos/search?title=...` chaining after Upload Video's `{{videoId}}`; pre-request poll-with-timeout (2 s interval, 120 s deadline) until a result carrying `{{videoId}}` appears; assert block validates the final request; env file keeps `REPLACE_WITH_API_ID`

## Gate

- [x] G1 `uv run --with ruff ruff check lambdas/ --select E,F`
- [x] G2 `uv run --with 'pytest>=8.0' pytest lambdas/ -q` — RED failed first, then all new + all existing pass
- [x] G3 `bash scripts/ci-local.sh` — 5 stages green (secrets-scan → lint → unit-test → tf-validate → integration 13/13)

## Red-Green Workflow

1. RED: mint this checklist → write conftest + full T1–T8 suite → suite fails on import (handler absent) — evidence recorded below
2. GREEN: implement `search_query/handler.py` minimally until T1–T8 pass
3. Terraform: `search-query.tf` (new file only) → fmt/validate → apply (state seeded first) → L1–L6
4. Bruno: B1
5. Gate G1–G3, then mark checklist items with evidence (counts, key lines)

## RED Phase Evidence (2026-08-24)

- `pytest lambdas/search_query/ -q` → `ModuleNotFoundError: No module named 'search_query.handler'` (collection error — RED, same shape as Stories 3.1/3.2)
- `ruff check lambdas/search_query/ --select E,F` → All checks passed! (RED suite itself lint-clean)

## Build Evidence (bmad-build, 2026-08-24)

- **Files:** `lambdas/search_query/{__init__,handler}.py` + `tests/{conftest,test_search_query}.py`, `terraform/search-query.tf` (ONLY new `.tf`), `bruno/search-video.bru`.
- **G2:** RED first (`ModuleNotFoundError` above), then GREEN: `pytest lambdas/ -q` → **354 passed** (30 new search-query cases; one test-fixture bug fixed during GREEN — seeded title "Hatchet" lacked the `nch` needle, replaced with "Lunch Clip"; no handler change needed). Review run 1 tightened the suite (+2 cases): **356 passed**.
- **X6 proof:** `git diff main --stat -- terraform/` → empty; `git status terraform/` → only untracked `search-query.tf`. Post-gate `terraform plan -detailed-exitcode` → exit 0, no drift.
- **L2:** worktree state seeded from the MAIN checkout (`terraform.tfstate*` + `.terraform/`) BEFORE any plan/apply per spec Design Notes; `terraform apply` (via ci-local stage 5) green adding only search-query resources.
- **L3:** upload `ed156de7-c793-4712-a02e-6ef01f012569` ("My Video") → PROCESSED → `GET /videos/search?title=My Video` → 200 with that videoId in `results`, entry exactly `{videoId, title, processedKey, indexedAt}`, title from the index.
- **L4:** `title=zzz-no-such-title` → 200 `{"title": ..., "results": []}`.
- **L5:** absent / empty / whitespace-only title → 400 `{"error": "missing or empty query parameter: title"}` through the gateway unchanged.
- **L6:** history route regression green — same upload produced its PROCESSED entry via `GET /videos/{videoId}/history`.

## Review Run 1 Evidence (2026-08-24)

Code-review patches P1–P3 substance applied (P4/P5 are the spec/checklist bookkeeping you are reading):

- **P1 — automated integration coverage for the route** (the verification-gap finding): `tests/integration/conftest.py` gained `SEARCH_INDEX_TABLE`, `Stack.search_entries(video_id)` (direct-table oracle mirroring `history_entries`), and search-index deletion in `cleanup_video`; new `tests/integration/test_search_query.py` with 4 tests: happy journey (upload → `wait_status` PROCESSED → poll index → gateway GET asserts body title echo, exactly-one result, exact `{videoId, title, processedKey, indexedAt}` keys, values equal oracle row), TRUE substring at the boundary (`title=gration Fix` ⊂ "Search Integration Fixture"), no-match 200 + empty list + query echo, missing-param 400 with single-key `{"error"}`.
- **P2 — tightened unit suite** (`lambdas/search_query/tests/test_search_query.py`): log assertion now `"results=1" in r.getMessage()` (was weak `"1" in r.message`); representative 400 case AND the transient-500 case assert `set(body) == {"error"}` + json Content-Type; new config-not-code case — `AWS_ENDPOINT_URL` deleted with `SEARCH_INDEX_TABLE` set → handler returns mapped 500 `{"error"}` (real accessors run; resource cache cleared defensively).
- **P3 — Bruno hardening**: `upload-video.bru` now also sets `videoTitle` ("My Video") at the source; `search-video.bru` reads `bru.getVar("videoTitle") || "My Video"` and builds its poll URL from it (no hardcoded title), plus a `script:post-response` block that throws unless the FINAL asserted response is 200 and carries `{{videoId}}` in results; docs updated for the chaining.

Verification run after patching: `ruff check lambdas/ --select E,F` green; `pytest lambdas/ -q` green (unit count in Build Evidence updated); integration stage deliberately NOT re-run in this pass per review instructions (ci-local stage 5 exercises the new file against live floci later).

## Deviations

- Matrix row 2's literal query `"anch"` replaced by `"nch"` (see Design Notes): the frozen case-sensitive constraint wins over the example literal; the row's observable semantics (both containing titles match, Other excluded) are preserved and case-sensitivity is pinned explicitly.
- ~~No separate `tests/integration/` files for this story: the spec's task list scopes integration coverage to live curl verification + the Bruno collection (the history-query story's dedicated integration tests covered the shared AWS_PROXY plumbing already exercised again here end-to-end).~~ SUPERSEDED by step-04 review (run 1): that scoping left the route's wiring with zero automated pipeline coverage (a renamed route key / dropped permission / missing env var would ship green-broken), so the search route now HAS repeatable automated coverage — `tests/integration/test_search_query.py` (+ conftest search-index helpers) runs in ci-local stage 5 against the deployed gateway; the original rationale is kept visible above.

## Review Run 1 Addendum — Live Substring Evidence (2026-08-24)

- BH12 closure: partial needle `title=y%20Vid` against the deployed gateway -> 200 with the uploaded video in `results` (TRUE contains() semantics, not full-literal); case-miss `title=my%20vid` -> 200 `{"results": []}` (case-sensitivity pinned live). Complements L3.
