---
workflowType: 'testarch-atdd'
storyId: '4.1'
storyKey: '4-1-search-consumer-indexing-processed-videos'
storyFile: '_bmad-output/planning-artifacts/epics.md#story-41-search-consumer--indexing-processed-videos'
primaryLevel: 'unit (pytest) + live floci integration'
stepsCompleted: ['step-01-preflight-and-context', 'step-02-generation-mode', 'step-03-test-strategy', 'step-04-generate-tests', 'step-05-validate-and-complete']
lastStep: 'step-05-validate-and-complete'
lastSaved: '2026-08-24'
atddChecklistPath: '_bmad-output/test-artifacts/atdd-checklist-4-1-search-consumer-indexing-processed-videos.md'
generatedTestFiles: []
inputDocuments:
  - '_bmad-output/planning-artifacts/epics.md (Story 4.1 ACs, FR-17, AD-1/AD-3/AD-6)'
  - '_bmad-output/implementation-artifacts/epic-3-context.md (queue-per-consumer wiring contract)'
  - '_bmad-output/test-artifacts/atdd-checklist-3-1-history-consumer-recording-terminal-events.md (sibling precedent)'
  - 'lambdas/history_consumer/handler.py (sibling consumer shape)'
  - 'lambdas/event_publisher/handler.py (flat wire Detail)'
  - 'lambdas/_shared/events.py, status.py, errors.py, clients.py'
  - 'terraform/history.tf (history-leg wiring template)'
---
# ATDD Checklist — Epic 4, Story 4.1: Search Consumer — Indexing Processed Videos

**Date:** 2026-08-23
**Author:** Murat (Test Architect) for Kygor
**Primary Test Level:** Unit (pytest with fakes) — integration backstop via live floci + CI mirror

## Story Summary

