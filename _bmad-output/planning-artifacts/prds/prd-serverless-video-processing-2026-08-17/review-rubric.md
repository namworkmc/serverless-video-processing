# PRD Quality Review — Serverless Video Processing Platform

## Overall verdict

A coherent, well-scoped learning-lab PRD whose thesis — "learn AWS serverless by routing work through each service as a first-class surface" — is stated clearly and served by every feature cluster. Scope honesty and shape fit are strong for the hobby/solo stakes. The main risk is downstream: most FRs are single-sentence requirements without the explicit testable-consequence bullets the old PRD carried, which story creation will lean on; and the PRD's biggest real-world risk — whether floci actually emulates the EventBridge→Step Functions and API Gateway→Lambda integrations it depends on — is nowhere surfaced.

## Decision-readiness — adequate

Decisions are stated as decisions: serverless-native re-architecture, Terraform-only, zip-packaged Lambda with copy fallback, FAILED path deferred. The addendum carries rejected alternatives (AD-B names the container-image Lambda as the rejected option with rationale), which is honest trade-off surfacing. However, the PRD itself contains no Open Questions and no `[ASSUMPTION]` tags, despite carrying at least one genuinely open risk (floci integration coverage — see below). For a hobby lab this is forgivable; for decision-readiness it leaves the risk invisible.

### Findings

- **high** Unverified emulator coverage is not surfaced (§7, §5 NFR-6) — the PRD bets the entire pipeline on floci emulating EventBridge→Step Functions triggering, API Gateway v2→Lambda integrations, and Lambda environment wiring. Phase 0 verified only S3 via Terraform. The old PRD pinned an equivalent assumption (A-2 on ministack's gateway emulation); this one has none. *Fix:* add an Open Question or `[ASSUMPTION]` naming the unverified floci integrations, with a spike/PoC as the revisit condition.
- **low** No Open Questions section at all (§ structure) — the old PRD had one; the deferred FAILED demo and the floci-coverage question are natural candidates. *Fix:* add a short Open Questions section with those two entries.

## Substance over theater — strong

No persona theater (single named protagonist in UJ-1, no standalone persona section), no innovation theater, no boilerplate NFRs — every NFR is product-specific (deterministic eventId idempotency, config-not-code via Lambda env vars, CloudWatch traceability of one video's full path). The vision is specific to this lab and could not be swapped into another PRD unchanged.

### Findings

None.

## Strategic coherence — strong

The thesis is explicit in §1: each target AWS service is "exercised as a first-class learning surface" and requirements "deliberately route work through" them. Every feature cluster names the services it puts in the builder's hands, and the pipeline arc (upload → orchestrate → transcode → derive → query) follows from it. Success metrics validate the thesis: SM-1 is the end-to-end flow, SM-3 is the self-explanation test, and counter-metrics explicitly ban throughput/latency polish.

### Findings

None.

## Done-ness clarity — adequate

FR-1 is the model: a requirement with an explicit testable consequence. FR-2 through FR-23 are single-sentence requirements that are *mostly* testable by inspection ("the object lands in the uploads bucket", "a duplicate eventId appends nothing"), but they lack the explicit consequence bullets the old PRD carried under every FR. A few are genuinely underspecified for an engineer:

### Findings

- **medium** Most FRs lack explicit testable consequences (§4.1–4.7) — only FR-1 has a consequence bullet; FR-2..FR-23 rely on the requirement sentence alone. The old PRD's "Consequences (testable)" pattern made story derivation mechanical. *Fix:* add one testable consequence per FR, at minimum for FR-5 (how do you observe the state machine started?), FR-7 (how do you observe status-first ordering?), FR-9 (how do you demonstrate the no-op?), FR-19 (how is rebuild triggered and observed?).
- **medium** FR-19 rebuild trigger mechanism unspecified (§4.5) — "admin-only (no client-facing surface)" inherits the old design's gRPC/admin answer, but serverless has no gRPC surface. The mechanism (direct Lambda invoke? a non-gateway route? a CLI-driven invoke?) is a real decision the PRD leaves dangling. *Fix:* either name a mechanism or add a `[NOTE FOR PM]` deferring it explicitly to architecture with the constraint (no client-facing surface) preserved.
- **low** FR-16 doesn't say what the history shows (§4.4) — "query a video's recorded status history" without saying the response shape (terminal events with timestamps, per the glossary). *Fix:* one clause: history entries carry status, eventId, timestamp.

## Scope honesty — adequate

§7 Out of Scope is explicit and covers the deliberate drops (real ffmpeg, FAILED path, schema versioning, CI/CD, real AWS). Per-feature out-of-scope notes survive on F1 and F2. The gap is that inferences made during discovery (hobby stakes, floci coverage) were logged to the memlog but never tagged in the PRD, so a reader of prd.md alone cannot see what was assumed versus confirmed.

### Findings

- **medium** No Assumptions Index (§ structure) — the old PRD carried six indexed assumptions; this one has none despite carrying real ones (floci integration coverage, zip-Lambda sufficiency for the learning goal, single-region us-east-1). *Fix:* add a short Assumptions Index; at minimum the floci-coverage assumption.

## Downstream usability — strong

Glossary present and used consistently (videoId, VideoStatus, eventId, derived store all appear identically across FRs/UJ/SMs). FR-1..FR-23 contiguous and unique; NFR-1..8, SM-1..3 likewise. Cross-references resolve (FR-17 → FR-15 poison rule). UJ-1 has a named protagonist. Each section reads standalone.

### Findings

- **low** Glossary "Event" entry says "routed via EventBridge" (§3) — fine for v1, but the architecture may use SQS event-source mappings for some consumers; the glossary pins a transport before architecture decides. *Fix:* soften to "routed via the event backbone (EventBridge/SQS — architecture decides)" or leave and let architecture amend.

## Shape fit — strong

Hobby/solo, single operator: capability-spec shape with exactly one UJ, no persona section, operational success metrics, lean ~2-page body. Neither over-formalized nor under-formalized for the stakes.

### Findings

None.

## Mechanical notes

- FR/NFR/SM IDs contiguous, no duplicates, cross-refs resolve. ✓
- Glossary terms used consistently; one pre-architecture transport pin (see Downstream usability finding).
- No `[ASSUMPTION]` tags inline, hence no Assumptions Index roundtrip to check — but see Scope honesty finding.
- UJ-1 protagonist named (Kygor). ✓
- Required sections for hobby stakes all present except Open Questions and Assumptions Index.
