---
title: 'Story 4.2 — Title Search Through the Gateway'
type: 'feature'
created: '2026-08-24'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'f5397c1d395241a2caa2d411911feeb53fa1e52b'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Indexed processed videos cannot be searched — Story 4.1 built the `search-index` table, but no client-facing surface reads it, so the third and final client journey (FR-18) is incomplete.

**Approach:** A `search-query` Lambda behind the existing gateway route `GET /videos/search?title=` answers title-substring searches with a DynamoDB Scan + `contains` filter over `SEARCH_INDEX_TABLE` (lab scale, NFR-7, no GSI): HTTP 200 + results on match, HTTP 200 + empty list on no match, HTTP 400 `{"error": ...}` on a missing or empty `title` parameter. RED suite first (ATDD checklist convention), GREEN implementation, Terraform wiring in a NEW file only, live verification on floci.

## Boundaries & Constraints

**Always:**
- RED before GREEN: mint `atdd-checklist-4-2` (following the `atdd-checklist-4-1` / `atdd-checklist-3-2` conventions) and write the full failing unit suite before the handler exists.
- Match semantics: case-sensitive substring via `contains(title, :t)` FilterExpression on a plain table Scan — sanctioned at lab scale (AD-3/NFR-7); NO GSI.
- No-match is success: unknown substring → HTTP 200 with an empty results list, never an error.
- Bad input: missing, empty, whitespace-only, or non-string `title` → `MalformedInputError` → `map_error` → HTTP 400 `{"error": ...}`; no scan performed (NFR-3).
- Config-not-code: `SEARCH_INDEX_TABLE` and `AWS_ENDPOINT_URL` come from Terraform-set env vars only; module-level `_index_table()` accessor is the monkeypatch target (project convention).
- Reuse `lambdas/_shared`: `from shared import clients, errors`; purity — this Lambda builds ONLY DynamoDB table handles.
- Terraform wiring lives in ONE new file (`terraform/search-query.tf`); every existing `.tf` file stays byte-unchanged (prove via `git diff main -- terraform/`). The integration/route join the EXISTING `aws_apigatewayv2_api.gateway`, stage, and `aws_dynamodb_table.search_index` by reference — nothing redeclared.
- Gateway route key is `GET /videos/search` (API GW v2 route keys carry no query string); `title` arrives via `queryStringParameters`. The lambda permission `source_arn` is scoped to that exact route.
- `bruno/environments/Local.bru` keeps the `REPLACE_WITH_API_ID` placeholder — never bake in a concrete apiId; apiId comes from `terraform output api_id` at exercise time.

**Ask First:**
- Any modification to an existing `.tf` file (breaks the byte-unchanged proof).
- Case-insensitive matching or any title normalization beyond stripping the query parameter (not an AC).
- Growing the Bruno collection by more than the one search request.

**Never:**
- No queue, EventBridge rule, or event-source mapping for this Lambda — it is request-invoked behind the gateway; the one-rule-per-consumer rule does NOT apply here and adding a rule would be wrong.
- No 404/metadata gate — search never consults `video-metadata`; there is no videoId in play.
- No pagination plumbing (single Scan, NFR-7); no GSI; no DLQ; no new dependencies.
- No auth; no edge remapping — responses pass through the gateway unchanged (FR-21).
- No `aws` CLI for provisioning — Terraform only.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy match | `GET /videos/search?title=<substring>`, ≥1 indexed title contains it | 200 `{"title": <stripped query>, "results": [<entry>...]}`; each entry exactly `{videoId, title, processedKey, indexedAt}` | N/A |
| Substring semantics | query `"anch"` against indexed `"Anchor A"` / `"Enchanted"` / `"Other"` | matches both containing titles (case-sensitive contains), excludes `"Other"` | N/A |
| No match | substring hitting nothing | 200 with `"results": []` — NOT an error | N/A |
| Multiple matches | several videos share the substring | all returned, deterministic order (videoId ascending) | N/A |
| Missing/empty title | `queryStringParameters` absent / `{}` / `title=""` / `"   "` / non-string | 400 `{"error": ...}` via MalformedInputError; no scan issued | mapped 400 |
| Transient scan error | scan raises any non-mapping exception | 500 `{"error": ...}` via map_error | mapped 500 |