A `search-consumer` Lambda behind `search-queue` upserts one `search-index` entry per `video.processed` event with `status = PROCESSED`: unwrap SQS body → EventBridge detail, status filter (PROCESSED only — the story's new behavior vs the history consumer), validate `videoId` against `video-metadata` (the metadata record is also the source of `title`), plain `PutItem` keyed by `videoId` (upsert — the PK IS the dedupe; redelivery overwrites, NFR-1). Unknown videoId = poison (drop + ack); transient errors raise (SQS retries). Entry shape: `{videoId, title, processedKey, indexedAt}` (FR-17).

## Design Notes (resolve during build)

- **Rule wording vs as-built AD-1.** The AC says the search queue is "added alongside the history queue target". As-built precedent (Story 3.1, epic-3-context.md): a new consumer = new queue + **new rule** — the history leg created its own `video-processed-to-history` rule rather than adding a target to an existing one. Follow precedent: a NEW `video-processed-to-search` rule targeting only the search queue. The AC's observable outcome ("fans out to both queues, each consumer behind its own queue") holds either way; the spine rule "new consumer = new queue + new rule target" is satisfied by a new rule carrying the new target.
- **Title provenance.** The `video.processed` detail carries `{videoId, status, bucket, originalKey, processedKey}` — no title (AD-6). `title` comes from the `video-metadata` record fetched during poison validation — the GetItem already paid for.
- **Upsert, not conditional write.** Unlike the history consumer's `attribute_not_exists(eventId)` append, the search index is upsert-by-PK: plain `PutItem` with `Key = videoId`. Redelivery overwrites with the same entry — no duplicates, no ConditionalCheckFailed path. Do NOT port the history consumer's condition expression.
- **Status filter placement.** Filter on `status != PROCESSED` BEFORE the metadata lookup: non-PROCESSED events are filtered without any table access (cheaper, and a FAILED event for an unknown videoId is still filtered, not dropped).
- **FAILED events are hand-crafted.** `shared.events.build_envelope` rejects `EVENT_PROCESSED` with any status other than PROCESSED (AD-6 guard), so FAILED test events must be hand-typed flat details — the AC explicitly says "exercised by a hand-crafted test event". This is the one sanctioned deviation from the fixture discipline below.
- **Summary counters.** Sibling shape `{processed, recorded, deduped, dropped, skipped}` maps to `{processed, indexed, filtered, dropped, skipped}` — `filtered` replaces `deduped` (no conditional-write collision path exists here).

## Risk Assessment

| Risk | Impact | Likelihood | Test response |
|------|--------|-----------|---------------|
| Status filter wrong — FAILED video indexed | High (FR-17's core promise: FAILED never indexed) | Medium (first status-filtered consumer; history consumer deliberately has NO filter — easy to port the wrong behavior) | Unit: FAILED / UPLOADED / PROCESSING all filtered, zero table access; live: hand-crafted FAILED event → no entry |
| Title sourced from event detail instead of metadata | High (title silently missing/empty — search in Story 4.2 returns junk) | Medium (detail has no title; a builder reaching for the event first gets nothing) | Unit: entry's title asserted equal to metadata record's title, with event detail carrying no title field |
| Conditional-write port from history consumer | Medium (redelivery raises instead of overwriting → retry churn) | Medium (sibling code is the obvious template) | Unit: redelivery test asserts plain PutItem (no ConditionExpression) and overwrite semantics |
| Wire-shape drift (flat Detail misread) | High (consumer reads nothing) | Low (shape proven by Epic 2/3; history consumer consumes the identical wire) | Unit: flat-shape fixture built from `events.build_envelope` + promotion, not hand-typed |
| Terraform wiring gap (rule/ESM/IAM/zip source blocks) | High (nothing runs) | Medium (hand-maintained zip source blocks are a known failure class) | Live AC: apply + upload + inspect; purity probe guards zip contents indirectly |
| Existing wiring disturbed (history rule, upload rule, capture rule) | Medium (regresses Epics 1–3) | Low | Live AC: `terraform plan`/state diff shows only new resources |

## Acceptance Criteria → Test Mapping

| AC | Level | Test |
|----|-------|------|
| AC1 wiring exists, existing rules untouched | Integration (terraform) | `git diff main -- terraform/` shows only `search.tf` added (existing resource definitions byte-unchanged) + green apply — empty post-retro state means full clean bring-up (spec change log §4) |
| AC2 PROCESSED event → search-index entry `{videoId, title, processedKey, indexedAt}` | Integration (live floci) | Bruno/curl upload → poll metadata to PROCESSED → `get_item` on search-index by videoId |
| AC3 FAILED event indexes nothing | Integration (live floci) | hand-crafted `video.processed`-detail-type event with `status=FAILED` via boto3 put_events → no entry for that videoId |
| AC4 redelivery overwrites, no duplicates | Integration (live floci) | republish identical `video.processed` via boto3 → still exactly one entry, same fields |
| AC5 unknown videoId dropped; transient retried | Unit (live probe optional) | Unit: NotFoundError → ack (`dropped`); other error → raise. Live poison probe optional (identical semantics already proven live in Story 3.1) |
| AC6 CI green | Gate | `bash scripts/ci-local.sh` all 5 stages |

## Unit Test Checklist (RED phase — `lambdas/search_consumer/tests/test_search_consumer.py`)

Fakes: `FakeMetadataTable` (get_item, configurable: found / NotFoundError / other error), `FakeIndexTable` (put_item records items, overwrites by videoId, can raise). Monkeypatch module accessors `_metadata_table()` / `_index_table()` (project convention).

I/O matrix coverage (one test per row minimum):

- [x] T1 happy index: flat-detail SQS event, status PROCESSED, known videoId → put_item called with exactly `{videoId, title, processedKey, indexedAt}` and NO ConditionExpression; summary `indexed=1`; indexedAt ISO-8601 UTC; title == metadata record's title (event detail carries no title)
- [x] T2 status filter — FAILED: hand-crafted flat detail with `status=FAILED`, known videoId → no put_item, no metadata get_item, summary `filtered=1`, no raise (AC3)
- [x] T3 status filter — non-terminal: parametrize UPLOADED / PROCESSING → same as T2 (filter is `status == PROCESSED`, not `status in TERMINAL_STATUSES`)
- [x] T4 redelivery overwrite: same event processed twice → exactly one item in FakeIndexTable, fields identical, both invocations return `indexed=1` (AC4, NFR-1)
- [x] T5 poison (unknown videoId): PROCESSED event, metadata get_item raises NotFoundError → no put_item, summary `dropped=1`, no raise (AC5)
- [x] T6 transient metadata error: get_item raises other exception → handler raises (ESM retry), no put_item (AC5)
- [x] T7 transient write error: put_item raises → handler raises
- [x] T8 detail as JSON string: stringified detail parsed, identical to T1
- [x] T9 malformed records (parametrize): body not JSON / no detail / missing eventId / missing videoId / missing status / missing processedKey / empty-string fields / non-string fields → skipped + acked, no raise, no writes, summary `skipped=1`
- [x] T10 unknown status string (e.g. `status=ARCHIVED`, legal fields otherwise): filtered before metadata access, summary `filtered=1` — filter precedes STATUSES concerns; decide during build whether unknown-status is `filtered` or `skipped` and pin it here (recommendation: `filtered` — it is a status decision, and the history consumer's `skipped` for unknown status exists only because it must validate legality for the audit trail)
- [x] T11 non-SQS event: not a dict / Records missing / Records not list → raises `MalformedInputError`
- [x] T12 multiple records: mixed batch (indexed + filtered + dropped + skipped) → per-record outcomes tallied correctly, processed in order
- [x] T13 purity probe: client-recorder — only a `dynamodb` resource is ever constructed; never s3/events/states/sqs (guards zip/env scope creep)
- [x] T14 wire-shape coupling: real `event_publisher.handler` output (mocked events client capturing the published Detail) fed through the search consumer → indexed (producer→consumer contract, same shape as the 3.1 review-loop test)

Fixture discipline: PROCESSED event fixtures built via `shared.events.build_envelope(EVENT_PROCESSED, processed_detail(...))` + flat promotion `{**envelope, **envelope["detail"]}` — the producer's real wire shape, never hand-typed. Sole exception: FAILED/non-PROCESSED fixtures are hand-crafted flat details (build_envelope rejects them by design — see Design Notes).

## Terraform Checklist (`terraform/search.tf`)

- [x] X1 `search-index` table: PK `videoId` (S), PAY_PER_REQUEST (AD-3: derived, disposable, exactly one writer)
- [x] X2 `search-queue`: `visibility_timeout_seconds = 300` (history-leg parity)
- [x] X3 queue policy: `events.amazonaws.com` `sqs:SendMessage` scoped to the search rule ARN only
- [x] X4 NEW rule `video-processed-to-search` on `video-bus`, pattern `detail-type = ["video.processed"]`; target = search queue only (AD-1 as-built: new consumer = new queue + new rule — see Design Notes)
- [x] X5 `video-processed-to-history` rule (history.tf), `video.uploaded` rule (trigger.tf), capture rule (integration.tf) byte-unchanged
- [x] X6 zip: `shared/` (all 5 modules) + `search_consumer/` source blocks — hand-maintained set complete
- [x] X7 IAM: logs + `dynamodb:GetItem` on video-metadata only + `dynamodb:PutItem` on search-index only + SQS-ESM trio on search queue only
- [x] X8 env: `METADATA_TABLE`, `SEARCH_INDEX_TABLE`, `AWS_ENDPOINT_URL` — no names in code
- [x] X9 ESM `batch_size = 1`; handler string `search_consumer.handler.handler`; outputs (table name, queue name/URL/ARN, function)
- [x] X10 `terraform fmt -check` + `terraform validate` green

## Live Verification Checklist (floci)

- [x] L1 floci healthy (`curl -sf http://localhost:4566/_localstack/health`)
- [x] L2 `terraform apply` green — full clean bring-up from the empty post-retro state; X5 ("no churn") proven by `git diff main -- terraform/` showing only untracked `search.tf`, no existing `.tf` modified (spec change log §4)
- [x] L3 upload via gateway → poll `video-metadata` to PROCESSED → `search-index` has exactly one entry `{videoId, title, processedKey, indexedAt}`; title matches the metadata record's title
- [x] L4 republish identical `video.processed` (boto3 put_events) → wait → still exactly one entry, fields unchanged (overwrite, not duplicate)
- [x] L5 hand-crafted event: `detail-type=video.processed`, flat detail with `status=FAILED`, known or unknown videoId, fabricated eventId → wait → no search-index entry for that videoId
- [x] L6 history leg regression: the same upload still produces exactly one `status-history` entry (both consumers fed by their own rules)
- [x] L7 consumer Lambda logs show the indexed/filtered/dropped lines (NFR-5 traceability)

## Build Evidence (bmad-build, 2026-08-24)

- **Files:** `lambdas/search_consumer/{__init__,handler}.py` + `tests/{conftest,test_search_consumer}.py` (RED confirmed first: 3 failed + 57 import errors before the handler existed), `terraform/search.tf` (new file only).
- **G2:** `pytest lambdas/ -q` → **319 passed** (60 new search-consumer tests: T1–T14 + config-not-code).
- **X5 proof:** `git diff main --stat -- terraform/` → empty; `git status terraform/` → only untracked `search.tf`. Post-retro empty state made the apply a full clean bring-up; `terraform apply` green.
- **L3:** upload `614bb1c4-d245-4d8c-a93e-2b5b861fb0f1` → PROCESSED → search-index entry `{"videoId": "614bb1c4-d245-4d8c-a93e-2b5b861fb0f1", "title": "Search Leg Live Fixture", "processedKey": "processed/614bb1c4-d245-4d8c-a93e-2b5b861fb0f1/fixture.mp4", "indexedAt": "2026-08-24T05:13:44Z"}` (re-read live from floci during review) — title matches metadata record.
- **L4:** republished identical event → still exactly one entry, videoId/title/processedKey unchanged (`indexedAt` intentionally refreshes — plain-PutItem upsert semantics pinned by review loop 1).
- **L5:** legal hand-crafted FAILED detail → no entry; log line `filtering non-PROCESSED event … status=FAILED`, summary `filtered=1`.
- **L6:** same upload → exactly one status-history entry (`eventId=d3ada7c4-7006-58da-896f-b05b70e1f9b7`).
- **L7:** consumer logs show `search indexed …` / `filtering non-PROCESSED …` / `skipping malformed record: missing or empty field processedKey` lines with batch summaries.
- **G3:** `bash scripts/ci-local.sh` → all 5 stages green (integration 13/13 passed).

## Gate

- [x] G1 `uv run --with ruff ruff check lambdas/ --select E,F`
- [x] G2 `uv run --with 'pytest>=8.0' pytest lambdas/ -q` — all new + all existing pass
- [x] G3 `bash scripts/ci-local.sh` — 5 stages green (worktree compose-name caveat per spec-2-3 design notes)

## Red-Green Workflow

1. RED: write conftest + full T1–T14 suite against a stub handler module → suite fails on import/behavior
2. GREEN: implement `search_consumer/handler.py` minimally until T1–T14 pass
3. Terraform: search.tf → fmt/validate → apply → L1–L7
4. Gate G1–G3, then mark checklist items with evidence (counts, key lines)

## Deviations

- Playwright/E2E/component sections of the template: N/A (no UI, no JS stack). Primary level is pytest unit per project convention (Stories 1.2/2.x/3.1 all shipped this shape).
- No `test.skip()` scaffolds committed: project ATDD convention (established in Story 3.1) is red-suite-then-implement within the build story, not persisted skip-marked files. Step-04 subagent dispatch collapses to sequential in-agent generation for the same reason; `generatedTestFiles` stays empty — the RED suite is authored during `bmad-build` of Story 4.1 from this checklist.
- Pact/contract testing: N/A (relevance gate — no consumer-driven contracts in this project; the wire shape is pinned by the shared layer and tested via T14).

## Completion Summary

- **Checklist:** `_bmad-output/test-artifacts/atdd-checklist-4-1-search-consumer-indexing-processed-videos.md`
- **Story handoff:** Story 4.1 ACs live in `_bmad-output/planning-artifacts/epics.md` (no separate spec file yet; the build story may mint `spec-4-1-*.md` per convention)
- **Key risks:** status-filter correctness (T2/T3/L5), title provenance from metadata (T1/L3), upsert-not-conditional-write discipline (T4/L4)
- **Next:** `bmad-build` Story 4.1 in a fresh context window (RED suite from this checklist → GREEN → terraform → live → gate)
