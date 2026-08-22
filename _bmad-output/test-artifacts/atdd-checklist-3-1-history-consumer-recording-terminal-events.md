---
workflowType: 'testarch-atdd'
storyId: '3.1'
storyKey: '3-1-history-consumer-recording-terminal-events'
storyFile: '_bmad-output/implementation-artifacts/spec-3-1-history-consumer-recording-terminal-events.md'
primaryLevel: 'unit (pytest) + live floci integration'
---

# ATDD Checklist — Epic 3, Story 3.1: History Consumer — Recording Terminal Events

**Date:** 2026-08-22
**Author:** Murat (Test Architect) for Kygor
**Primary Test Level:** Unit (pytest with fakes) — integration backstop via live floci + CI mirror

## Story Summary

A `history-consumer` Lambda behind `history-queue` appends one `status-history` entry per unique `video.processed` event: unwrap SQS body → EventBridge detail, validate `videoId` against `video-metadata`, conditional `PutItem` keyed by `eventId` (the condition IS the dedupe). Unknown videoId = poison (drop + ack); transient errors raise (SQS retries).

## Risk Assessment

| Risk | Impact | Likelihood | Test response |
|------|--------|-----------|---------------|
| Duplicate history entries on redelivery | High (audit trail integrity is the story's whole point) | High (SQS is at-least-once) | Unit: conditional-PutItem collision path; live: republish redelivery AC |
| Poison event dropped vs transient error retried — wrong branch | High (silent data loss vs retry storm) | Medium | Unit: NotFoundError → ack; any other error → raise. Both branches explicit |
| Wire-shape drift (flat Detail misread) | High (consumer reads nothing) | Medium (shape reconciled 2026-08-21, proven by Epic 2) | Unit: flat-shape fixture built from `events.build_envelope` + promotion, not hand-typed |
| Terraform wiring gap (rule/ESM/IAM/zip source blocks) | High (nothing runs) | Medium (hand-maintained zip source blocks are a known failure class) | Live AC: apply + upload + inspect; purity probe guards zip contents indirectly |
| Existing rules disturbed (video.uploaded, smoke capture) | Medium (regresses Epic 1/2 + smoke) | Low | Live AC: `terraform plan`/state diff shows only new resources |

## Acceptance Criteria → Test Mapping

| AC | Level | Test |
|----|-------|------|
| AC1 wiring exists, existing rules untouched | Integration (terraform) | `terraform apply` resource diff + `terraform state list` before/after |
| AC2 upload → PROCESSED → exactly one history entry | Integration (live floci) | Bruno/curl upload → poll metadata → `get_item` on status-history by deterministic eventId |
| AC3 redelivery appends nothing | Integration (live floci) | republish identical `video.processed` via boto3 → still one entry |
| AC4 unknown videoId dropped | Integration (live floci) | publish `video.processed` with unknown videoId + fabricated eventId → no entry |
| AC5 CI green | Gate | `bash scripts/ci-local.sh` all 5 stages |

## Unit Test Checklist (RED phase — `lambdas/history_consumer/tests/test_history_consumer.py`)

Fakes: `FakeMetadataTable` (get_item, configurable: found / NotFoundError / other error), `FakeHistoryTable` (put_item honoring `attribute_not_exists(eventId)` — raises `ConditionalCheckFailedException`-named class on collision, records items). Monkeypatch module accessors `_metadata_table()` / `_history_table()` (project convention).

I/O matrix coverage (one test per row minimum):

- [ ] T1 happy record: flat-detail SQS event, known videoId → put_item called with exactly `{eventId, videoId, status, timestamp}` + condition `attribute_not_exists(eventId)`; summary `recorded=1`; timestamp ISO-8601 UTC
- [ ] T2 duplicate eventId: history table raises conditional-check-failed → no raise, summary `deduped=1`, table still holds exactly one item
- [ ] T3 poison (unknown videoId): metadata get_item raises NotFoundError → no put_item call, summary `dropped=1`, no raise
- [ ] T4 transient metadata error: get_item raises other exception → handler raises (ESM retry), no put_item
- [ ] T5 transient write error: put_item raises non-conditional error → handler raises
- [ ] T6 detail as JSON string: stringified detail parsed, identical to T1
- [ ] T7 malformed records (parametrize): body not JSON / no detail / missing eventId / missing videoId / missing status / empty-string fields → skipped + acked, no raise, no writes, summary `skipped=1`
- [ ] T8 non-SQS event: not a dict / Records missing / Records not list → raises `MalformedInputError`
- [ ] T9 multiple records: mixed batch (recorded + deduped + dropped + skipped) → per-record outcomes tallied correctly, processed in order
- [ ] T10 purity probe: client-recorder — only a `dynamodb` resource is ever constructed; never s3/events/states/sqs (guards zip/env scope creep)
- [ ] T11 eventId provenance: entry's eventId equals `shared.events.event_id(videoId, status)` — dedupe key is the deterministic UUID5, not the EventBridge top-level id

Fixture discipline: event fixtures built via `shared.events.build_envelope(EVENT_PROCESSED, processed_detail(...))` + flat promotion `{**envelope, **envelope["detail"]}` — the producer's real wire shape, never a hand-typed dict.

## Terraform Checklist (`terraform/history.tf`)

- [ ] X1 `status-history` table: PK `eventId` (S), PAY_PER_REQUEST
- [ ] X2 `history-queue`: `visibility_timeout_seconds = 300`
- [ ] X3 queue policy: `events.amazonaws.com` `sqs:SendMessage` scoped to the history rule ARN only
- [ ] X4 NEW rule `video-processed-to-history` on `video-bus`, pattern `detail-type = ["video.processed"]`; target = history queue only
- [ ] X5 `video.uploaded` rule (trigger.tf) and smoke capture rule (smoke.tf) byte-unchanged
- [ ] X6 zip: `shared/` (all 5 modules) + `history_consumer/` source blocks — hand-maintained set complete
- [ ] X7 IAM: logs + `dynamodb:GetItem` on video-metadata only + `dynamodb:PutItem` on status-history only + SQS-ESM trio on history queue only
- [ ] X8 env: `METADATA_TABLE`, `HISTORY_TABLE`, `AWS_ENDPOINT_URL` — no names in code
- [ ] X9 ESM `batch_size = 1`; handler string `history_consumer.handler.handler`; outputs (table name, queue name/URL/ARN, function)
- [ ] X10 `terraform fmt -check` + `terraform validate` green

## Live Verification Checklist (floci)

- [ ] L1 floci healthy (`curl -sf http://localhost:4566/_localstack/health`)
- [ ] L2 `terraform apply` — only the ~10 new resources created; state list diff confirms no churn on existing resources
- [ ] L3 upload via gateway → poll `video-metadata` to PROCESSED → `status-history` has exactly one entry `{eventId=UUID5(videoId, PROCESSED), videoId, status, timestamp}`
- [ ] L4 republish identical `video.processed` (boto3 put_events) → wait → still exactly one entry
- [ ] L5 publish `video.processed` with unknown videoId + fabricated eventId → wait → no entry for that eventId
- [ ] L6 consumer Lambda logs show the recorded/deduped/dropped lines (NFR-5 traceability)

## Gate

- [ ] G1 `uv run --with ruff ruff check lambdas/ --select E,F`
- [ ] G2 `uv run --with 'pytest>=8.0' pytest lambdas/ -q` — all new + all existing pass
- [ ] G3 `bash scripts/ci-local.sh` — 5 stages green (smoke reuses healthy running floci; worktree compose-name caveat per spec-2-3 design notes)

## Red-Green Workflow

1. RED: write conftest + full T1–T11 suite against a stub handler module → suite fails on import/behavior
2. GREEN: implement `history_consumer/handler.py` minimally until T1–T11 pass
3. Terraform: history.tf → fmt/validate → apply → L1–L6
4. Gate G1–G3, then mark checklist items with evidence (counts, key lines)

## Execution Evidence (2026-08-22)

- RED: `pytest lambdas/history_consumer/ -q` → `ModuleNotFoundError: No module named 'history_consumer.handler'` (collection error, as expected)
- GREEN: 39 tests pass (T1–T11 all covered; one test bug fixed in RED suite — poison test missing `known.clear()`)
- X1–X10: `terraform fmt -check -recursive` + `terraform validate` green; `terraform apply` created all 10 history resources; post-apply `terraform plan` = no changes; `video.uploaded` rule and smoke capture rule untouched (declared only in trigger.tf/smoke.tf, unmodified)
- L1: floci healthy (compose up from main checkout)
- L2: apply complete, plan clean after
- L3: gateway upload → PROCESSED → exactly one history entry `{eventId=5bec8f4d-…, videoId=c68a67e3-…, status=PROCESSED, timestamp=2026-08-22T02:54:02Z}`; eventId matches UUID5(videoId, PROCESSED)
- L4: republished identical `video.processed` → still exactly one entry; log: `duplicate eventId — dedupe`
- L5: poison event (unknown videoId, fabricated eventId) → no entry; log: `dropping poison event: unknown videoId=…`
- L6: CloudWatch logs show recorded/dedupe/dropped lines + batch summaries
- G1: ruff `All checks passed!`
- G2: 217 passed (39 new + 178 existing)
- G3: stages 1–4 via script commands green; stage 5 smoke invoked directly against healthy floci (worktree compose-name caveat per spec-2-3): `all_pass: True`, 10 scenarios pass. Bonus evidence: smoke's own `video.processed` events were recorded by the history leg (3 entries total) — consumer serves every producer.

### Review-loop patches (2026-08-22, step-04 triage)

- `_now_iso()` duplication removed — handler now calls `shared.status._now_iso()` (blind-hunter)
- Purity probe fixed — was self-defeating (patched accessors bypassed real client choices); now patches only `h.clients`, real accessors run (blind-hunter)
- Added non-string-field tests (status=123/dict/list → skipped) and non-string-body test (dict body → skipped) (blind-hunter)
- `sprint-status.yaml` last_updated format restored to `MM-DD-YYYY HH:MM` (blind-hunter)
- `lambdas/README.md` updated: directory tree + history-consumer section + suite list (43 tests) (blind-hunter)
- Re-verified after patches: ruff green, 221 passed (43 consumer), `terraform apply` (zip rebuilt), fresh live upload → PROCESSED → exactly one history entry
- Deferred (5 entries in deferred-work.md): _parse_detail consolidation, eventId derivation cross-check, history-leg smoke scenario, smoke residue cleanup, root README refresh
- Rejected against frozen spec: DLQ/retry machinery (Never), status filtering (Never), consumption-time timestamp (Always), depends_on (apply green twice), messageId logging (batch_size=1, shim parity)

## Deviations

- Playwright/E2E/component/data-testid sections of the template: N/A (no UI, no JS stack). Primary level is pytest unit per project convention (Stories 1.2/2.1/2.2/2.3 all shipped this shape).
- No `test.skip()` scaffolds committed: project ATDD convention is red-suite-then-implement within the story, not persisted skip-marked files.
