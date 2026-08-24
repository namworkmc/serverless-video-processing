---
title: 'Story 4.1 — Search Consumer: Indexing Processed Videos'
type: 'feature'
created: '2026-08-23'
status: 'done'
review_loop_iteration: 1
baseline_commit: 'd900264c60b4efe8022cbbf6074af540bb5ca341'
context:
  - '{project-root}/_bmad-output/test-artifacts/atdd-checklist-4-1-search-consumer-indexing-processed-videos.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Processed videos are not searchable — Epic 4's third client journey (title search, Story 4.2) has no index to read.

**Approach:** A `search-consumer` Lambda behind its own `search-queue` consumes `video.processed` events via a NEW EventBridge rule, and upserts one `search-index` entry per PROCESSED event: `{videoId, title, processedKey, indexedAt}`. Title comes from the `video-metadata` record fetched during poison validation. RED suite first (from the ATDD checklist), then GREEN, then Terraform wiring, then live verification on floci.

## Boundaries & Constraints

**Always:**
- Status filter (`status == PROCESSED`) runs BEFORE the metadata lookup — non-PROCESSED events never touch a table.
- Upsert is a plain `PutItem` keyed by `videoId` — the PK IS the dedupe (NFR-1). No ConditionExpression (do NOT port the history consumer's conditional write).
- Unknown videoId (NotFoundError from metadata) → dropped + acked; any other metadata/write error → raise (ESM retries). FR-15 semantics.
- Config-not-code: `METADATA_TABLE`, `SEARCH_INDEX_TABLE`, `AWS_ENDPOINT_URL` from Terraform env vars only.
- Consumer builds ONLY DynamoDB table handles (purity probe T13).
- RED before GREEN: full T1–T14 suite written and failing before the handler exists.
- New rule `video-processed-to-search` targeting only the search queue (AD-1 as-built precedent: new consumer = new queue + new rule). Existing rules byte-unchanged.
- Unknown status strings (e.g. `ARCHIVED`) count as `filtered`, not `skipped` — it is a status decision, made before any legality concern (pins checklist T10).

**Ask First:**
- Any deviation from the ATDD checklist's I/O matrix or fixture discipline.
- Any change to existing Terraform resources (history leg, trigger leg, capture rule).

**Never:**
- No title from the event detail (it carries none — AD-6); title strictly from the metadata record.
- No indexing of FAILED/UPLOADED/PROCESSING events.
- No `aws` CLI for provisioning — Terraform only.
- No new dependencies; no DLQ; no GSI.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy index | PROCESSED flat-detail SQS record, known videoId | put_item exactly `{videoId, title, processedKey, indexedAt}`, no ConditionExpression; `indexed=1`; title == metadata title | N/A |
| Status filter | detail `status` != PROCESSED (FAILED/UPLOADED/PROCESSING/unknown) | no put_item, no metadata get_item; `filtered=1` | N/A |
| Redelivery | same event twice | one item, fields identical, both calls `indexed=1` | N/A |
| Poison | PROCESSED, metadata NotFoundError | no put_item; `dropped=1`; no raise | acked |
| Transient metadata error | get_item raises other | raise; no put_item | ESM retries |
| Transient write error | put_item raises | raise | ESM retries |
| Stringified detail | detail is JSON string | parsed, identical to happy | N/A |
| Malformed record | body not JSON / no detail / missing or empty or non-string eventId, videoId, status, processedKey | `skipped=1`, acked, no writes, no raise | logged |
| Non-SQS event | not a dict / Records missing or not list | raises MalformedInputError | N/A |
| Mixed batch | indexed + filtered + dropped + skipped | per-record outcomes tallied, in order | N/A |

</frozen-after-approval>

## Code Map

- `lambdas/history_consumer/handler.py` — sibling consumer to mirror: module-level `_metadata_table()` accessors (monkeypatch targets), per-record outcome + summary shape. Differences: status filter before lookup, plain PutItem, `filtered` counter. Do NOT copy its `_parse_detail` — import `shared.events.parse_detail` instead (retro AI-4 consolidation; the shim purity-probe pattern permits an unwrap-only `shared.events` import).
- `lambdas/history_consumer/tests/test_history_consumer.py` + `tests/conftest.py` — test harness template: sys.path + `shared` package alias, fakes, `_flat_detail()` fixture recipe (`build_envelope` + `{**envelope, **envelope["detail"]}`), purity recorder, wire-shape coupling test (T14 pattern at `TestProducerWireShapeCoupling`).
- `lambdas/_shared/events.py` — `build_envelope`/`processed_detail`; rejects non-PROCESSED status for `EVENT_PROCESSED` → FAILED fixtures must be hand-crafted flat details.
- `lambdas/_shared/status.py` — `get_record` (NotFoundError on unknown videoId), `_now_iso()` for `indexedAt`.
- `lambdas/_shared/errors.py` — `MalformedInputError`, `NotFoundError`.
- `lambdas/_shared/clients.py` — `dynamodb_table(name)`.
- `lambdas/event_publisher/handler.py` — producer wire shape for T14 (flat Detail via `put_events`).
- `terraform/history.tf` — wiring template to clone: table, queue (visibility 300s), queue policy scoped to rule ARN, rule+target, zip source blocks (hand-maintained `shared/` set), IAM (logs + GetItem metadata + PutItem index + SQS trio), Lambda env, ESM batch_size=1, outputs.
- `terraform/locals.tf` — `local.lambda_endpoint_url`.
- `terraform/integration.tf:10` — `aws_dynamodb_table.video_metadata` (reuse, don't redeclare).
- `scripts/ci-local.sh` — gate; pins `COMPOSE_PROJECT_NAME=serverless-video-processing` (worktree caveat).

## Tasks & Acceptance

**Execution:**
- [x] `lambdas/search_consumer/tests/conftest.py` — create: copy history_consumer conftest (sys.path + shared alias).
- [x] `lambdas/search_consumer/tests/test_search_consumer.py` — create RED suite: checklist T1–T14 (happy, FAILED filter, non-terminal filter, redelivery overwrite, poison, transient metadata, transient write, stringified detail, malformed parametrize, unknown-status filtered, non-SQS, mixed batch, purity probe, producer wire-shape coupling) + config-not-code accessor tests. Run → fails on import.
- [x] `lambdas/search_consumer/__init__.py` + `lambdas/search_consumer/handler.py` — create: minimal GREEN implementation per I/O matrix; summary `{processed, indexed, filtered, dropped, skipped}`.
- [x] `terraform/search.tf` — create: checklist X1–X9 (table, queue, policy, new rule+target, zip, IAM, Lambda env, ESM, outputs). X5: existing .tf files untouched.
- [x] Live verification L1–L7 on floci (checklist): apply diff clean, upload→index entry, republish→still one, hand-crafted FAILED→no entry, history leg regression intact, consumer logs show indexed/filtered lines.
- [x] Update checklist boxes + sprint-status `4-1-search-consumer-indexing-processed-videos` → done evidence.

**Acceptance Criteria:**
- Given terraform apply, when state is listed, then only the new search resources exist and history/trigger/capture resources are unchanged.
- Given a video processed to PROCESSED via the upload journey, when the consumer runs, then search-index has exactly one entry `{videoId, title, processedKey, indexedAt}` with title matching the metadata record.
- Given a hand-crafted `video.processed` event with `status=FAILED`, when published, then no search-index entry exists for that videoId.
- Given the same PROCESSED event republished, when the consumer processes it again, then still exactly one entry, fields unchanged.
- Given the full suite, when `bash scripts/ci-local.sh` runs, then all 5 stages green.

## Spec Change Log

- 2026-08-24 (resume, human-directed corrections honored over stale Code Map wording):
  1. Routing is ONE RULE PER CONSUMER: NEW rule `video-processed-to-search` targets ONLY the search queue (the frozen "Always" bullet was already correct; epics.md + spine AD-1 agree). Never add a target to an existing rule.
  2. Do NOT copy `_parse_detail` a third time — import `shared.events.parse_detail` (landed on main via retro AI-4; unwrap-only shared.events import passes the purity probe).
  3. `bruno/environments/Local.bru` keeps the `REPLACE_WITH_API_ID` placeholder — do not bake in a concrete apiId.
  4. Live-state reality: the post-retro Terraform state is EMPTY and any previously running floci predates this branch — the first apply from this worktree is a full clean bring-up (docker compose up → terraform apply → exercise). "Existing resources unchanged" (AC1/X5) is proven by git showing no existing `.tf` file modified plus a green apply, not by a populated-state plan diff. If port 4566 is occupied by a stale compose project, remove that container first; ci-local.sh pins `COMPOSE_PROJECT_NAME=serverless-video-processing`.

- 2026-08-24 (review loop 1, human-resolved): On redelivery of an identical PROCESSED event, `indexedAt` REFRESHES — the plain-PutItem upsert stands; the frozen matrix/AC4 phrase "fields identical/unchanged" is interpreted as the DOMAIN fields (`videoId`/`title`/`processedKey`), now pinned by an explicit two-timestamp unit assertion (T4). Additional review-driven hardening, consistent with FR-15 and the no-DLQ deterministic-poison philosophy: a PROCESSED event whose metadata record lacks a usable string `title` is dropped+acked like an unknown videoId instead of KeyError-retrying forever; SQS-body parsing also tolerates `RecursionError`. Checklist verification-method wording synced to entry 4 above (git-diff X5 proof); sprint-status moved to `review` per convention.

## Verification

**Commands:**
- `uv run --with ruff ruff check lambdas/ --select E,F` -- expected: no errors
- `uv run --with 'pytest>=8.0' pytest lambdas/ -q` -- expected: all pass (new T1–T14 + all existing)
- `(cd terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false && terraform validate)` -- expected: green
- `bash scripts/ci-local.sh` -- expected: 5 stages green
- `(cd terraform && terraform apply -input=false)` -- expected: green apply; from the empty post-retro state this is a full clean bring-up (see Spec Change Log) — prove X5 via `git diff main -- terraform/` (only search.tf added); then live checks L1–L7 per checklist

## Suggested Review Order

**Consumer contract**

- Entry point: batch loop + summary shape `{processed, indexed, filtered, dropped, skipped}`
  [`handler.py:170`](../../lambdas/search_consumer/handler.py#L170)

- Per-record pipeline; outcome vocabulary indexed/filtered/dropped/skipped
  [`handler.py:85`](../../lambdas/search_consumer/handler.py#L85)

**Status filter before any table access**

- Filter is `status == PROCESSED`; unknown strings are a status decision → filtered
  [`handler.py:118`](../../lambdas/search_consumer/handler.py#L118)

**Poison handling & title provenance**

- Unknown videoId = successful negative lookup → dropped + acked (FR-15)
  [`handler.py:132`](../../lambdas/search_consumer/handler.py#L132)

- Titleless/unusable metadata title = deterministic poison → dropped (review loop 1)
  [`handler.py:141`](../../lambdas/search_consumer/handler.py#L141)

- Title strictly from the metadata record (AD-6); plain PutItem = the dedupe (NFR-1)
  [`handler.py:151`](../../lambdas/search_consumer/handler.py#L151)

**Config-not-code**

- Monkeypatch-target accessors; env-var-only table names
  [`handler.py:67`](../../lambdas/search_consumer/handler.py#L67)

**Wiring (one rule per consumer)**

- NEW rule `video-processed-to-search`, only target = search queue
  [`search.tf:70`](../../terraform/search.tf#L70)

- Hand-maintained zip source blocks — the known failure class this leg guards by live test
  [`search.tf:87`](../../terraform/search.tf#L87)

- Least privilege: GetItem metadata / PutItem index / SQS trio on one queue
  [`search.tf:140`](../../terraform/search.tf#L140)

- ESM batch_size=1 is load-bearing for raise-to-retry semantics
  [`search.tf:204`](../../terraform/search.tf#L204)

**Tests (matrix → evidence)**

- Happy index pins entry shape + no ConditionExpression
  [`test_search_consumer.py:214`](../../lambdas/search_consumer/tests/test_search_consumer.py#L214)

- Redelivery with two frozen timestamps: domain fields stable, indexedAt refreshes
  [`test_search_consumer.py:311`](../../lambdas/search_consumer/tests/test_search_consumer.py#L311)

- Unusable-title poison parametrize (new in review loop 1)
  [`test_search_consumer.py:372`](../../lambdas/search_consumer/tests/test_search_consumer.py#L372)

- Purity probe + producer→consumer wire-shape coupling
  [`test_search_consumer.py:572`](../../lambdas/search_consumer/tests/test_search_consumer.py#L572)
