---
workflowStatus: 'completed'
totalSteps: 5
stepsCompleted: ['step-01-detect-mode', 'step-02-load-context', 'step-03-risk-and-testability', 'step-04-coverage-plan', 'step-05-generate-output']
lastStep: 'step-05-generate-output'
nextStep: ''
lastSaved: '2026-08-26'
---

# Test Design: Epic ai-15-ai-17 — Retro Guard Underscore Hole & Read-Lambda Truncation Warnings

**Date:** 2026-08-26
**Author:** Kygor (Master Test Architect run)
**Status:** Approved (scoped to spec `_bmad-output/implementation-artifacts/spec-ai-15-ai-17-retro-guard-truncation.md`, baseline a0fe408)

---

## Executive Summary

**Scope:** Epic-level (single-spec) test design for the two epic-4 retro remediations: AI-15 (extend FR-19 admin-only guard to the underscore/address reference form) and AI-17 (LastEvaluatedKey truncation warnings + unit pins on both read lambdas).

**Risk Summary:**

- Total risks identified: 5
- High-priority risks (≥6): 1 (R3 — vacuous pin / false-assurance recurrence)
- Critical categories: BUS (proof integrity), TECH

**Coverage Summary:**

- P0 scenarios: 3 (~0.5–1 hour) — includes AI15-UNIT-003, the durable in-suite guard-probe pin added at step-04 review
- P1 scenarios: 4 (~1–2 hours)
- P2/P3 scenarios: 0
- **Total effort**: ~2–3 hours (~0.5 day) including validation gates and RED-evidence capture

---

## Not in Scope

| Item | Reasoning | Mitigation |
| --- | --- | --- |
| Integration/E2E tests for the new behavior | Changes are source-text assertions + log lines; unit level is primary AND sufficient (duplicate-coverage guard) | Full existing integration suite re-runs in CI as regression net |
| Pagination/GSI plumbing | NFR-7 pins single-scan; retro disposition keeps scope | scan_calls == 1 asserted in every new pin |
| `truncated` flag in responses/summary (retro F8) | Deferred by retro with its own entry | None needed here |
| Other reference-form hardening beyond the two literals (dynamic/computed refs) | Verified absent today; speculative forms would over-constrain | R1 residual documented; retro process re-checks |

---

## Risk Assessment

### High-Priority Risks (Score ≥6)

| Risk ID | Category | Description | Probability | Impact | Score | Mitigation | Owner | Timeline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R3 | BUS | Truncation pin passes vacuously (caplog/logger mismatch, wrong fixture) — the false-assurance disease AI-15/17 treat recurs in the cure | 2 | 3 | 6 | ATDD RED→GREEN sequencing is mandatory: each pin observed failing BEFORE its handler change lands; assertions target the handler module's own logger records; wrap-the-real-fake technique copied from rebuild pin | Kygor | before merge (2026-08-26 build) |

### Medium-Priority Risks (Score 3-4)

| Risk ID | Category | Description | Probability | Impact | Score | Mitigation | Owner |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R4 | OPS | history_query pin blocked at the 404 metadata gate → green for the wrong reason | 2 | 2 | 4 | Fixture already knows VIDEO_ID; pin asserts exactly 1 history scan call proving the gate passed | Kygor |
| R1 | TECH | Guard bypassed by a reference form carrying neither literal (computed/dynamic refs) | 1 | 3 | 3 | Residual accepted: rg-verified constraint holds today at baseline; documented residual | Kygor |

### Low-Priority Risks (Score 1-2)

| Risk ID | Category | Description | Probability | Impact | Score | Action |
| --- | --- | --- | --- | --- | --- | --- |
| R2 | TECH | Warning placed/counted wrong (post-projection count vs raw page size); log-only blast radius | 2 | 1 | 2 | Document — pin asserts warning + page items intact + single call |
| R5 | TECH | Guard extension flags a legitimate future substring use → CI friction | 2 | 1 | 2 | Document — spec Ask First rule: any real hit halts for human routing |

---

## NFR Planning

| NFR Category | Requirement / Threshold | Risk Link | Planned Validation | Evidence Needed |
| --- | --- | --- | --- | --- |
| Reliability/observability (NFR-5) | Partial results past one Scan page must be observable | R2, R3 | Unit pins assert WARNING record containing "truncated" via caplog | pytest lambdas/ output |
| Performance posture (NFR-7) | Single-scan semantics stay; no pagination introduced | R2 | scan_calls length == 1 asserted inside every truncation pin | pytest lambdas/ output |

