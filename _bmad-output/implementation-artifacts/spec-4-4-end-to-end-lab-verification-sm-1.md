---
title: 'Story 4.4 — End-to-End Lab Verification (SM-1)'
type: 'feature'
created: '2026-08-24'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'c5c2b7d411f21924d650d9da5ae0df81b7177d10'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing has yet proven the lab's definition of done: that from a clean `terraform destroy` + `apply` the entire pipeline reproduces with zero manual glue, and that one upload demonstrably flows through every target service to a `PROCESSED` metadata record, a status-history entry, and a search hit — all queryable through the gateway (SM-1). Known gaps block it: the README lacks the `-replace` caveat for ASL changes and a documented clean-rebuild procedure, and the Bruno collection's `REPLACE_WITH_API_ID` placeholder has no documented one-command propagation from `terraform output` (epic-3 retro AI-11).

**Approach:** A verification story — no new infrastructure. Mint the ATDD checklist first (B-range Bruno-content guards, R-range README checks, X-range Terraform structural checks, L-range live checks), close the documentation gaps in `README.md`, then execute the live proof on floci: destroy → apply → full `bru run` collection (gateway URL only, poll-with-timeout) → ad-hoc traceability inspection (Step Functions execution history, event records, logs) → record evidence.

## Boundaries & Constraints

**Always:**
- RED before GREEN: mint `atdd-checklist-4-4` (following the `atdd-checklist-4-3` conventions) before editing docs; R-range checks must FAIL against the current tree (`-replace` caveat absent today).
- The rebuild procedure is exactly: floci healthy (`docker compose up -d --wait`) → `terraform apply` after `terraform destroy` → `bru run --env Local --env-var "gatewayBaseUrl=$(cd terraform && terraform output -raw gateway_base_url)"`. No step outside those commands; no `aws` CLI anywhere (FR-23, FR-24, NFR-6, NFR-8).
- apiId propagation is by command substitution from the existing `gateway_base_url` output at run time — committed files stay environment-neutral (`Local.bru` keeps its placeholder).
- The full collection = all four requests in seq order (upload → upload-malformed → history-query → search-video); history/search legs use their existing poll-with-timeout loops; final search assertion pins the uploaded videoId.
- Traceability evidence recorded per target service: API Gateway (the passing collection itself), Lambda + Step Functions (`list_executions`/`describe_execution` on the state-machine name output), EventBridge (event received on `smoke-capture-queue`), S3 (processed object readable), DynamoDB (metadata/history/index records via gateway surfaces plus permitted ad-hoc reads).
- Copy `terraform/terraform.tfstate*` from the main checkout into the worktree BEFORE any destroy/apply, and copy back AFTER — the worktree state becomes canonical-live; the main checkout must never hold stale live state.

**Ask First:**
- Any change to a `.tf` file or to any `.bru` request's content (verification story: expect ZERO infra diff; only touch Bruno if the live run exposes an assertion gap, preserving gateway-only + poll conventions and the placeholder).
- Installing missing local tooling beyond documenting it as a prerequisite (e.g. bru CLI via npm).
- Wiring destroy or the Bruno run into `scripts/ci-local.sh` or CI.
- Any direct-table assertion replacing a gateway query in the collection.

**Never:**
- No new Lambda, table, queue, rule, route, role, or any Terraform resource — `git diff main -- terraform/` must be EMPTY.
- No `aws` CLI commands in any setup/teardown/procedure documentation.
- No direct backend URLs in Bruno requests — gateway data plane (`localhost:4566/_aws/execute-api/...`) only.
- No fixed sleeps replacing poll-with-timeout.
- No auth, no new dependencies, no retries beyond what the polls already give.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh rebuild | complete config, floci running; `terraform destroy` then `terraform apply` | every resource recreated; `terraform output gateway_base_url` resolvable; no manual glue between steps | apply failure = loud stop, no partial workaround |
| Async lag | history/search queried immediately after upload | poll-with-timeout retries until PROCESSED terminal entry / search hit appears; 120s ceiling | timeout fails the request's assertions loudly |
| Search miss | title matching nothing | HTTP 200 with empty results list (never an error) | N/A |
| Malformed upload leg | multipart without file part | HTTP 400 with `{"error": ...}` body; journey continues to next requests | N/A |
| Placeholder drift | committed `Local.bru` still carries `REPLACE_WITH_API_ID` | run-time env-var override supplies the real base URL; committed file byte-unchanged | N/A |

