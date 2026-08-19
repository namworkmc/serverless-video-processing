# CI Pipeline Guide

GitHub Actions pipeline for the serverless-video-processing floci lab.

- **Config:** `.github/workflows/ci.yml`
- **Local mirror:** `scripts/ci-local.sh` (same commands, same order)
- **Design record:** `_bmad-output/test-artifacts/ci-pipeline-progress.md`

## Stages

| Job | What it does | Gate |
| --- | --- | --- |
| `lint` | `ruff check lambdas/ --select E,F` + `terraform fmt -check -recursive` | both must pass |
| `unit-test` | `pytest lambdas/` — shared access layer suite (27 tests) | 100% pass |
| `terraform-validate` | `terraform init -backend=false` + `terraform validate` | valid config |
| `smoke` | floci via `docker compose up -d --wait` → `terraform apply` → invoke `smoke` Lambda through floci's Lambda API → assert `statusCode==200 && body.all_pass` → `terraform destroy` (always) | smoke report all_pass |

Job graph: `lint → {unit-test, terraform-validate} → smoke`.

## Triggers

- Push to `main`
- Pull requests targeting `main`
- Manual (`workflow_dispatch`)

Concurrency: one run per ref, in-progress runs cancelled.

## Secrets

**None required.** floci uses dummy credentials (`test`/`test`), Terraform state
is local, and the smoke invoke goes through floci's unauthenticated Lambda API.
If real-AWS deployment is added later, that gets its own workflow with
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (or OIDC) as repository secrets —
never hardcoded.

## Toolchain pins

- Python 3.11 (Lambda runtime parity), installed via `astral-sh/setup-uv@v6`
- Terraform 1.6.1 via `hashicorp/setup-terraform@v3` (matches `required_version >= 1.6.0`)
- ruff and pytest are pulled ad-hoc by `uv run --with` — no lockfile needed yet

## Smoke stage details

The smoke Lambda (`lambdas/smoke/handler.py`, declared in `terraform/smoke.tf`)
exercises the shared access layer inside floci's real Docker runtime:
create, idempotent create, legal/illegal transitions, re-assertion, envelope
determinism, not-found. It self-cleans its fixed test record, so reruns are
safe. The CI invoke uses floci's Lambda REST API directly
(`POST http://localhost:4566/2015-03-31/functions/smoke/invocations`) — no
`aws` CLI in the pipeline, matching the lab's Terraform-only rule.

`terraform destroy` runs with `if: always()` so a failed smoke never leaves
state behind on the runner. On failure, floci logs are captured and uploaded.

## Deliberate omissions

- **No sharding** — 27 unit tests run in one process; sharding adds overhead, not speed.
- **No burn-in loop** — burn-in targets UI flakiness; this is a backend-only stack.
- **No contract tests** — no Pact artifacts in the repo.
- **No deploy stage** — the lab deploys locally via `terraform apply`; there is no remote environment.

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `docker compose up -d --wait` times out | floci image pull slow on cold runner cache; rerun. Locally: check Docker Desktop is running. |
| Smoke invoke returns 500 with `all_pass: false` | Read the scenario report in the response body — it names the failing check. Pull `smoke-failure` artifact for floci logs. |
| `terraform apply` fails with `InvalidClientTokenId` | A service endpoint is missing from the provider `endpoints{}` block (spine AD-8 fact 3). Add it to `terraform/providers.tf`. |
| Lambda invoke hangs / times out | floci needs the Docker socket to spawn Lambda containers; on a self-hosted runner verify `/var/run/docker.sock` access. GitHub-hosted `ubuntu-latest` provides it. |
| Ruff fails on a new file | Run `uv run --with ruff ruff check lambdas/ --select E,F` locally and fix. |

## Badge (optional)

```markdown
[![CI](https://github.com/namworkmc/serverless-video-processing/actions/workflows/ci.yml/badge.svg)](https://github.com/namworkmc/serverless-video-processing/actions/workflows/ci.yml)
```
