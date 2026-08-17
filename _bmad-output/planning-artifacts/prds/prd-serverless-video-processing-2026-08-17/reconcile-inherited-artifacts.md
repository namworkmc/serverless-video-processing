# Input Reconciliation — Inherited aws-lab Artifacts vs. New PRD

**Inputs checked:**
- `_bmad-output/planning-artifacts/planning-artifacts-video-processing-ms/prds/prd-aws-lab-2026-08-15/prd.md` (old PRD, 26 FRs / 10 NFRs / 6 assumptions)
- `_bmad-output/planning-artifacts/planning-artifacts-video-processing-ms/epics.md` (old epics, additional requirements section)

**Against:** `prd.md` + `addendum.md` (new serverless PRD, 23 FRs / 8 NFRs)

**Scope of check:** stack-independent domain requirements only. Technology-specific requirements (Spring Boot, gRPC, PostgreSQL, ports, Maven, normative port table, Bruno-as-gRPC-debug-surface) are intentionally gone. Deliberate drops per user decision: event-schema versioning feature, FAILED producing path, teaching-to-others goal.

## Coverage verdict

The new PRD carries forward the old domain core faithfully:

| Old domain rule | New PRD location | Status |
|---|---|---|
| Pipeline shape upload→process→notify/search | F1–F5 | ✓ |
| videoId minted once at ingress | FR-2 | ✓ |
| State machine + terminal finality + same-status idempotency | FR-11 | ✓ |
| Idempotent create by ingress id | FR-12 | ✓ |
| Deterministic eventId, exactly-once emission | FR-8, NFR-1, NFR-2 | ✓ |
| Redelivered duplicate = no-op | FR-9 | ✓ |
| Status-first ordering (transition acked before event published) | FR-7 | ✓ |
| Poison-event rule (unknown videoId dropped; transient errors retried never dropped) | FR-15, FR-17 | ✓ |
| FAILED never indexed | FR-17 | ✓ |
| Rebuildable index, admin-only trigger | FR-19 | ✓ |
| Single client ingress, no auth, responses pass through | FR-20, FR-21 | ✓ |
| Error body `{"error": ...}` + 400/404/409/500 | NFR-3 | ✓ |
| Config-not-code; names declared in Terraform | NFR-4 | ✓ |
| Terraform-only setup/teardown, no aws cli | FR-22, FR-23, NFR-8 | ✓ |
| Performance non-goal | NFR-7 | ✓ |
| Reproducible destroy+apply verification | NFR-6 | ✓ |

**No contradictions found.** Every deviation from the old domain rules is either technology-specific (gone with the stack) or an explicitly logged user decision.

## Gaps (silently dropped, not out-of-scoped)

1. **Emulator-sufficiency assumption missing (old A-2 equivalent).** The old PRD explicitly assumed ministack's API Gateway v2 emulation was sufficient and pinned the invoke-URL mechanics (assumption A-2). The new PRD depends on floci emulating EventBridge→Step Functions triggering, API Gateway v2→Lambda integrations, and Lambda env wiring — none verified in Phase 0 (only S3 was round-tripped) — yet carries no assumption or open question about it. This is the single most consequential silent drop.

2. **No explicit test-surface deliverable (old Story 5.2 equivalent).** The old plan had a dedicated story for a Bruno collection exercising all three journeys through the gateway — the reproducible proof of SM-1. The new PRD mentions "Bruno or curl" in passing (§2.2) but has no FR or deliverable for a reproducible test collection. SM-1's "reproducible" quality currently has no named artifact backing it.

3. **History response shape unspecified (old FR-17 showed `PROCESSED` in response).** Old consequence: history query "returns the history showing PROCESSED." New FR-16 says "query a video's recorded status history" without saying what an entry contains (status, eventId, timestamp). Minor — glossary implies it.

4. **SM-1 doesn't verify the thesis.** Old SM-1 explicitly required "all 5 services exercised." The new thesis (§1) is "each target AWS service exercised as a first-class learning surface," but new SM-1 only checks the pipeline outcome (PROCESSED record + history + search hit) — it would pass even if some service were silently bypassed. The metric should name the service-coverage check.

5. **ListVideos-equivalent not named for rebuild.** Old FR-20 rebuilt from metadata `ListVideos`. New FR-19 says "rebuildable from the metadata table" — fine in principle, but the scan/enumeration mechanism is left dangling together with the rebuild trigger (see review-rubric.md, Done-ness clarity).

## Notes

- Old FR-13's "foreign collision → ALREADY_EXISTS" semantics were dropped without comment; with a single videoId minter in serverless form, collision is not a reachable state, so this is a defensible silent drop — worth a one-line note in the PRD if a reviewer asks.
- Old glossary naming convention (dot event names / dash queue names) survives implicitly (`video.uploaded` events); queue/bus naming is now an architecture concern.