**Unknown thresholds:** None. NFR-7 is deliberately a non-goal declaration, not a measurable threshold; lab posture documented in the architecture spine.

---

## Entry Criteria

- [x] Requirements agreed: approved spec with frozen I/O matrix + ACs
- [x] Test environment: unit-only — fakes per established suite conventions; no live infra required
- [x] Test data: fake table fixtures already exist in all three suites
- [x] Baseline clean: 393 unit tests green at a0fe408

## Exit Criteria

- [x] All P0 tests passing (guard suite incl. the durable address-form probe pin AI15-UNIT-003) — evidence: guard suite 4/4 green in `pytest lambdas/` run of 2026-08-26
- [x] All P1 tests passing (4 truncation/control pins across both read lambdas) — evidence: `pytest lambdas/ -q` → 398 passed
- [x] Observed RED evidence recorded for every new pin + the extended guard assertion (R3 gate) — evidence: both truncation pins FAILED pre-handler-change on missing "truncated" WARNING record (controls passed); extended guard FAILED against a scratch `.tf` carrying `aws_lambda_function.search_rebuild.arn`, naming file + form — that one-time probe is now made durable as AI15-UNIT-003
- [x] No open score ≥6 risk unmitigated — R3 mitigation status Complete (below)
- [x] Full local CI mirror green — stages verified individually on 2026-08-26: gitleaks clean, ruff E,F clean, terraform fmt-check + validate green, unit suite 398 passed, integration 18/18 against live floci (`ci-local.sh` stage 5's terraform apply cannot run from a stateless worktree checkout — environmental, not change-related; apply skipped, integration invoked directly via GATEWAY_BASE_URL)

---

## Test Coverage Plan

*P0/P1 = priority, NOT execution timing.*

ID convention adapts `{EPIC}.{STORY}-{LEVEL}-{SEQ}` to `AI{item}-UNIT-{seq}` (no story-epic number exists for retro remediation work).

### P0 (Critical)

**Criteria**: Security-critical proof integrity (FR-19 admin-only structural guarantee) with no workaround — this IS the automated proof.

| Test ID | Requirement | Test Level | Risk Link | Notes |
| --- | --- | --- | --- | --- |
| AI15-UNIT-001 | Address-form ref (`aws_lambda_function.search_rebuild.arn`) injected into any other `.tf` outside comments → guard FAILS naming file + form | Unit | R3 | RED proven via temporary scratch `.tf` injection, reverted before commit |
| AI15-UNIT-002 | Unmodified tree → full guard suite passes (no false positive from the added literal) | Unit | R1, R5 | rg pre-verified: zero cross-file occurrences of either form at baseline |
| AI15-UNIT-003 | Durable positive-direction pin: scratch probe `.tf` written into the terraform dir carrying the address form → invoking the guard test raises AssertionError naming `_probe_ai15_scratch.tf` AND the `search_rebuild` form; probe removed in try/finally | Unit | R3 | Added at step-04 review — replaces reliance on unreproducible one-time manual RED evidence |

**Total P0**: 3 tests, ~0.5–1 hour

### P1 (High)

**Criteria**: Core client journeys (search, history) observability; regression-prevention class against silent partial results.

| Test ID | Requirement | Test Level | Risk Link | Notes |
| --- | --- | --- | --- | --- |
| AI17-UNIT-001 | search_query: Scan result carries LastEvaluatedKey → WARNING containing "truncated" logged; response stays 200 with that page's projected+sorted items; exactly 1 scan call | Unit | R2, R3 | Wrap (not replace) FakeIndexTable.scan so expression-shape evaluation still runs |
| AI17-UNIT-002 | search_query: normal single-page result → no truncation warning record | Unit | R3 | Control half — prevents always-warn vacuous pass |
| AI17-UNIT-003 | history_query: same truncated-scan contract with entries shape preserved; videoId known so 404 gate passes | Unit | R4 | scan_calls == 1 doubles as gate-ordering evidence |
| AI17-UNIT-004 | history_query: normal single-page result → no truncation warning record | Unit | R3 | Control half |

**Total P1**: 4 tests, ~1–2 hours

---

## Execution Strategy

**Philosophy:** Run everything in PRs if < 15 minutes — defer only what is expensive/long-running.

- **PR gate:** full unit suite + ruff E,F + gitleaks (+ terraform fmt/validate floor) via `scripts/ci-local.sh` stages; unit portion runs in seconds. Integration stage runs when Docker/floci available.
- **Nightly/Weekly:** nothing — no expensive tier applies to this change set.

Within the build itself, execution order per pin: write pin → observe RED → land handler/guard change → observe GREEN → record evidence.

---

## Resource Estimates

### Test Development Effort

| Priority | Count | Hours/Test | Total Hours | Notes |
| --- | --- | --- | --- | --- |
| P0 | 3 | ~0.25–0.5 | ~0.75–1.5 | Pattern source exists (rebuild suite); AI15-UNIT-003 makes the RED dance durable in-suite |
| P1 | 4 | ~0.25–0.5 | ~1–2 | Copy wrap-the-real-fake technique; caplog assertions |
| **Total** | **7** | **—** | **~2–3.5** | Including validation gates and RED-evidence capture (~0.5 day) |

### Prerequisites

**Test Data:** existing fakes (`FakeIndexTable`, `FakeHistoryTable`, `FakeMetadataTable`) — expression-evaluating, already proven.

**Tooling:** pytest + caplog (existing convention), uv-managed environment.

**Environment:** none beyond local Python (unit level only).

---

## Quality Gate Criteria

- **P0 pass rate**: 100% (no exceptions)
- **P1 pass rate**: 100% (all 4 pass)
- **High-risk mitigations**: R3 mitigation (observed RED evidence) 100% complete before merge
- **Regression**: all unit tests remain green — 393 baseline at a0fe408 plus 5 new (4 truncation/control pins + durable guard pin AI15-UNIT-003); expected post-change total 398
- NFR evidence identified above; final PASS/CONCERNS/FAIL deferred to `nfr-assess` if ever run on this scope

---

## Mitigation Plans

### R3: Vacuous-pin false assurance (Score: 6)

**Mitigation Strategy:** 1) Write every pin first and run it against unchanged handlers — capture the failure output as RED evidence. 2) Assert on records emitted by the handler module's own logger (`caplog.at_level(logging.WARNING)` + message-content match on "truncated"), never on generic record counts alone. 3) Include the control pin (no warning on single-page) so an always-warn implementation cannot pass. 4) Reuse the rebuild suite's proven wrap-the-real-fake technique rather than stubbing scan wholesale.
**Owner:** Kygor
**Timeline:** before merge (this build)
**Status:** Complete — observed RED evidence captured 2026-08-26: both truncation pins failed pre-handler-change (assert missing "truncated" record), and the extended guard failed against a scratch `.tf` address-form reference naming file + form; the latter is now enshrined as the durable pin AI15-UNIT-003 so detection proof reruns on every suite execution.
**Verification:** RED evidence recorded per pin; GREEN after minimal handler change; full suite green.

