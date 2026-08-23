---
title: 'Integration test suite — Step 2: delete smoke'
type: 'chore'
created: '2026-08-23'
status: 'done'
route: 'one-shot'
baseline_commit: '2ff7aaf70ba3e071da3844a5d5d6a4bfb80095d8'
context:
  - '{project-root}/_bmad-output/test-artifacts/integration-test-plan.md'
---

# Integration test suite — Step 2: delete smoke

## Intent

**Problem:** Step 1 swapped the CI stage 5 gate from the smoke Lambda to the
`tests/integration/` pytest suite (T1–T10) and passed its gate (green CI on
PR #19). The smoke Lambda and every stale reference to it are now dead weight
that misleads future readers about how the lab is verified.

**Approach:** Delete `lambdas/smoke/`, scrub live smoke references from docs
and code comments (keeping the `smoke_capture` resource addresses intact per
plan decision D4), and close the six smoke-scenario deferred-work items as
superseded by T3–T8 — not done.

## Suggested Review Order

**The deletion itself**

- The whole fixture goes; nothing imports or invokes it anymore.
  [`lambdas/smoke/handler.py`](../../lambdas/smoke/handler.py) (deleted, 470 lines)

**Deferred-work closures (§6.3)**

- Six smoke-scenario items marked superseded/obsolete with T-test pointers.
  [`deferred-work.md:11`](../../_bmad-output/implementation-artifacts/deferred-work.md#L11)

**Doc scrub — living references**

- Story 1.2 status paragraph reworded: fixture retired, integration suite is the gate.
  [`README.md:332`](../../README.md#L332)
- Smoke fixture section deleted; boto3-availability note kept and re-sourced.
  [`lambdas/README.md:40`](../../lambdas/README.md#L40)
- Stage-5 naming fixed (smoke → integration) in the validation gate block.
  [`AGENTS.md:57`](../../AGENTS.md#L57)
- Header comment: docker is for the integration stage.
  [`ci-local.sh:4`](../../scripts/ci-local.sh#L4)

**Comment-only pointer fixes (smoke.tf → integration.tf / upload.tf)**

- boto3-availability docstring re-sourced to the integration suite.
  [`clients.py:11`](../../lambdas/_shared/clients.py#L11)
- Zip-layout pointer now cites upload.tf.
  [`conftest.py:4`](../../lambdas/_shared/tests/conftest.py#L4)
- REUSES/capture-rule comments repointed.
  [`history.tf:11`](../../terraform/history.tf#L11)
  [`processing.tf:24`](../../terraform/processing.tf#L24)
  [`upload.tf:7`](../../terraform/upload.tf#L7)

**Deliberately untouched**

- `terraform/integration.tf` resource addresses (`smoke_capture`,
  `smoke-capture-queue`) — D4 mandates identical addresses; no state migration.
- `tests/integration/`, CI wiring — Step 1 territory.
- Historical `_bmad-output/` records (specs, retros, sprint-status, epic
  contexts, architecture spine) — they document what was true then.
