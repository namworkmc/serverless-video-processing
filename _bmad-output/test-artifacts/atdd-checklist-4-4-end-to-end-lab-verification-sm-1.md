---
workflowType: 'testarch-atdd'
storyId: '4.4'
storyKey: '4-4-end-to-end-lab-verification-sm-1'
storyFile: '_bmad-output/implementation-artifacts/spec-4-4-end-to-end-lab-verification-sm-1.md'
atddChecklistPath: '_bmad-output/test-artifacts/atdd-checklist-4-4-end-to-end-lab-verification-sm-1.md'
generatedTestFiles: []
inputDocuments:
  - '_bmad-output/implementation-artifacts/spec-4-4-end-to-end-lab-verification-sm-1.md'
  - '_bmad-output/implementation-artifacts/epic-4-context.md'
  - '_bmad-output/test-artifacts/atdd-checklist-4-3-admin-index-rebuild.md'
  - 'bruno/*.bru, bruno/environments/Local.bru'
  - 'scripts/ci-local.sh, docs/ci.md'
  - 'terraform/upload.tf (api_id / gateway_base_url outputs)'
primaryLevel: 'live floci verification + static content checks (rg) — no unit-test surface (zero new code)'
---

# ATDD Checklist — Epic 4, Story 4.4: End-to-End Lab Verification (SM-1)

**Date:** 2026-08-24
**Author:** Murat (Test Architect) for Kygor
**Primary Test Level:** Live floci end-to-end (destroy → apply → full Bruno collection) + static content gates — this story ships NO code, so there is no T-range; correctness lives in B/R/X/L ranges.

## Story Summary

Prove SM-1, the definition of done: from a clean `terraform destroy` + `apply` on healthy floci the entire environment rebuilds with no manual steps and no `aws` CLI; the complete Bruno collection (upload → upload-malformed → history-query → search-video, gateway URL only, poll-with-timeout) passes against the fresh environment; one upload yields a PROCESSED-visible history entry and a search hit through the gateway; the video's path is traceable through Step Functions execution history, event records, S3, and Lambda logs; the README documents the fixed bring-up order, the `_aws/execute-api` data-plane URL with the apiId from Terraform output, the `-replace` caveat for ASL changes, and a clean-rebuild procedure.

## Design Notes

