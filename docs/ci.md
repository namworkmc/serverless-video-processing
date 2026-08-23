# CI Pipeline Guide

GitHub Actions pipeline for the serverless-video-processing floci lab.

- **Config:** `.github/workflows/ci.yml`
- **Local mirror:** `scripts/ci-local.sh` (same commands, same order)
- **Design record:** `_bmad-output/test-artifacts/ci-pipeline-progress.md`

## Stages

| Job | What it does | Gate |
| --- | --- | --- |
| `gitleaks` | `gitleaks detect` over full git history (`fetch-depth: 0`) with `.gitleaks.toml` | zero findings |
| `lint` | `ruff check lambdas/ --select E,F` + `terraform fmt -check -recursive` | both must pass |
| `unit-test` | `pytest lambdas/` — shared access layer suite (27 tests) | 100% pass |
| `terraform-validate` | `terraform init -backend=false` + `terraform validate` | valid config |
| `integration` | floci via `docker compose up -d --wait` → `terraform apply` → `pytest tests/integration/` (T1–T10: gateway upload with binary round-trip, auto-processing journey, state machine, transcode, history leg) → `terraform destroy` (always) | 10 integration tests pass |

Job graph: `gitleaks → lint → {unit-test, terraform-validate} → integration`.

## Secrets scanning (gitleaks)

- **Config:** `.gitleaks.toml` — extends the default ruleset (~150 rules:
  AWS keys, generic API keys, private keys, JWTs, …) with a project
  allowlist. Currently allowlisted: `_bmad/_config/files-manifest.csv`
  (SHA-256 content hashes that trip `generic-api-key`).
- **Local:** stage 1 of `scripts/ci-local.sh` — `gitleaks detect --no-banner
  --config .gitleaks.toml`. Install once with `scoop install gitleaks`.
- **CI:** `gitleaks/gitleaks-action@v3` with `fetch-depth: 0` so every commit
  is scanned, not just HEAD. Hard gate — a finding fails the pipeline.
- The floci dummy creds (`test`/`test`) need no allowlisting: they match no
  key pattern and carry no entropy.
- If a real secret ever lands in history, rotate it first, then purge with
  `git filter-repo` / BFG and force-push — gitleaks only *detects*, it does
  not clean.

## Triggers

- Push to `main`
- Pull requests targeting `main`
- Manual (`workflow_dispatch`)

**Path filtering:** pushes and PRs are filtered with `paths-ignore` —
changes that touch *only* documentation (`README.md`, `docs/**`,
`_bmad/**`, `_bmad-output/**`, any `**.md`) skip CI entirely. Any commit
touching code, Terraform, scripts, or the workflow itself still triggers
the full pipeline. `workflow_dispatch` is unfiltered, so a manual run
always works regardless of what changed.

Concurrency: one run per ref, in-progress runs cancelled.

## Secrets

**None required.** floci uses dummy credentials (`test`/`test`), Terraform state
is local, and the integration suite talks to floci's unauthenticated APIs.
The `gitleaks` job passes the auto-provided `GITHUB_TOKEN` (mandatory for
scanning pull requests) — no repository secret to configure.
If real-AWS deployment is added later, that gets its own workflow with
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (or OIDC) as repository secrets —
never hardcoded.

## Toolchain pins

- Python 3.11 (Lambda runtime parity), installed via `astral-sh/setup-uv@v7`
- Terraform 1.6.1 via `hashicorp/setup-terraform@v4` (matches `required_version >= 1.6.0`)
- gitleaks via `gitleaks/gitleaks-action@v3` (locally: `scoop install gitleaks`)
- Actions are on Node-24 majors (`checkout@v5`, `setup-uv@v7`, `setup-terraform@v4`, `upload-artifact@v5`) — GitHub deprecated the Node 20 runner runtime (Sept 2025) and warns on older majors
- ruff and pytest are pulled ad-hoc by `uv run --with` — no lockfile needed yet

## Integration stage details

The pytest suite in `tests/integration/` drives the DEPLOYED stack through
real API Gateway calls (`POST /videos/upload` via floci's
`_aws/execute-api` data plane) and real AWS-API side-effect reads (S3,
DynamoDB, SQS, EventBridge, Step Functions). Coverage: binary upload
round-trip (byte-identical), malformed-request 400s, the full
auto-processing journey (handler → rule → queue → shim → SFN → transcode →
publisher), redelivery dedupe, ad-hoc state-machine and transcode invokes,
and the history leg (recorded / deduped / poison-dropped). Design record:
`_bmad-output/test-artifacts/integration-test-plan.md`. The gateway base
URL comes from `terraform output -raw gateway_base_url` (env override
`GATEWAY_BASE_URL` honored); the capture queue (`smoke-capture-queue`,
declared in `terraform/integration.tf`) is the `video.processed`
observation point. No `aws` CLI in the pipeline, matching the lab's
Terraform-only rule.

`terraform destroy` runs with `if: always()` so a failed suite never leaves
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
| Integration test fails or times out | Read the failing test's assertion — it names the resource/state that diverged. Polling timeouts are generous (180 s) for cold floci Lambda containers; rerun once on cold-cache flakes. Pull `integration-failure` artifact for floci logs. |
| `terraform apply` fails with `InvalidClientTokenId` | A service endpoint is missing from the provider `endpoints{}` block (spine AD-8 fact 3). Add it to `terraform/providers.tf`. |
| Lambda invoke hangs / times out | floci needs the Docker socket to spawn Lambda containers; on a self-hosted runner verify `/var/run/docker.sock` access. GitHub-hosted `ubuntu-latest` provides it. |
| Ruff fails on a new file | Run `uv run --with ruff ruff check lambdas/ --select E,F` locally and fix. |

## Badge (optional)

```markdown
[![CI](https://github.com/namworkmc/serverless-video-processing/actions/workflows/ci.yml/badge.svg)](https://github.com/namworkmc/serverless-video-processing/actions/workflows/ci.yml)
```