</frozen-after-approval>

## Code Map

- `lambdas/history_query/handler.py` -- sibling QUERY-lambda template to mirror structurally: docstring contract, module-level `_metadata_table()`-style accessor raising RuntimeError on unset env, single try/except with `map_error` tail. Differences for 4.2: read `event["queryStringParameters"]` (not pathParameters), NO 404 gate, scan filter is `contains(title, :t)`.
- `lambdas/history_query/tests/test_history_query.py` + `tests/conftest.py` -- RED-suite harness template: sys.path + `shared` alias conftest (copy as-is), `FakeHistoryTable.scan()` honoring FilterExpression (adapt to evaluate `contains(attr, :ph)`), `ClientFactoryRecorder` purity probe, `deps` fixture monkeypatching accessors + env.
- `lambdas/_shared/errors.py:22,68` -- `MalformedInputError` (http_status 400), `map_error(exc) -> (status, {"error": msg})`.
- `lambdas/_shared/clients.py:87` -- `dynamodb_table(name)`; endpoint from `AWS_ENDPOINT_URL`.
- `terraform/search.tf:23-33` -- `aws_dynamodb_table.search_index` (PK `videoId` S) — reference `aws_dynamodb_table.search_index.name/.arn`, never redeclare. Its env var name `SEARCH_INDEX_TABLE` is already the established config key.
- `terraform/history.tf:216-339` -- wiring template to clone into the new file: hand-maintained `archive_file` zip source blocks (`shared/__init__, status, events, errors, clients` — ALL FIVE, because `shared/__init__` imports them all — plus the function package), IAM role + policy (logs `Resource="*"` parity + least-privilege table action), `aws_lambda_function` (python3.11, timeout 30, memory 128), integration (AWS_PROXY, payload 2.0) / route / `aws_lambda_permission` trio joining the existing gateway + stage.
- `terraform/upload.tf:140-181` -- `aws_apigatewayv2_api.gateway`, `aws_apigatewayv2_stage.local`, `api_id` + `gateway_base_url` outputs — the resources the new file references.
- `terraform/locals.tf` -- `local.lambda_endpoint_url` for the Lambda env.
- `bruno/history-query.bru` -- poll-with-timeout template (pre-request script loop, 2s interval / 120s deadline, assert block) for the new `search-video.bru`; chains after Upload Video which sets the `videoId` collection var; upload sends fixed `title: My Video`, so searching a substring of "My Video" must surface the uploaded videoId.
- `scripts/ci-local.sh` -- validation gate (5 stages); pins `COMPOSE_PROJECT_NAME`.

## Tasks & Acceptance

