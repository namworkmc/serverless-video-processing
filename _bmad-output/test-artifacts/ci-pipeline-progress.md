---
stepsCompleted: ['step-01-preflight', 'step-02-generate-pipeline', 'step-03-configure-quality-gates', 'step-04-validate-and-summary']
lastStep: 'step-04-validate-and-summary'
lastSaved: '2026-08-19'
---

# CI Pipeline Setup — Progress Record

Workflow: `bmad-testarch-ci` (TEA module) · Run by Murat (Test Architect) with
Winston (System Architect) ratifying the deploy-stage design against the
architecture spine.

## Step 1 — Preflight

| Check | Result |
| --- | --- |
| Git repository | ✅ `.git/` present, remote `origin` → github.com/namworkmc/serverless-video-processing |
| Test stack type | ✅ `backend` (auto-detected: Python Lambda handlers + Terraform; no frontend/mobile indicators) |
| Test framework | ✅ pytest ≥ 8.0 (`requirements-dev.txt`); suite at `lambdas/_shared/tests/` with conftest aliasing `_shared` → `shared` |
| Local tests pass | ✅ `uv run --with 'pytest>=8.0' pytest lambdas/ -q` → **27 passed** |
| CI platform | ✅ `github-actions` (remote is github.com; no pre-existing CI config) |
| Environment context | ✅ Python 3.11 (Lambda runtime parity), Terraform 1.6.1 (`required_version >= 1.6.0`), uv as installer |
| TEA config flags | ✅ `tea_use_playwright_utils` / `tea_use_pactjs_utils` irrelevant (no Playwright/Pact artifacts in repo — skipped, not wired); `ci_platform: auto` → github-actions |

Additional preflight probes (run 2026-08-19):

- `terraform validate` → **Success! The configuration is valid.**
- `terraform fmt -check -recursive` → failed on `providers.tf` → **fixed** with `terraform fmt` (now clean; gate is green from the first run)
- `ruff check lambdas/` (default rules) → 10 style findings; `--select E,F` (errors + pyflakes only) → **All checks passed**. Gate pinned to E,F so style opinions don't block the lab; broadening the ruleset is a future choice.
- Live smoke proof against running floci: `POST /2015-03-31/functions/smoke/invocations {"scenario":"all"}` → `statusCode 200, all_pass true, boto3 available, 7 scenarios` — the exact command the pipeline's smoke stage runs.

## Step 2 — Pipeline Generation

- Platform: `github-actions` → output `.github/workflows/ci.yml` (adapted from `github-actions-template.yaml` for a backend-only Python/Terraform stack)
- Stages: `lint` → `unit-test` + `terraform-validate` (parallel) → `smoke`
- No direct `${{ inputs.* }}` / `github.event.*` interpolation in any `run:` block (no reusable-workflow inputs exist yet; the rule is documented in the file header for future extensions)
- Dependency handling via `uv run --with` (no lockfile to cache yet); Terraform provider plugin cache via `hashicorp/setup-terraform` defaults
- Artifacts: `pytest-report.xml` (JUnit) on unit-test failure; `floci-logs.txt` on smoke failure; 30-day retention

### Deploy stage (the "CD") — architecture ratification

Winston's read of the spine: AD-9 fixes the bring-up order
`docker compose up → terraform apply → exercise via gateway`, local state,
Terraform-only (no `aws` CLI for infra). The CD stage is therefore the lab
bring-up itself, executed ephemerally on the runner:

1. `docker compose up -d --wait` (floci 1.6.0, healthcheck-gated)
2. `terraform init` + `terraform apply -auto-approve`
3. Invoke the `smoke` Lambda through floci's Lambda REST API and assert the
   structured report (`statusCode==200 && body.all_pass`) — this exercises the
   shared access layer inside floci's real Docker runtime, which is the deepest
   verification the lab defines
4. `terraform destroy -auto-approve` with `if: always()` — the runner
   environment is disposable; no state persists

Deferred by design: real-AWS deployment (spine Deferred list), remote state,
presigned-URL v2 ingest. When real AWS enters, it gets a separate workflow with
OIDC/secret-based credentials — not this one.

## Step 3 — Quality Gates & Notifications

- **Burn-in:** skipped — backend-only stack; burn-in targets UI flakiness
  (per workflow stack-conditional rule). Documented in `docs/ci.md`.
- **Quality gates:** P0 = 100% pass enforced structurally — every job fails
  the pipeline on any test failure; no `continue-on-error` on any test step.
  The smoke stage asserts the full scenario report, and the handler itself
  fails closed (500) on any scenario miss.
- **Contract testing:** none — no Pact artifacts in repo (flag on, relevance
  gate off).
- **Notifications:** GitHub-native only (PR checks + email on failure). No
  Slack/webhook configured — single-developer lab; add if the team grows.

## Step 4 — Validation & Summary

Checklist (`checklist.md`) outcome:

- ✅ Config created at platform-correct path, YAML valid (parsed locally)
- ✅ Correct framework commands for detected backend/pytest stack
- ✅ Sharding: intentionally N/A (27 tests; checklist allows suite-size-appropriate count — 1)
- ✅ Burn-in: intentionally skipped (backend-only, documented)
- ✅ Caching: uv-managed ad-hoc deps (no lockfile); terraform plugin cache via setup-terraform
- ✅ Artifacts: failure-only, unique names, 30-day retention, no sensitive data
- ✅ Retry: none configured — suite is deterministic and fast; retries would mask real flakiness (revisit if runner-level flakes appear)
- ✅ Helper script: `scripts/ci-local.sh` mirrors all four stages
- ✅ Docs: `docs/ci.md` (guide + troubleshooting + secrets note); secrets checklist folded in — **zero secrets required**
- ✅ Security: no credentials in config; no unsafe context interpolation in `run:` blocks
- ⏳ First CI run: pending user push (post-workflow action)

### Files produced

| File | Purpose |
| --- | --- |
| `.github/workflows/ci.yml` | The pipeline |
| `scripts/ci-local.sh` | Local mirror of all stages |
| `docs/ci.md` | Pipeline guide, secrets note, troubleshooting |
| `_bmad-output/test-artifacts/ci-pipeline-progress.md` | This record |

Also touched: `terraform/providers.tf` (fmt fix), `ARCHITECTURE-SPINE.md`
Deferred entry updated to record the CI scope decision.

### Post-workflow actions (user)

1. Commit + push → first CI run triggers on `main`
2. Watch the four jobs; smoke is the long pole (~floci pull + apply)
3. Optional: add the badge from `docs/ci.md` to README
