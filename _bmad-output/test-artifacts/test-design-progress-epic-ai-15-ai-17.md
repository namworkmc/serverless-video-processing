---
runScope: 'epic-level'
runKey: 'epic-ai-15-ai-17'
workflowStatus: 'completed'
totalSteps: 5
stepsCompleted: ['step-01-detect-mode', 'step-02-load-context', 'step-03-risk-and-testability', 'step-04-coverage-plan', 'step-05-generate-output']
lastStep: 'step-05-generate-output'
nextStep: ''
lastSaved: '2026-08-26'
---

# Test Design Progress — epic-ai-15-ai-17

## Step 1: Detect Mode & Prerequisites

- **Mode:** Epic-Level — user named the build spec `_bmad-output/implementation-artifacts/spec-ai-15-ai-17-retro-guard-truncation.md` (requirements + Given/When/Then ACs); `sprint-status.yaml` present (file-based detection agrees).
- **Scope note:** run covers ONE spec (epic-4 retro remediation: AI-15 guard extension, AI-17 truncation warnings), not the whole epic-4 story set — slug `ai-15-ai-17` derived from the spec's tracking identifiers because the work carries no story-epic number.
- **Prerequisites:** spec ACs ✓; architecture context via epic-4-context.md + code on disk ✓; TEA config loaded (`_bmad/tea/config.yaml`, user Kygor, English) ✓.
- **Existing checkpoint for this run_key:** none — fresh run.

## Step 2: Load Context & Knowledge Base

- **Config flags:** playwright_utils=true, pactjs_utils=true, pact_mcp=mcp, browser_automation=auto, test_stack_type=auto.
- **Stack detect:** backend (pyproject/uv/pytest; no frontend/mobile manifests, no page.goto anywhere) -> API-only posture.
- **Relevance gates:** Playwright/Pact fragments skipped (no browser tests; no Pact artifacts/JS/microservices); pact_mcp_reachable=false (tool-list probe; no MCP tools in session), recorded once per mandate fallback order -> provider source = handler code + existing suites.
- **Required epic-level fragments loaded:** risk-governance.md, probability-impact.md, test-levels-framework.md, test-priorities-matrix.md. nfr-criteria.md skipped: NFR-7 semantics already pinned by spine + spec frozen block.
- **Project artifacts:** approved spec spec-ai-15-ai-17-retro-guard-truncation.md (frozen I/O matrix + ACs); epics.md FR-19/NFR-7 definitions; ARCHITECTURE-SPINE scan rule; retro F1/F2 findings; code + suites for search_rebuild/search_query/history_query read during build planning.
- **Existing coverage:** 393 unit tests green at baseline a0fe408; rebuild truncation pin exists (test_search_rebuild.py:288-306); guard suite covers hyphen-form only; query suites have zero LastEvaluatedKey coverage. Integration stage exists but out of scope for this design (log-line + source-text changes only).

## Step 3: Risk Assessment

Scope note: epic-level run -> risk matrix only; system-level testability review not applicable.

| ID | Category | Risk | P | I | Score | Action | Mitigation |
|----|----------|------|---|---|-------|--------|------------|
| R1 | TECH | Guard bypassed by reference form carrying neither literal (computed/dynamic refs) | 1 | 3 | 3 | DOCUMENT | Residual accepted; rg-verified constraint holds today; retro process re-checks |
| R2 | TECH | Truncation warning wrong placement/count; log-only blast radius | 2 | 1 | 2 | DOCUMENT | Pin asserts 'truncated' record + page-size intact + single scan call |
| R3 | BUS | Truncation pin passes vacuously (caplog/logger mismatch) -> false-assurance class recurs | 2 | 3 | 6 | MITIGATE | RED-first execution evidence (spec sequencing); assert records from handler's own logger; wrap-the-real-fake technique copied from rebuild pin |
| R4 | OPS | history_query pin blocked at 404 gate -> green for wrong reason | 2 | 2 | 4 | MONITOR | Fixture knows VIDEO_ID; pin asserts scan_calls == 1 proving the scan ran |
| R5 | TECH | Guard extension flags legit future substring -> CI friction | 2 | 1 | 2 | DOCUMENT | Spec Ask First: real hit halts for human routing |

