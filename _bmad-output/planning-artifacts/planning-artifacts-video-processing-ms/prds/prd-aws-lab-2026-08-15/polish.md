# Polish Notes — PRD + Addendum (Video processing platform)

Reviewer: DOCUMENT POLISH subagent (structure lens, then prose lens).
Scope: `prd.md` (397 lines) + `addendum.md` (30 lines). Review only — no edits made.

---

## PASS 1 — STRUCTURE

### 1.1 What's working (keep)

- **Uniform cluster shape.** All 8 feature clusters (§4.1–4.8) follow the same skeleton — Description / Functional Requirements / Consequences (testable) / Out of Scope. FR naming and "Consequences (testable)" boilerplate are consistent. No reshaping needed.
- **UJ-1 is proportionate.** Single journey for a solo learning lab, with persona → entry state → path → climax → resolution is the right size. Not over-built.
- **Ordering of §4 is sound.** Shared contract first (everything compiles against it), then flow order (upload → processing → notification → search). Moving Metadata before Processing is *defensible* (processing depends on the state machine) but the pattern-first grouping is fine as-is — add no reorder.
- **§5–§9 are appropriately compact.** Non-goals, MVP scope, success metrics, open questions, assumptions all pull their weight. §6.2's "Everything in §5 Non-Goals" cross-reference is good practice.
- **Addendum sizing is right.** Options-considered rationale, tech facts, and drift note each earn their place; no filler.

### 1.2 Redundancy to consolidate

- **P1 (High) — Poison-message rule duplicated verbatim across two clusters.** FR-16 consequence and FR-18 consequence are near-identical ("The consumed event is validated against metadata (`GetVideo`): a terminal event for a `videoId` metadata reports as unknown is dropped as poison (not stored / not indexed)"). This is one cross-cutting rule appearing twice. Extract it into a single shared statement (e.g., a "derived-service consumption rule" note in §4 intro, or in the addendum) and have FR-16/FR-18 reference it.
- **P2 (Medium) — Metadata's status-rejection rule asserted twice.** FR-10 consequence ("Regressions or transitions out of terminal status are rejected with `FAILED_PRECONDITION` by metadata") restates what FR-14 (Metadata cluster's own requirement) specifies. Since FR-14 owns the rule, trim FR-10's consequence to the transition order only, or cross-reference FR-14.
- **P3 (Medium) — Glossary "API Gateway" entry duplicates §4.7.** The glossary entry (~2 lines) and §4.7 description repeat the same facts: single client ingress, v2 HTTP API emulated by ministack, no client calls a service directly, no auth. Trim the glossary entry to a one-liner + pointer to §4.7.
- **P4 (Medium) — Single-ingress claim stated four times.** §0, §2.3 (UJ-1), §4.7 description, §6.1, and SM-1 all restate "every client call goes through the gateway." Acceptable as traceability, but the most verbose restatement (UJ-1 Resolution) can compress to "all client calls via the API Gateway."
- **P5 (Medium) — gRPC debugging carve-out appears three times.** §2.2 ("plus direct gRPC as a debugging/inspection surface only"), FR-21 Carve-out (full definition), and A-4 (restates it). Keep the definition in FR-21 only; §2.2 and A-4 should just cite "per the FR-21 carve-out."
- **P6 (Low) — §4.8 "Realizes UJ-1" is a forced mapping.** §4.8 claims "Realizes UJ-1 (the lab's backing infrastructure is up because Terraform applied it)," but the UJ-1 path never mentions Terraform or infrastructure bring-up. Either add one beat to UJ-1 (e.g., a prerequisite line: "environment up via `terraform apply`") or drop the claim and the parenthetical.
- **P7 (Low) — §8 Open Question overlaps Non-Goals/Out-of-Scope.** "Whether a FAILED-demo is wanted later" is a future consideration, not a blocking open question; §4.3 Out-of-Scope and §5 already note no FAILED demo. Reframe as a noted future iteration or fold into the Non-Goal.

