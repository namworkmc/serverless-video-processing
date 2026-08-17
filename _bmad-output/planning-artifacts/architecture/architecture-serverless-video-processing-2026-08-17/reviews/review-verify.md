# Review: Verify (reality-check lens)

**Verdict: PASS with 3 findings applied.** Every committed technology was verified against the live environment (floci 1.6.0 health endpoint, Terraform 1.6.1, aws provider 5.100.0 lock file) or against a spike that exercised it end-to-end on this machine. No version was asserted from training data without a live check.

## Findings

1. **[HIGH — fixed] `arn:aws:states:::events:putEvents` unsupported on floci 1.6.0.** AD-4's final publish task assumed this direct integration. Spike test 11 failed with `Unsupported resource: arn:aws:states:::events:putEvents`. Workaround (publisher Lambda invoked by the state machine) verified SUCCEEDED end-to-end. AD-4 and AD-8 amended.
2. **[MEDIUM — fixed] `UpdateStateMachine` unsupported on floci 1.6.0.** Terraform cannot update a state machine definition in place (`api error UnsupportedOperation`). Any ASL change requires destroy+recreate (`terraform apply -replace`). Added to AD-8 as binding platform fact (4).
3. **[LOW — fixed] `history-query` and `search-query` Lambdas appeared in the structural seed and route table but were missing from the frontmatter `binds` list.** Added, plus `event-publisher-lambda`.
4. **[LOW — accepted] boto3 presence in floci's python3.11 runtime image is assumed, not verified.** The spike stayed stdlib-only (urllib) precisely to avoid depending on it. Already carried in Deferred with a proven fallback. No change needed.
5. **[LOW — accepted] floci pinned as `floci/floci:latest` in compose.yaml while the spine pins 1.6.0.** Acceptable for the lab (latest resolved to 1.6.0 at authoring); a strict pin is a future hardening, not a divergence risk.

## Evidence base

- floci version + service list: live `/_localstack/health` (1.6.0, all required services "running")
- Terraform/provider: `terraform version` (1.6.1), `.terraform.lock.hcl` (aws 5.100.0)
- All 11 spike tests executed against the live emulator; results recorded in the run memlog