- **The collection IS the test suite** for the client journeys — assertions live in the `.bru` files (declarative blocks + JS throws); this checklist pins their CONTENT (B-range) so a weakened collection cannot silently pass SM-1.
- **apiId propagation by command substitution**, never hand-copy: `cd bruno && bru run --env Local --env-var "gatewayBaseUrl=$(cd ../terraform && terraform output -raw gateway_base_url)"` (bru 4.x runs only at a collection root). Committed files stay environment-neutral (`REPLACE_WITH_API_ID` placeholder remains canonical in git) — closes epic-3 retro AI-11 without committing a machine-specific id.
- **State-file dance is load-bearing**: two checkouts must never hold divergent copies of live state. Copy `terraform/terraform.tfstate*` from main INTO the worktree BEFORE any plan/apply; copy BACK after (the spec's frozen Always tier scopes the mandatory set to the state files — `.terraform/` is optional since ci-local re-runs `terraform init`). Verify parity via resource-count before/after.
- **Destroy-on-floci risk retired by precedent**: CI runs `terraform destroy` on every pipeline run (docs/ci.md) — the emulator handles teardown cleanly.
- **Traceability source mapping** (CloudWatch is emulated — floci emits Lambda logs to container stdout): SFN `list_executions` → `describe_execution` (name/input carry videoId); `smoke-capture-queue` receives `video.processed` (EventBridge leg proof); S3 GetObject on `processedKey` (transcode leg proof); `docker compose logs floci` for invocation logs.

## Risk Assessment

| Risk | Impact | Likelihood | Test response |
|------|--------|-----------|---------------|
| Stale/divergent tfstate in worktree → apply recreates everything or corrupts live state | High (destroys the running stack's identity) | Medium (two checkouts, one state) | L2 parity check before destroy; copy-back step L9 |
| Collection weakened (poll removed, videoId pin dropped, backend URL sneaks in) → SM-1 passes falsely | High (definition-of-done proven by a hollow suite) | Medium (no automated guard existed) | B-range content pins checked line-by-line |
| Hand-copied apiId drifts after fresh apply (retro AI-11 recurrence) | Medium (404s against fresh env) | Medium if docs still say "replace the placeholder" | R4: one-liner with command substitution is THE documented path |
| ASL edited later without `-replace` → apply reports "no changes", old machine runs | High (silent stale behavior) | Medium (floci applies ASL in place) | R3: caveat present near the state-machine docs |
| `aws` CLI creeps into setup/teardown docs | Medium (violates FR-24/AD-8) | Low (shim broken anyway) | R6 rg gate |
| Async lag flakiness (history/search polled too early) | Low (polls already handle) | Low (existing 120 s loops) | B4/B5 pin the loops' existence |

## Bruno Checklist (collection content guards — verified existing, regression pins)

- [x] B1 exactly four requests, seq order 1–4: `upload-video.bru`, `upload-malformed.bru`, `history-query.bru`, `search-video.bru`
- [x] B2 upload asserts 200 + `videoId`, chains `videoId`/`videoTitle` vars for downstream requests
- [x] B3 malformed-upload asserts 400 + `res.body.error`
- [x] B4 history request uses poll-with-timeout pre-request loop (~120 s deadline, `bru.sleep` retry), final response asserted
- [x] B5 search request polls until the uploaded `videoId` appears in results (client-side hit condition is strict `r.videoId === videoId` equality; title-substring matching is server-side semantics), final response asserted
- [x] B6 every request URL derives ONLY from `{{gatewayBaseUrl}}` — zero direct backend URLs
- [x] B7 `environments/Local.bru` keeps the `REPLACE_WITH_API_ID` placeholder (environment-neutral in git)

## README Checklist (R-range — RED phase: R2/R3/R4/R5 fail against current tree; R1 is a regression pin)

- [x] R1 quick start shows fixed bring-up order: compose up → health wait → `terraform init` + `apply`
- [x] R2 clean-rebuild/teardown subsection documents full reproducibility: `terraform destroy` → `docker compose down` → fresh bring-up (the destroy+apply procedure SM-1 demands)
- [x] R3 `-replace` caveat present near the state-machine/floci-facts section (rg `\-replace` ≥1 hit)
- [x] R4 Bruno section's PRIMARY path is the one-liner: `cd bruno && bru run --env Local --env-var "gatewayBaseUrl=$(cd ../terraform && terraform output -raw gateway_base_url)"` — placeholder replacement demoted to optional; no hand-copy required
- [x] R5 end-to-end verification (SM-1) subsection: exact reproducibility procedure + where each service's traceability evidence lives
- [x] R6 no `^\s*aws\s` shell lines anywhere in README (prose "no aws cli" mentions fine)

## Terraform Checklist (structural — expect ZERO change)

- [x] X1 `git diff main --stat -- terraform/ bruno/` shows NO terraform change and NO Bruno change beyond the single sanctioned `search-video.bru` fix recorded under Deviations
- [x] X2 outputs `api_id` + `gateway_base_url` declared (upload.tf:175–181) — the one-liner's source
- [x] X3 `terraform fmt -check -recursive` + `validate` green post-run

## Live Verification Checklist (floci)

- [x] L1 floci healthy (`curl -sf http://localhost:4566/_localstack/health`)
- [x] L2 state dance IN: worktree `terraform/` seeded from main checkout BEFORE any plan/apply; resource-count parity recorded
- [x] L3 `terraform destroy` green (full environment down)
- [x] L4 `terraform apply` green (full recreate from the same configuration — no manual steps between)
- [x] L5 `terraform output -raw gateway_base_url` resolves to the recreated apiId
- [x] L6 FULL Bruno collection passes: `bru run` exit 0, all four requests green against the FRESH environment
- [x] L7 derived-surface oracle: the collection's one upload left a PROCESSED-terminal history entry and a search hit, both through the gateway (ad-hoc direct table reads supplement, never replace)
- [x] L8 traceability evidence recorded PER SERVICE: API Gateway (passing collection), Lambda (container log lines), Step Functions (`describe_execution` input carries the videoId), EventBridge (`video.processed` on smoke-capture-queue), S3 (processed object readable), DynamoDB (metadata/history/index records)
- [x] L9 state dance BACK: worktree `terraform.tfstate*` copied to main checkout post-run

## Gate

- [x] G1 `uv run --with ruff ruff check lambdas/ --select E,F` + `uv run --with 'pytest>=8.0' pytest lambdas/ -q` — untouched, still green
- [x] G2 `bash scripts/ci-local.sh` — 5 stages green

## Red-Green Workflow

1. RED: mint this checklist → R-ranges fail against current tree (rg evidence below)
2. GREEN docs: README edits until R1–R6 pass
3. Live: docker compose up → L1/L2 → destroy+apply (L3–L5) → bru run (L6–L7) → traceability (L8)
4. State sync back (L9), X/G gates, mark boxes with evidence

## RED Phase Evidence (2026-08-24)

- `rg -n "\-replace" README.md` → exit 1, no hits (R3 RED)
- Teardown is a bare one-liner at README.md:90 — no clean-rebuild subsection (R2 RED)
- Bruno primary path still instructs placeholder hand-edit (R4 RED)
- No SM-1/e2e verification section exists (R5 RED)
- R1/B1–B7/X2 verified ALREADY PASSING — they are regression pins, not gaps
- Post-edit B-range re-verification (after the sanctioned search-video.bru fix): B5's `{{searchTitle}}` interpolation + `res.data || res.body` containment proof confirmed present and exercised by the passing L6 runs; B6 gateway-only URLs unchanged; B7 placeholder intact (`git diff main -- bruno/` touches only search-video.bru)

## Build Evidence (bmad-build, 2026-08-24)

- **Files:** `README.md` (clean-rebuild subsection :90, one-liner Bruno path :173, SM-1 e2e section :181 with per-service traceability table, `-replace` caveat :278, stale Next pointer fixed) + `bruno/search-video.bru` (sanctioned live-gap fix, see Deviations). Terraform: ZERO diff (X1 ✓).
- **R-ranges:** R3 rg hit at README.md:278; R6 no `^\s*aws\s` lines; R2/R4/R5 sections verified present.
- **L1/L2:** floci 1.7.0 healthy; state seeded from main (75=75 parity) BEFORE any plan/apply.
- **L3/L4 (first cycle):** destroy wiped state (fresh emulator — resources pre-deceased with the previous Docker daemon; teardown-under-load precedent = CI's per-run destroy); apply recreated ALL **75** resources; new apiId `bb0cffd518`.
- **L5:** `terraform output -raw gateway_base_url` resolved each cycle.
- **L6 (canonical):** after migrating to the pinned compose project (`serverless-video-processing`), full pristine cycle: destroy → apply (**75**, apiId `ab00cc40f2`) → `bru run --env Local --env-var "gatewayBaseUrl=$(...)"` → **4/4 requests PASS, 10/10 assertions, exit 0** against the fresh environment. One upload (`740d8c6a`) produced: metadata PROCESSED, history entry PROCESSED, search-index hit — all surfaced through the gateway by the collection's own assertions (L7).
- **L8 traceability (single pristine video):** API Gateway = passing collection; Lambda = container log chain (upload-handler "upload accepted" → sfn-trigger-shim "execution started eb-59a2d0e5" → transcode "transcode complete"); Step Functions = `describe_execution eb-59a2d0e5…` SUCCEEDED with the videoId in its input; EventBridge = exactly 1 `video.processed` captured on smoke-capture-queue; S3 = `processed/740d8c6a…/sample.mp4` head OK (26 bytes); DynamoDB = metadata/history/index reads above.
- **L9:** worktree tfstate copied back to main checkout; parity 75=75.
- **Gates:** ruff clean; `pytest lambdas/ -q` → **393 passed**; full `ci-local.sh` (git-bash; WSL has no bash) → **all 5 stages green**, integration **18 passed** against the redeployed stack.

## Deviations

- **Bruno fix (Ask First sanctioned mid-build):** Bruno CLI 4.0.0 double-encodes a static `%20` in URL templates — `search-video.bru`'s final request searched the literal string `My%20Video` (server log: `title='My%20Video' results=0`) while the JS-encoded poll probes hit correctly; additionally the post-response containment proof read `res.data`, which is undefined in 4.x post-response context (`.body` is the shape there), false-negating even correct responses. Fix (user-approved var-interpolation option): title travels as `{{searchTitle}}` computed via `encodeURIComponent` in the pre-request script, and the post-response check accepts `res.data || res.body`. Poll-with-timeout conventions, gateway-only URL, and the env placeholder all preserved. This is the ONLY collection change; X1 otherwise empty.
- Session nuance recorded for reviewers: the first destroy ran against an already-empty emulator (previous stack died with the Docker daemon), so its observable effect was the state wipe; the canonical destroy→apply→collection proof was re-executed pristine under the pinned compose project name.