**Execution:**
- [x] `_bmad-output/test-artifacts/atdd-checklist-4-2-title-search-through-the-gateway.md` -- mint during this workflow from the 4-1/3-2 checklist conventions: T-numbered unit list from the I/O matrix, X-range terraform checks, L-range live checks, gate section -- RED-phase contract.
- [x] `lambdas/search_query/tests/conftest.py` -- create: copy history_query conftest (sys.path + shared alias).
- [x] `lambdas/search_query/tests/test_search_query.py` -- create RED suite covering every I/O-matrix row + purity probe + config-not-code accessor tests; run → fails on import (handler absent) -- proves RED before GREEN.
- [x] `lambdas/search_query/__init__.py` + `lambdas/search_query/handler.py` -- create: minimal GREEN implementation per the matrix until the suite passes.
- [x] `terraform/search-query.tf` -- create (ONLY new .tf): zip blocks (5 shared modules + `search_query/`), IAM (logs + `dynamodb:Scan` on search-index ARN ONLY), Lambda env `SEARCH_INDEX_TABLE`/`AWS_ENDPOINT_URL`, AWS_PROXY integration + route `GET /videos/search` + permission scoped `${execution_arn}/${stage}/GET/videos/search`, output `search_query_function` -- FR-21 third route lands.
- [x] `bruno/search-video.bru` -- add: poll-with-timeout GET `{{gatewayBaseUrl}}/videos/search?title=<substring-of-My-Video>` asserting 200 + results contain `{{videoId}}` -- FR-22 grows by exactly one request.
- [x] `tests/integration/test_search_query.py` (+ conftest search-index helpers) -- create: happy / true-substring / no-match-200-empty / missing-param-400 through the deployed gateway -- route wiring now fails loudly in CI instead of shipping green-broken.
- [x] Live verification on floci (reuse running stack; copy main checkout's `terraform/terraform.tfstate*` into the worktree BEFORE any plan/apply) -- upload → PROCESSED → search returns the video by substring; no-match → 200 `[]`; missing-param → 400 `{"error"}`; history route still works (regression).
- [x] Checklist boxes + sprint-status `4-2-title-search-through-the-gateway` updated with evidence.

**Acceptance Criteria:**
- Given `terraform apply`, when the route table is inspected, then all three authoritative routes exist (FR-21 complete) and every pre-existing `.tf` file is byte-unchanged (`git diff main -- terraform/` shows only `search-query.tf` added).
- Given an indexed processed video (Story 4.1), when `GET /videos/search?title=<substring>` goes through the gateway, then HTTP 200 carries that video in `results` with its title from the index.
- Given a substring matching nothing, when searched, then HTTP 200 with an empty list (not an error).
- Given a missing or empty `title` parameter, when searched, then HTTP 400 with body `{"error": ...}`.
- Given the Bruno collection run after the upload journey, when the search request executes, then it passes against the gateway data-plane URL only and returns the uploaded video by title substring.
- Given the full suite, when `bash scripts/ci-local.sh` runs, then all 5 stages green.

## Spec Change Log

### 2026-08-24 — review run 1: verification-gap finding (step-04)

- **Finding:** the new gateway route shipped with ZERO automated pipeline coverage — every gate (unit, tf-validate, integration) stayed green even though nothing exercised the deployed `GET /videos/search` wiring. A known-bad state could pass all gates while search is dead: a renamed route key, a dropped lambda permission, or a missing env var would surface only in manual live checks.
- **Amended:** added `tests/integration/test_search_query.py` (+ `Stack.search_entries` oracle + search-index cleanup in `tests/integration/conftest.py`) picked up by ci-local stage 5; tightened unit asserts (log message via `getMessage()`, error bodies exactly `{"error"}` + json Content-Type on a 400 and the 500, AWS_ENDPOINT_URL-missing → mapped 500); hardened Bruno (`videoTitle` captured at upload, script-built URL from that var, post-response containment proof on the final asserted request); this log entry.
- **KEEP:** `FakeIndexTable` must evaluate real substring containment (a handler swapping in an equality filter fails); Bruno uses the poll-with-timeout pattern (no fixed sleeps); worktree state-copy-before-apply caveat stands (copy main checkout's `terraform/terraform.tfstate*` before any plan/apply).

- 2026-08-24 (build, human-ratified): The frozen substring-semantics row's example literal `title="anch"` against `"Anchor A"`/`"Enchanted"` was internally defective — under the same block's case-sensitive-contains constraint it matches neither title. Resolution (user-approved): keep the row's observable semantics (both containing titles match, `"Other"` excluded) with needle `"nch"`, and pin case-sensitivity explicitly in a separate test (`"anchor"` does not match `"Anchor A"`). The frozen block text is unchanged; this entry is the ratification record. KEEP: the FakeIndexTable evaluates true substring containment and rejects any other FilterExpression shape, so an equality handler fails loudly.

## Design Notes

- Response body pinned for testability: `{"title": "<stripped query>", "results": [...]}`, each result projected to exactly `{videoId, title, processedKey, indexedAt}` (the 4.1 entry shape — no internal fields), sorted by videoId ascending so live/Bruno assertions are order-stable.
- `contains(title, :t)` needs no attribute definition (title is not a key attribute); the FakeTable must evaluate `contains` (substring) rather than equality, so a handler that swaps in an equality filter fails RED tests.
- floci/HTTP-API precedence: exact literal segment `/videos/search` wins over the `/videos/{videoId}/history` parametrized route — no conflict with Story 3.2's route.
- State caveat (session-specific): Terraform state currently sits in the MAIN checkout's `terraform/terraform.tfstate*`; copy it into the worktree `terraform/` before any plan/apply or apply tries to recreate the whole populated stack.

## Verification

**Commands:**
- `uv run --with ruff ruff check lambdas/ --select E,F` -- expected: no errors
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: RED fails first, then all pass (new suite + all existing)
- `(cd terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate)` -- expected: green
- `git diff main -- terraform/` -- expected: EMPTY tracked diff (byte-unchanged proof); complemented by `git status terraform/` showing only the untracked `search-query.tf` pre-commit (git diff does not list untracked files)
- `bash scripts/ci-local.sh` -- expected: 5 stages green
- `(cd terraform && terraform apply -input=false)` -- expected: green apply adding only search-query resources
- `curl http://localhost:4566/_aws/execute-api/<apiId>/local/videos/search?title=...` (apiId from `terraform output api_id`) -- expected: 200 hit / 200 `[]` / 400 cases as per matrix

## Suggested Review Order

**Handler contract (entry point)**

- Validates `title` from queryStringParameters, strips once; 400 before any table access
  [`handler.py:50`](../../lambdas/search_query/handler.py#L50)

- Scan + `contains(title, :t)` — the sanctioned lab-scale read (AD-3/NFR-7, no GSI)
  [`handler.py:71`](../../lambdas/search_query/handler.py#L71)

- Projection to exactly the 4.1 entry shape + videoId-ascending sort; no-match is 200 `[]`
  [`handler.py:75`](../../lambdas/search_query/handler.py#L75)

- Entry point: try/everything with map_error tail — every failure surfaces as a mapped HTTP body
  [`handler.py:64`](../../lambdas/search_query/handler.py#L64)

**Wiring — third gateway route (new file only)**

- Route trio joins the EXISTING api/stage by reference; route key carries no query string
  [`search-query.tf:126`](../../terraform/search-query.tf#L126)

- Lambda permission scoped to the exact search route (exact literal beats `{videoId}` params)
  [`search-query.tf:132`](../../terraform/search-query.tf#L132)

- Least privilege: logs + `dynamodb:Scan` on search-index ARN ONLY
  [`search-query.tf:70`](../../terraform/search-query.tf#L70)

- Hand-maintained zip blocks: all FIVE shared modules + package (known failure class)
  [`search-query.tf:19`](../../terraform/search-query.tf#L19)

- Request-invoked by design: NO queue/rule/ESM anywhere in this file
  [`search-query.tf:97`](../../terraform/search-query.tf#L97)

**Unit suite (matrix → evidence)**

- FakeIndexTable EVALUATES contains() and rejects any other expression — equality handlers fail loudly
  [`test_search_query.py:43`](../../lambdas/search_query/tests/test_search_query.py#L43)

- Substring semantics incl. live-ratified case-sensitivity pin ("nch" needle; see Spec Change Log)
  [`test_search_query.py:216`](../../lambdas/search_query/tests/test_search_query.py#L216)

- Bad title parametrize asserts NO scan issued (NFR-3 ordering)
  [`test_search_query.py:281`](../../lambdas/search_query/tests/test_search_query.py#L281)

- Purity probe + config-not-code incl. missing-endpoint → mapped 500
  [`test_search_query.py:334`](../../lambdas/search_query/tests/test_search_query.py#L334)

**Integration coverage (review run 1 — closes the verification gap)**

- Happy journey through the DEPLOYED gateway with direct-table oracle equality
  [`test_search_query.py:26`](../../tests/integration/test_search_query.py#L26)

- True substring at the gateway boundary (partial needle, pins contains() end-to-end)
  [`test_search_query.py:55`](../../tests/integration/test_search_query.py#L55)

- No-match 200-empty + missing-param 400 through the deployed route
  [`test_search_query.py:74`](../../tests/integration/test_search_query.py#L74)

- Stack helpers: search-index oracle + cleanup parity
  [`conftest.py:341`](../../tests/integration/conftest.py#L341)

**Bruno (FR-22 grows)**

- Poll-with-timeout on chained videoTitle/videoId vars; post-response enforces containment on the asserted request
  [`search-video.bru:24`](../../bruno/search-video.bru#L24)