---

## Assumptions and Dependencies

### Assumptions

1. DynamoDB Scan page semantics (~1 MB) are the only truncation vector in scope; no other silent-truncation path exists in these handlers.
2. caplog captures handler logs under default propagation (proven by existing rebuild pin and query log-line tests).
3. The terraform dir glob in the guard picks up any scratch `.tf` created for RED probing (verified: `glob("*.tf")`).

### Risks to Plan

- **Risk**: Scratch probe file left behind after RED evidence capture → permanent CI red.
  - **Impact**: broken main; confusing failure naming a nonexistent wiring violation.
  - **Contingency**: create/remove within one pytest invocation (try/finally); verify `git status` clean post-run.

---

## Interworking & Regression

| Service/Component | Impact | Regression Scope |
| --- | --- | --- |
| search-query lambda | +1 log line (warning path only) | Existing search_query suite + gateway integration tests |
| history-query lambda | +1 log line (warning path only) | Existing history_query suite + gateway integration tests |
| search-rebuild guard test | Test-only strictness increase | Guard suite itself + full unit run (no runtime code touched) |
| Terraform resources | None (no .tf edits) | tf fmt/validate stages |

---

## Appendix

### Knowledge Base References

- `risk-governance.md` — risk classification framework
- `probability-impact.md` — P×I scoring methodology
- `test-levels-framework.md` — unit-level selection rationale
- `test-priorities-matrix.md` — P0/P1 assignment

### Related Documents

- Spec: `_bmad-output/implementation-artifacts/spec-ai-15-ai-17-retro-guard-truncation.md`
- Retro findings F1/F2: `_bmad-output/implementation-artifacts/epic-4-retro-2026-08-26.md`
- FR-19/NFR-7: `_bmad-output/planning-artifacts/epics.md`
- Architecture spine: `_bmad-output/planning-artifacts/architecture/architecture-serverless-video-processing-2026-08-17/ARCHITECTURE-SPINE.md`

---

**Generated by**: BMad TEA Agent - Test Architect Module
**Workflow**: `bmad-testarch-test-design`