### 1.3 Structure issues / gaps

- **P8 (Medium) — §0 is one dense paragraph doing three jobs.** It mixes (a) document purpose, (b) relationship-to-spine / scope separation, and (c) a change-control warning that two features are not yet bound in the spine (the gated prerequisite for story derivation). The gated-prerequisite warning is the most operationally important line in the doc and is buried. Promote it to a visible callout (bold/`> [!IMPORTANT]`-style block or a short "Gated features" note) so downstream story work cannot miss it.
- **P9 (Low) — UJ-1 and §4.8's Terraform.** See P6. If UJ-1 is meant to cover the whole lab outcome, its Path/Entry-state should acknowledge provisioning; otherwise the per-cluster "Realizes UJ-1" tags on §4.7/§4.8 are weaker than those on the service clusters. Consider a single traceability sentence instead of repeating "Realizes UJ-1" on every cluster.

---

## PASS 2 — PROSE (on top of structure)

### 2.1 Terminology and capitalization

- **P10 (High) — Unstated dot-vs-dash convention for events vs queues.** Events are consistently `video.uploaded` / `video.processed` (dot), while queues are consistently `video-uploaded` / `video-processed` (dash) in FR-8, FR-25, and the addendum. This looks like a deliberate distinction (queue carries the event) but is never explained, so a reader will suspect a typo. Add an explicit note (glossary under **Event** and/or at FR-8): "event types use dots (`video.uploaded`); the SQS queue that carries them is dash-named (`video-uploaded`)."
- **P11 (Medium) — "history" naming is three-spelled.** `status-history` (§4.5 description, §4.7 description), `status history` (FR-21, addendum), and `history` as the route name (FR-17 "gateway history path", FR-22 "history → notification-service"). Pick one canonical form (recommend `status history` in prose, and confirm the gateway path name) and apply everywhere.
- **P12 (Medium) — §4.7 route mapping drops the `-service` suffix.** "routes by path to the upload (upload), status-history (notification), and search (search) HTTP facades" — the parenthetical is the target service, so two of three are wrong: should be `upload (upload-service)`, `status-history (notification-service)`, `search (search-service)`. Also use the chosen canonical term for "status-history" (see P11).
- **P13 (Low) — Pattern name is inconsistent.** §4 intro says "single-ingress pattern"; §4.7 heading says "single ingress / API Gateway as edge"; §4.7 description says "API-Gateway-as-single-ingress pattern." Unify on one name (recommend "single-ingress pattern").
- **P14 (Low) — `aws cli` vs `aws CLI`.** Used lowercase-consistent everywhere (§4.8, FR-26, §5, addendum) — fine as a deliberate style, but consider rendering the product name as `aws CLI` in prose while keeping the command `aws cli` in code spans. At minimum, state the convention once.
- **P15 (Low) — "ministack" is consistent** (lowercase everywhere, including addendum). Good — do not change.

### 2.2 Sentence-level clarity

- **P16 (High) — Tangled poison-message sentence (twice).** FR-16 and FR-18: "a terminal event for a `videoId` metadata reports as unknown is dropped as poison (not stored)." Grammar is inverted/ambiguous. Rewrite both to: "an event for a `videoId` that metadata reports as unknown is dropped as poison (not stored / not indexed)." (Fix once if P1 consolidation lands.)
- **P17 (Low) — §2.1 JTBD: "Exercise real AWS service integrations (S3, SQS) against ministack emulation."** "Real … against emulation" reads as an oxymoron. Reword: "Exercise the real AWS service APIs (S3, SQS) against ministack's local emulation."
- **P18 (Low) — FR-4: "renumber/rename/remove is a breaking change."** Subject-verb reads awkwardly. Reword: "renumbering, renaming, or removing fields is a breaking change requiring a new version."
- **P19 (Low) — FR-10: "Regressions or transitions out of terminal status are rejected."** "Regressions" is ambiguous (sounds like a software regression). Reword: "Backward transitions (status regressions) or transitions out of a terminal status are rejected."
- **P20 (Low) — UJ-1 Path: "notification-service records history and search-service indexes it."** The "it" is ambiguous (the event vs the video). Reword: "…records history and search-service indexes the video."
- **P21 (Low) — §5 Non-Goals, first bullet: "no auth" twice in one bullet.** "No auth, no multi-tenancy, no real end users; the API Gateway is open (no auth, rate limits, usage plans, or custom domains in this lab)." Drop the redundant second "no auth."
- **P22 (Low) — Addendum §Options/Provisioning: "against the user's explicit rule."** Colloquial for a downstream artifact; the PRD calls the builder "Kygor." Reword: "against the builder's explicit rule."
- **P23 (Low) — Addendum intro names "deployment envelope" as a downstream consumer but the term is used nowhere else.** Define it or drop it.
- **P24 (Low) — §0 "FR-21..24" vs "FR-25/26" mixed notation.** Use one style ("FR-21–FR-24" / "FR-25–FR-26").

