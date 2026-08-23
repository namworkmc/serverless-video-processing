# Serverless Video Processing — Agent Instructions

Serverless video platform (upload → transcode → status/search) running locally on
floci (`localhost:4566`), infrastructure managed exclusively with Terraform.
Project context already exists — read it before working, do not re-derive it:
`README.md` (stack, upload journey, layout), `lambdas/README.md` (Lambda
conventions, packaging, tests), `docs/ci.md` (pipeline design + troubleshooting),
`_bmad-output/` (PRD, architecture spine, epics, story specs).

## Workflow rules (mandatory)

1. **Worktree before work — mandatory for BMAD workflows.** Worktrees live
   under `.worktrees/` (gitignored):

   ```bash
   git worktree add .worktrees/<short-topic> -b <branch-name>
   ```

   - **Starting any BMAD workflow** (any `bmad-*` skill is loaded/run —
     `bmad-build`, `bmad-prd`, `bmad-architecture`,
     `bmad-create-epics-and-stories`, `bmad-review`, …): BEFORE doing
     anything else in the workflow — before clarifying questions, reading
     specs, planning, or touching any file — create a worktree with a fresh
     branch and run the entire workflow inside it. This is mandatory: do not
     ask permission, just announce the worktree path and branch created.
     Skip creation only if this session is already inside a worktree checkout
     (check `git worktree list` first — never nest or duplicate worktrees).
   - Branch naming: story implementation follows the existing
     `feat/story-<id>-<slug>` convention; other workflows use
     `bmad/<workflow>-<short-topic>`. Base the branch on the latest `main`
     unless the user names a different base.
   - **Any other file change:** first suggest a worktree; if the user
     declines, edit in the main checkout.

   List/clean up with `git worktree list` / `git worktree remove`.
2. **Terraform reviews require the skill.** Whenever reviewing, writing, or
   debugging Terraform implementation (`terraform/*.tf`), load the
   `terraform-skill` skill first (`skill_view(name='terraform-skill')`) and
   follow it. No Terraform review without it.
3. **Local validation before commit/push.** Never commit or push without a green
   local validation run. Minimum gate for any change:

   ```bash
   bash scripts/ci-local.sh        # full CI mirror: secrets-scan → lint → unit-test → tf-validate → integration
   ```

   Stage-by-stage equivalents (same commands CI runs, in the same order):

   ```bash
   gitleaks detect --no-banner --config .gitleaks.toml          # secrets scan (full history)
   uv run --with ruff ruff check lambdas/ --select E,F          # lint: Python
   (cd terraform && terraform fmt -check -recursive)            # lint: Terraform
   uv run --with 'pytest>=8.0' pytest lambdas/ -q               # unit tests
   (cd terraform && terraform init -backend=false -input=false && terraform validate)
   ```

   The integration stage needs Docker; it reuses a healthy running floci.
   For Terraform-only changes, `terraform fmt -check` + `terraform validate`
   is the floor; for Lambda changes, add ruff + pytest. The gitleaks stage is
   cheap (~1s) — run it on every change. Fix failures locally, re-run, then
   commit.
4. **Conflict resolution: rebase on branches, current strategy into main.**
   When resolving conflicts while syncing a feature/story branch with `main`,
   prefer rebase over merge (`git rebase main` / `git pull --rebase`) to keep
   history linear. When merging into `main`, keep the current strategy
   (merge via PR) — never rebase `main` itself.

## Hard rules

- **No `aws` CLI for infrastructure.** Provision/teardown is Terraform-only
  (`terraform apply` / `terraform destroy` in `terraform/`). Ad-hoc Lambda
  invokes go through floci's REST API or local boto3 (see `lambdas/README.md`).
- floci credentials are dummy (`test`/`test`); Terraform state is local
  (`terraform/terraform.tfstate`), no remote backend, no secrets anywhere.

## Conventions (observed)

- Commit messages: `type: summary`, often with story refs —
  `feat: Story 3.1 — history consumer recording terminal events`,
  `ci: …`, `test: …`, `bmad: …`.
- One directory per Lambda under `lambdas/`; all handlers import the shared
  access layer as `from shared import status, events, errors, clients`
  (`_shared/` is packed into each zip as `shared/` by `archive_file`).
- Config-not-code: bucket/table/bus names and `AWS_ENDPOINT_URL` are
  Terraform-set env vars, never hardcoded in handlers.
- Tests live in `lambdas/<function>/tests/`; each `conftest.py` registers the
  local `_shared/` dir as the `shared` package to mirror the zip layout.
- Chat output during `bmad-*` workflows and any long reasoning session:
  caveman mode (compressed, terse replies) — long reasoning drifts into
  unnecessary words otherwise. Written artifacts/docs stay normal prose.

## Pitfalls

- `terraform/*.zip` and `*.tfstate*` are generated — never hand-edit
  (gitignored; zips rebuild via `archive_file` on apply).
- floci supports `UpdateStateMachine` — ASL changes apply in place via
  `terraform apply`.
- Every AWS service used must be listed in the provider `endpoints{}` block in
  `terraform/providers.tf`, or applies fail with `InvalidClientTokenId` 403.
- API Gateway v2 data plane resolves only at
  `http://localhost:4566/_aws/execute-api/{apiId}/{stage}/...` — the
  Terraform invoke URL output does not resolve locally.
- `docker-compose.yaml` must mount the Docker socket or no Lambda will run.
- EventBridge cannot target Step Functions directly on floci, and SFN's
  `events:putEvents` direct integration is unsupported — use shim/publisher
  Lambdas (architecture spine records these gaps).
