---
title: 'Story 1.1: Lab Environment Bootstrap (floci + Terraform skeleton)'
type: 'feature'
created: '2026-08-19'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'd1c214f273dca94d7666abebf3ec364d1d0bd0ff'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The repo has a Phase 0 spike seed (compose file, provider config, smoke resource) but no clean, reproducible lab substrate. The smoke resource must go, the floci image must be pinned, and the README must reflect the actual bootstrap procedure — so every later story starts from a deterministic `docker compose up` → `terraform apply`.

**Approach:** Harden the existing seed into the final substrate: pin floci 1.6.0, remove the Phase 0 smoke resource, verify the provider skeleton matches the architecture's endpoint requirements, and update the README to document the Terraform-only bring-up/teardown.

## Boundaries & Constraints

**Always:**
- Terraform >= 1.6.0, hashicorp/aws ~> 5.0, targeting `http://localhost:4566`
- `s3_use_path_style = true`, all credential/validation skips enabled
- `endpoints{}` block must list every service the platform will use (s3, sqs, dynamodb, lambda, stepfunctions, events, apigatewayv2, iam, cloudwatch, sts)
- `/var/run/docker.sock` mounted into the floci container
- No `aws` CLI in documented setup/teardown (FR-23, FR-24)
- No resources declared — substrate only

**Ask First:**
- Any change to the provider endpoint list beyond what's already verified
- Any change to the compose healthcheck strategy

**Never:**
- Declare any AWS resource (buckets, tables, functions, etc.)
- Use `aws` CLI in README quick-start or teardown
- Remove or alter `.gitignore` Terraform exclusions
- Modify `_bmad-output/` planning artifacts

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh bring-up | Clean repo, Docker Desktop running | `docker compose up -d` → floci healthy at :4566 within ~30s | Healthcheck retries 12×10s; if still unhealthy, inspect Docker Desktop / port conflict |
| Terraform apply | floci healthy, `terraform/` has providers.tf only | `terraform init && terraform apply` succeeds, 0 resources added | Provider connection error → verify floci is up and port 4566 reachable |
| Terraform destroy | Empty state after apply | `terraform destroy` succeeds, 0 resources destroyed | N/A |
| Docker sock missing | docker.sock not mounted | floci starts but Lambda creation fails later | Compose file must always mount the sock — verified by inspection |

</frozen-after-approval>

## Code Map

- `docker-compose.yaml` -- floci service definition; currently uses `floci/floci:latest`, needs pin to `1.6.0`
- `terraform/providers.tf` -- provider skeleton; already has correct endpoints block (superset of required list, verified in Phase 0). Keep as-is.
- `terraform/smoke.tf` -- Phase 0 smoke resource (`aws_s3_bucket.smoke`); must be deleted (AC: no resources declared)
- `terraform/terraform.tfstate` -- empty state (resources: []); gitignored, no action needed
- `terraform/.terraform.lock.hcl` -- provider lock; keep
- `.gitignore` -- already excludes `.terraform/`, `*.tfstate*`; no changes needed
- `README.md` -- quick-start and status section; update status to reflect Story 1.1 completion, verify no aws CLI references
- `lambdas/README.md` -- placeholder; no changes this story

## Tasks & Acceptance

**Execution:**
- [x] `docker-compose.yaml` -- Pin image from `floci/floci:latest` to `floci/floci:1.6.0` -- AC requires specific version for reproducibility
- [x] `terraform/smoke.tf` -- Delete file -- AC: "no resource is declared yet — this story creates the substrate only"
- [x] `terraform/providers.tf` -- Verify endpoints block contains all required services; no edit expected (already correct) -- read-only confirmation
- [x] `README.md` -- Update Status section to reflect lab bootstrap complete; verify quick-start has no `aws` CLI -- FR-23/FR-24 compliance

**Acceptance Criteria:**
- Given Docker Desktop running, when `docker compose up -d`, then floci 1.6.0 is healthy at `localhost:4566` (healthcheck passes) and `/var/run/docker.sock` is mounted
- Given floci healthy and `terraform/` containing only `providers.tf` + lock file, when `terraform init && terraform apply -auto-approve`, then apply succeeds with "0 to add, 0 to change, 0 to destroy"
- Given empty state, when `terraform destroy -auto-approve`, then destroy succeeds cleanly
- Given the README quick-start, when inspected, then no `aws` CLI invocation appears in setup or teardown steps

## Verification

**Commands:**
- `docker compose up -d --wait` -- expected: compose blocks until the healthcheck passes (no hardcoded sleep; the healthcheck itself allows up to 12×10s)
- `curl -sf http://localhost:4566/_localstack/health` -- expected: JSON with services listed, HTTP 200
- `docker inspect serverless-video-processing-floci-1 --format '{{.Config.Image}}'` -- expected: `floci/floci:1.6.0` (pins the running version, not just liveness)
- `docker inspect serverless-video-processing-floci-1 --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'` -- expected: `/var/run/docker.sock -> /var/run/docker.sock` present
- `cd terraform && terraform init -input=false && terraform apply -auto-approve -input=false` -- expected: "Apply complete! Resources: 0 added, 0 changed, 0 destroyed."
- `terraform destroy -auto-approve -input=false` -- expected: "Destroy complete! Resources: 0 destroyed."
- `grep -rnE 'aws (s3|lambda|dynamodb|sqs|events|stepfunctions|apigatewayv2|iam|cloudwatch|sts) ' README.md` (run from repo root, case-sensitive) -- expected: no matches (prose like "AWS provider" is fine; only CLI invocations count)

**Manual checks (if no CLI):**
- Inspect `docker-compose.yaml` volumes section: `/var/run/docker.sock:/var/run/docker.sock` present
- Inspect `terraform/` directory: only `providers.tf` and `.terraform.lock.hcl` remain (plus gitignored `.terraform/` and state files)

## Suggested Review Order

**Lab substrate**

- The one-line change that makes the lab reproducible: pinned emulator version.
  [`docker-compose.yaml:6`](../../docker-compose.yaml#L6)

- Provider skeleton verified unchanged — endpoints block is the contract every later story depends on.
  [`providers.tf:23`](../../terraform/providers.tf#L23)

**Cleanup**

- Phase 0 smoke resource deleted; `terraform/` now declares zero resources (substrate only).
  [`smoke.tf` (deleted)](../../terraform/)

**Documentation & tracking**

- README status rewritten to reflect bootstrap completion; quick-start untouched and aws-CLI-free.
  [`README.md:45`](../../README.md#L45)

- Sprint tracking: story and epic-1 moved to in-progress (synced to `review` at completion).
  [`sprint-status.yaml:40`](sprint-status.yaml#L40)