NFR planning: NFR-7 single-scan semantics stay (pins assert exactly one scan call); NFR-5 observability improved by warnings (evidence: caplog records in unit runs); no new thresholds; no UNKNOWN threshold items. Evidence sources planned: pytest lambdas/ output (unit), ruff E,F, gitleaks, scripts/ci-local.sh stages.

Summary: highest risk R3 (score 6) is mitigated by construction - the workflow itself demands observed RED before GREEN, which converts the pin from claim to evidence. No score >=9 risks. No blockers.

## Step 4: Coverage Plan & Execution Strategy

ID convention adapted from {EPIC}.{STORY}-{LEVEL}-{SEQ}: run has no story-epic number, so AI-item stands in for story -> AI15-* / AI17-*.

| ID | Scenario (Given/When/Then condensed) | Level | Priority | Covers |
|----|--------------------------------------|-------|----------|--------|
| AI15-UNIT-001 | Other .tf carries address-form ref outside comments -> guard FAILS naming file+form | Unit | P0 | AC-1, F1/R3, R1-residual documented |
| AI15-UNIT-002 | Unmodified tree -> full guard suite green (no false positive) | Unit | P0 | AC-2 |
| AI17-UNIT-001 | search_query: LastEvaluatedKey present -> WARN 'truncated', 200 + page items projected/sorted intact, exactly 1 scan call | Unit | P1 | AC-3, F2/R2 |
| AI17-UNIT-002 | search_query: single-page result -> no truncation record | Unit | P1 | AC-4 |
| AI17-UNIT-003 | history_query: LastEvaluatedKey present (videoId known) -> same as 001 with entries shape | Unit | P1 | AC-3, F2/R4 |
| AI17-UNIT-004 | history_query: single-page result -> no truncation record | Unit | P1 | AC-4 |

Duplicate-coverage guard: no API/E2E duplicates - changes are source-text assertions + log lines; unit level is primary AND sufficient. Existing rebuild pin (test_search_rebuild.py:288-306) already covers pattern source; untouched.

NFR evidence plan: NFR-7 -> scan_calls==1 assertions inside AI17-UNIT-001..004 (evidence: pytest output); NFR-5 observability -> 'truncated' record assertions (evidence: caplog records). No missing thresholds; lab posture documented in spine.

Execution strategy: PR gate only - full unit suite + ruff + gitleaks via scripts/ci-local.sh stages (<1 min unit); no nightly/expensive tier needed.

Resource estimate: P0 ~0.5-1h; P1 ~1-2h; total ~2-3h including validation gates and RED-evidence capture.

Quality gates: P0 pass rate 100%; P1 pass rate >=95% (target 100%, 4 tests); R3 mitigation complete BEFORE merge = observed RED evidence recorded for each new pin + guard extension; NFR evidence sources identified above; final PASS/CONCERNS/FAIL deferred (nfr-assess out of scope here).

## Step 5: Generate Output & Validate

- Output: _bmad-output/test-artifacts/test-design-epic-ai-15-ai-17.md (single epic-level doc, template structure).
- Validation: checklist passed — risk register complete (IDs/categories/P/I/scores/mitigations/owners), coverage matrix atomic + no cross-level duplication, priority headers criteria-only, PR-model execution strategy, range estimates, quality gates incl. R3 RED-evidence gate.
- Hygiene: no CLI/browser sessions opened; all artifacts under test-artifacts/.
- Completion report: mode=epic-level (runKey epic-ai-15-ai-17); 1 high risk (R3, score 6) gated by observed-RED evidence before merge; 6 planned tests (2 P0, 4 P1); open assumptions recorded in Assumptions and Dependencies (page-size vector only, caplog propagation, glob pickup of scratch probe).