</frozen-after-approval>

## Code Map

- `README.md:77-90` -- Quick start (compose up → health → apply): bring-up order exists but teardown is a bare one-liner (:90); `-replace` caveat ABSENT repo-wide (rg confirms); Bruno section :144-158 documents manual `bru run` with hand-copied apiId — replace with one-liner.
- `bruno/environments/Local.bru:1-3` -- `gatewayBaseUrl: http://localhost:4566/_aws/execute-api/REPLACE_WITH_API_ID/local`; stays as-is.
- `bruno/upload-video.bru` / `upload-malformed.bru` / `history-query.bru` / `search-video.bru` -- the collection; poll loops at history-query.bru:22-32 and search-video.bru:27-37 (120s deadline, `bru.sleep(2000)`); var chaining upload:23-29.
- `terraform/upload.tf:175-181` -- `api_id` and `gateway_base_url` outputs (the one-liner's source).
- `scripts/ci-local.sh:25-32` -- stage-5 pattern: pinned `COMPOSE_PROJECT_NAME`, compose up --wait, apply, `GATEWAY_BASE_URL="$(terraform output -raw gateway_base_url)"` — same substitution idiom reused in docs.
- `docs/ci.md:87-88` -- CI already runs `terraform destroy` (if: always()) — proves destroy works cleanly against floci.
- `tests/integration/conftest.py:421-434`, `:28-56` -- gateway URL resolution template + resource-name constants for ad-hoc oracle reads; `smoke-capture-queue` for event-record evidence.
- `lambdas/README.md:112-116, 246-250` -- boto3 ad-hoc invoke patterns (local aws shim broken — why docs must never suggest aws CLI).
- `_bmad-output/test-artifacts/atdd-checklist-4-3-admin-index-rebuild.md` -- checklist conventions to clone (T/B/X/L ranges, gate section, evidence boxes).
- Worktree state caveat precedent: spec-4-3 Design Notes ("copy state before plan/apply").

## Tasks & Acceptance

**Execution:**
- [x] `_bmad-output/test-artifacts/atdd-checklist-4-4-end-to-end-lab-verification-sm-1.md` -- mint from the 4-3 conventions: B-range (collection content: four requests, seq order, polls, gateway-only, placeholder intact), R-range (README: bring-up order, clean-rebuild section, `-replace` caveat, one-liner bru run, no `^\s*aws\s` shell lines, teardown documented), X-range (zero terraform diff vs main; outputs exist), L-range (fresh rebuild → collection green → traceability evidence per service), gate section -- RED contract: R-ranges fail now.
- [x] `README.md` -- add: clean-rebuild/teardown subsection (destroy → down → fresh bring-up incl. health wait); `-replace` caveat near the state-machine section; Bruno section gains the one-liner run command with command-substituted base URL; new "End-to-end verification (SM-1)" subsection stating the exact reproducibility procedure + where each service's traceability evidence lives; keep zero `aws` CLI shell lines.
- [x] Live fresh rebuild on floci (state-file dance per Always-tier) -- `terraform destroy` then `apply` in the worktree; health check; outputs resolve.
- [x] Live full collection -- `bru run` (one-liner above) passes all four requests' assertions against the fresh environment; record summary counts.
- [x] Live traceability evidence -- SFN execution for the uploaded videoId; `video.processed` event captured; processed object in S3; Lambda invocation logs (container/CloudWatch-emulated); record per-service evidence lines in the checklist L-range.
- [x] Sync worktree `terraform/terraform.tfstate*` back to main checkout post-run.
- [x] Checklist boxes + sprint-status `4-4-end-to-end-lab-verification-sm-1` updated with evidence.

**Acceptance Criteria:**
- Given floci running and the complete Terraform configuration, when `terraform destroy` then `terraform apply` runs, then the entire environment rebuilds from the same configuration with no manual steps and no `aws` CLI in the procedure (FR-23, FR-24, NFR-6, NFR-8).
- Given the fresh environment, when the complete Bruno collection runs via the documented one-liner, then every request passes its assertions and the one upload yields a PROCESSED-visible status-history entry and a search hit through the gateway — SM-1's three derived surfaces.
- Given that run, when inspected ad-hoc, then the video's path is traceable through Step Functions execution history, event records, and Lambda logs, demonstrably exercising API Gateway, Lambda, S3, DynamoDB, EventBridge, and Step Functions (SM-1, NFR-5).
- Given the README, when reviewed, then it documents the fixed bring-up order, the `_aws/execute-api` URL with apiId from Terraform output, the `-replace` caveat for ASL changes, and contains no `aws` CLI in setup/teardown (FR-24, AD-8/AD-9).

## Spec Change Log

- 2026-08-25 (code review): Verification one-liner corrected — bru CLI 4.x executes only at a collection root, so the documented command must run from `bruno/`: `cd bruno && bru run …` with output path `../terraform`. README updated likewise (Bruno section + SM-1 block); proven live post-fix (4/4 requests, 10/10 assertions, exit 0). The frozen Always-tier wording (:24) stays verbatim — its arg-less form remains accurate when executed from `bruno/`.

## Design Notes

- State-file dance: two checkouts must never point at divergent copies of live state. Copy IN before destroy (else apply tries to recreate everything), copy BACK after (worktree removal would otherwise orphan the live state). Verify parity with `terraform state list | wc -l` before/after.
- The one-liner closes epic-3 retro AI-11 without committing a machine-specific id: `Local.bru` remains the git-canonical neutral file; reality flows through `--env-var` at run time. Same idiom ci-local stage 5 already uses. Bruno CLI 4.x assumed (script/response APIs differ in older lines).
- Destroy-on-floci risk is retired by precedent: CI destroys on every run (docs/ci.md:87-88).
- Traceability source mapping: SFN `list_executions` filtered on the started-after window → `describe_execution` (name input shows videoId); `smoke-capture-queue` receives `video.processed` (EventBridge proof); S3 GetObject on `processedKey` (transcode proof); floci emits Lambda logs to container stdout (`docker compose logs floci`) since CloudWatch is emulated.
- bru CLI prerequisite documented, not installed silently: `npm install -g @usebruno/cli`.

## Verification

**Commands:**
- `rg -n "\-replace" README.md` -- expected: ≥1 hit (caveat present)
- `rg -n "^\s*aws\s" README.md` -- expected: no hits (no aws CLI shell lines)
- `(cd terraform && terraform output -raw gateway_base_url)` -- expected: resolvable localhost URL
- `cd bruno && bru run --env Local --env-var "gatewayBaseUrl=$(cd ../terraform && terraform output -raw gateway_base_url)"` -- expected: all requests pass, exit 0 (bru 4.x runs only at a collection root)
- `git diff main --stat -- terraform/ bruno/` -- expected: EMPTY (unless an Ask First triggered)
- `bash scripts/ci-local.sh` -- expected: 5 stages green
- Ad-hoc boto3 reads per Design Notes mapping -- expected: SFN execution, captured event, S3 object, log lines found

## Suggested Review Order

**The sanctioned collection fix (live-run gap — the only code change)**

- Interpolated `{{searchTitle}}` carries the encoded title — static `%20` double-encodes under Bruno 4.x
  [`search-video.bru:8`](../../bruno/search-video.bru#L8)

- Encoding happens once in JS, where it is observable and testable
  [`search-video.bru:28`](../../bruno/search-video.bru#L28)

- Containment proof reads both response shapes so a correct pass can't false-negative
  [`search-video.bru:52`](../../bruno/search-video.bru#L52)

**The documented procedure (README — what SM-1 now guarantees)**

- Clean-rebuild cycle: destroy → down → fresh bring-up, no manual glue
  [`README.md:90`](../../README.md#L90)

- Bruno one-liner with version floor + PowerShell variant — apiId never hand-copied
  [`README.md:173`](../../README.md#L173)

- SM-1 procedure made self-contained (destroy included) + per-service traceability table
  [`README.md:184`](../../README.md#L184)

- `-replace` caveat for in-place ASL updates, taint mention dropped
  [`README.md:281`](../../README.md#L281)

**Verification artifacts**

- ATDD checklist: B/R/X/L ranges, live evidence incl. the pristine 75-resource rebuild + trace
  [`atdd-checklist-4-4:68`](../test-artifacts/atdd-checklist-4-4-end-to-end-lab-verification-sm-1.md#L68)

- Deviations section records the Bruno gap, user approval, and session nuances
  [`atdd-checklist-4-4:117`](../test-artifacts/atdd-checklist-4-4-end-to-end-lab-verification-sm-1.md#L117)

**Peripherals**

- Sprint tracker synced to review
  [`sprint-status.yaml:60`](./sprint-status.yaml#L60)

### Review Findings

**Code review 2026-08-24 — full mode; layers: blind-hunter, edge-case-hunter, verification-gap, acceptance-auditor.**

- [x] [Review][Patch] Documented one-liner fails on bru CLI 4.x [README.md:172,197; spec Verification] — `bru run bruno …` errors "You can only run at the root of a collection" (folder arg unsupported; collection root is `bruno/`). Corrected everywhere to `cd bruno && bru run --env Local --env-var "gatewayBaseUrl=$(cd ../terraform && terraform output -raw gateway_base_url)"`; PowerShell variant updated to match; proven live post-fix (4/4 requests, 10/10 assertions, exit 0 under git-bash; pwsh two-step variant also green). Discovered while validating the P-patches against the live stack.

- [x] [Review][Decision] (sanctioned in review — applied) search-video.bru docs block still instructs hand-editing `Local.bru` — bruno/search-video.bru:82–85 tells users to "re-read it from `terraform output gateway_base_url` and update it there", contradicting README.md's new position that the one-liner is the supported path and the placeholder "never needs hand-editing". Any `.bru` edit is Ask First under the frozen spec, so even a comment-only alignment needs explicit sanction.
- [x] [Review][Patch] `-replace` caveat documents a non-existent resource address [README.md:281] — says `aws_sfn_state_machine.processing_state_machine`; actual resource is `aws_sfn_state_machine.processing` (terraform/processing.tf:174). The command fails with "Resource address not found" when followed; every other artifact (spec-2-2:29, spec-2-3:102, ARCHITECTURE-SPINE:103) uses `.processing`.
- [x] [Review][Patch] Checklist B5 wording imprecise [atdd-checklist-4-4:53] — "(substring title match)" describes server-side matching; the client-side poll/hit condition is strict `r.videoId === videoId` equality (bruno/search-video.bru:32-33).
- [x] [Review][Patch] bru CLI version floor unverifiable [README.md:171-175] — README demands 4.x but omits the actually-tested version (4.0.0, recorded only in checklist Deviations); a user on an older CLI hits the exact double-encoding/res.data failures with no diagnostic anchor.
- [x] [Review][Patch] Traceability table lacks evidence-perishability warning [README.md:200-208] — smoke-capture-queue messages and SFN execution history vanish on the next destroy/rebuild cycle the same section encourages; nothing tells the reader to inspect before re-running.
- [x] [Review][Defer] No machine-executed guard over collection content / SM-1 ritual [scripts/ci-local.sh, bruno/] — B-range pins are hand-checked markdown; X1 diff gate is review-time only; destroy→apply→bru is never scripted. Recorded in deferred-work.md (tail entry of this branch) and ratified by this review's verification-gap layer; ci-local wiring reserved Ask First by the frozen spec — deferred, pre-existing.
- [x] [Review][Defer] README rebuild procedure omits two-checkout tfstate hazard [README.md:90-105] — the spec made copy-state-between-checkouts load-bearing for worktree runs, but the documented destroy→apply cycle says nothing about second clones; run from another checkout it silently recreates everything under a new apiId — deferred, pre-existing.
- [x] [Review][Defer] Checklist gate conventions are presence-only [atdd-checklist-4-4:61,64] — R3 proves the string `-replace` appears but not that the flagged Terraform address exists (this review caught README.md:281 naming a resource that isn't declared); R6 `^\s*aws\s` misses inline usage (`cd x && aws …`, `$(aws …)`) — deferred, pre-existing.