### 2.3 Metrics consistency

- **P25 (High) — SM-1 claims "Validates FR-5..FR-26," skipping FR-1..FR-4.** The Shared Contract cluster (§4.1) is in scope (§6.1 lists it) but is excluded from SM-1's validation claim. Either widen SM-1 to FR-1..FR-26 (shared contract is validated by compilation, which the end-to-end run exercises), or state explicitly that contract validation is covered elsewhere.

---

## PRIORITIZED ACTION LIST (for the fix pass)

| # | Sev | Location | Fix |
|---|-----|----------|-----|
| P1 | High | FR-16 & FR-18 consequences | Deduplicate the poison-message rule into one shared statement; cross-reference from both FRs. |
| P10 | High | Glossary (Event) + FR-8 | Add an explicit note that event types use dots (`video.uploaded`) while the carrying queue is dash-named (`video-uploaded`). |
| P16 | High | FR-16, FR-18 | Rewrite the tangled sentence: "an event for a `videoId` that metadata reports as unknown is dropped as poison." |
| P25 | High | §7 SM-1 | Fix "Validates FR-5..FR-26" to cover the in-scope Shared Contract cluster (FR-1..FR-4) or justify the exclusion. |
| P12 | Medium | §4.7 description | Fix parentheticals to service names: `upload-service`, `notification-service`, `search-service`. |
| P11 | Medium | §4.5, §4.7, FR-17, FR-21, FR-22, addendum | Standardize on one term for the history surface (recommend `status history` in prose; confirm route name). |
| P8 | Medium | §0 | Promote the "two features not yet bound in the spine" warning into a visible callout so story gating cannot be missed. |
| P2 | Medium | FR-10 vs FR-14 | Trim FR-10's duplicate status-rejection consequence; let FR-14 own the rule. |
| P3 | Medium | Glossary "API Gateway" | Slim to a one-liner + pointer to §4.7. |
| P5 | Medium | §2.2, FR-21, A-4 | Keep the gRPC carve-out definition in FR-21 only; cite "per the FR-21 carve-out" elsewhere. |
| P6 | Low | §4.8 | Drop or repair the forced "Realizes UJ-1" claim (Terraform is not in UJ-1's path). |
| P7 | Low | §8 | Reframe the FAILED-demo item as a future iteration note rather than an open question. |
| P13–P24 | Low | various | Minor terminology/capitalization/grammar cleanups as itemized in §2.1–§2.2. |

---

## BOTTOM LINE

Structure is fundamentally solid: uniform clusters, proportionate UJ-1, well-sized addendum. The document needs no restructuring — it needs **deduplication** (poison rule, status-rejection rule, glossary-vs-§4.7, carve-out 3x), **one prose fix repeated twice** (the poison sentence), **one terminology convention made explicit** (dot-events vs dash-queues), **one metrics-coverage gap** (SM-1 excludes FR-1..4), and a **visibility upgrade for the gated-features warning** in §0. Highest-value fixes are P1, P10, P16, P25, P8.
