# PRD Quality Validation — Rubric Review

**PRD:** `prd-aws-lab-2026-08-15/prd.md` + `addendum.md`
**Date:** 2026-08-15
**Stakes calibration:** hobby/solo learning lab (single builder, 5 services, ministack, Bruno). Rigor light, substance bar applied.
**Files reviewed:** `prd.md` (393 lines), `addendum.md` (30 lines). Spine cross-checked against `ARCHITECTURE-SPINE.md` to verify binding claims and the addendum's Known-Drift statement.

---

## Overall Verdict

This is a well-scoped, honest, capability-first PRD that is appropriate for hobby stakes: every FR (FR-1..FR-26) carries a testable consequence, scope is explicitly carved per cluster plus a global Non-Goals list, and the addendum records options-considered and a frank Known-Drift disclosure. One real defect blocks clean downstream story derivation — an internal contradiction about whether Bruno may call gRPC directly or every client call must go through the gateway (FR-19/FR-21/FR-23 vs Assumption 4, §2.2, SM-1). The spine-drift for the two new clusters (API Gateway, Terraform) is honestly flagged and tracked, but it means the PRD's stated "binds to the spine" claim is not yet true for FR-21..FR-26.

**Gate: PASS with conditions** — resolve the gRPC ingress contradiction and land the spine revision before epics/stories are derived from §4.7/§4.8.

---

## 1. Decision-readiness — **adequate**

Decisions are stated as decisions with trade-offs in the addendum: API Gateway v2 vs v1 (chosen/rejected/why, addendum §Options Considered), Terraform vs aws cli vs control-plane calls. The PRD itself correctly defers technical detail to the spine and addendum rather than duplicating.

- **HIGH — FR-21..FR-26 have no spine home, so the PRD's stated binding is unsatisfied.**
  `prd.md` §0 (line 13) says the PRD "binds to [the spine] and does not duplicate it." But §4.7 (API Gateway as single ingress) and §4.8 (Terraform provisioning) are PRD additions the spine does **not** contain: the spine's structural seed still shows `C[Client] -->|HTTP multipart upload| U[upload-service]` (direct client→service), and no Terraform. The addendum's "Known Drift" (line 28) discloses this honestly and tracks it as the next step.
  *Fix:* elevate the spine revision to a named gated prerequisite for story derivation (the addendum already implies it — make it explicit), and optionally mark FR-21..FR-26 "pending spine binding" so downstream readers know these two clusters rest on the PRD alone.

- **MEDIUM — gRPC testing-surface decision is not resolved cleanly (spans this dimension and dimension 6).**
  §4.7/FR-23 say gRPC "stays strictly internal service-to-service"; Assumption 4 and §2.2 (line 33) explicitly allow Bruno gRPC "for direct service testing." The PRD never reconciles "gateway is the only client ingress" with "Bruno sends direct gRPC." See finding under dimension 6 — it is a decision the PRD made two ways.

- **Low positive note:** producer assignment (only processing-service mints terminal statuses), eventId determinism from `(videoId, status)`, and the port-table-is-normative rule are all carried from the spine without drift. Good.

---

## 2. Substance over theater — **strong**

- No persona theater: the UJ has a persona/context block, but for a solo-lab doc the named protagonist (Kygor) is the actual user, and the JTBD list (§2.1) is substantive. Earned, not furniture.
- No NFR theater: no fake "5 nines / sub-second" claims anywhere. SM-C1 ("do not optimize for throughput/latency/scale") is exactly the right anti-theater guardrail for a learning lab.
- No vision theater: Vision (§1) is two concrete sentences tied to a measurable outcome (SM-1). "The lab succeeds when the builder can upload one video... and teach each decision" is grounded and coherent with §7.
- §2.2 Non-Users is honest ("no real end users, no auth"), which preempts pretending at production posture.

No findings. Every section earns its place.

---

## 3. Strategic coherence — **strong**

The thesis is a single end-to-end flow demonstrating named patterns, with a teaching payoff. SM-1 directly validates the thesis (upload → PROCESSED → history → search → all 5 services → gateway-only ingress → `terraform apply` rebuild), SM-2 validates the teaching JTBD, SM-C1 fences scope creep. FRs are individually traceable to UJ-1 ("Realizes UJ-1" on every cluster).

- **LOW — SM-1 claims "every client call made through the API Gateway" (line 374), which Assumption 4's sanctioned Bruno gRPC direct testing contradicts.** Same root cause as the dimension-6 HIGH finding; the fix there resolves this too.

- **LOW — SM-1's range "FR-5..FR-26" omits FR-1..FR-4 (shared contract).** Defensible (shared contract is a build-time precondition, not validated by the flow), but the range is slightly misleading — consider "FR-1..FR-26 with FR-1..4 as build-time preconditions" or leave as-is with a note.

---

## 4. Done-ness clarity — **strong**

Every FR (FR-1..FR-26) has a "Consequences (testable)" block — a strong pattern for downstream stories. No vague "reasonable / graceful / user-friendly" phrasing found. A few thresholds are left open:

- **MEDIUM — FR-20 "rebuild" has no trigger mechanism.**
  `prd.md` line 265-270: "After clearing the index, a rebuild repopulates it from metadata" — but nothing says how a rebuild is *invoked* (Bruno-callable HTTP/gRPC action? documented script?). The search story cannot be derived without it.
  *Fix:* specify the trigger (e.g., "a Bruno-invokable search-service action" or "a documented script") or explicitly defer it.

- **LOW — FR-5 "returns success" (line 115) is unspecified.** Pin the expected HTTP status (e.g., 201/200) so a Bruno assertion is unambiguous.

- **LOW — FR-12 "ListVideos respects a limit" (line 189) names no limit.** Pin a default or the invariant ("result count ≤ requested limit").

- **LOW — FR-6 "defensive generate-if-absent is fallback only" (line 123)** is a design constraint whose test is fuzzy; the listed consequence ("required video_id") is the testable half. Add the inverse assertion (omitting `video_id` still yields a record, idempotently) or drop the fallback wording from the consequence.

Positive: FR-10/FR-14 (state machine regressions → `FAILED_PRECONDITION`), FR-16 (duplicate eventId → no second row), FR-25/26 (terraform-only bring-up, no aws cli) are all crisply testable.

---

## 5. Scope honesty — **strong**

- Non-Goals (§5) are explicit, numbered, and include the two notable ones for this lab (no FAILED producing path, no SSE live streaming).
- Per-cluster Out-of-Scope blocks exist on every cluster that has deferrals (FR-4, FR-7, FR-10, FR-15, FR-17, FR-20, FR-24, FR-26).
- Assumptions Index (§9) has 6 tagged items; ffmpeg assumption roundtrips to FR-10's out-of-scope ("ffmpeg install strategy deferred to Dockerfile story"). The gateway path-form assumption (A2) roundtrips to §4.7 and the addendum's technical facts.
- The addendum's "Known Drift" section is exemplary scope honesty — it names exactly what the spine lacks and what must change before downstream work.
- Open Questions (§8) density: 1 item. Appropriate for hobby stakes — the big open items (FAILED demo) are properly deferred with a revisit trigger.

- **LOW — "Working title — confirm" (line 9) is an untracked open item living in the header, not §8.** Either resolve it or move it to Open Questions.

- **LOW — §4 intro (line 62) enumerates clusters as "each service" + "the API Gateway" but omits the Shared Contract module (4.1) and Terraform (4.8) from that enumeration.** Cosmetic, but the enumeration should match the section list.

---

## 6. Downstream usability — **adequate**

IDs are clean: FR-1..FR-26 contiguous and unique across 8 clusters; UJ-1 single; SM-1/SM-2/SM-C1 unique; cross-references resolve (SM-1 → "FR-5..FR-26", addendum → "FR-25/26", clusters → "Realizes UJ-1"). Protagonist Kygor is named consistently in §0 and UJ-1. Glossary exists and is mostly used identically.

- **HIGH — gRPC ingress contradiction, the one gate-blocker.**
  - `prd.md` FR-21 (line 283): "no client call goes directly to a service."
  - `prd.md` FR-23 (line 299): "gRPC calls (metadata, search) are service-to-service only; they are not exposed through the gateway."
  - `prd.md` FR-19 (line 263): "Bruno gRPC/REST search through the gateway returns matching videos" — reads as if gRPC search goes *through the gateway*, which FR-23 forbids.
  - `prd.md` §2.2 (line 33) and Assumption 4 (line 391): Bruno makes "REST + gRPC requests"; gRPC is explicitly a sanctioned direct test surface.
  - SM-1 (line 374): "every client call made through the API Gateway."
  → Three FRs + an SM contradict the assumptions, and FR-19 is internally ambiguous (gRPC via gateway vs gRPC direct). A downstream search story cannot be written against this.
  *Fix:* add one explicit carve-out sentence, e.g. in §4.7: "Direct gRPC to metadata/search via Bruno is a sanctioned *test* surface and is not a violation of the gateway-only rule, which governs the *product* client surface." Then restate FR-19 as "Bruno REST search through the gateway; Bruno gRPC search direct to search-service for service-level testing only."

- **MEDIUM — Glossary term "VideoStatus" is defined (line 50) but never used in the body** (body consistently uses lowercase "status" and enum literals UPLOADED/PROCESSING/PROCESSED/FAILED). Either use the term where the state machine is discussed (§4.4) or drop it from the glossary.

- **LOW — §9 heading says "Index" but assumptions are un-ID'd bullets with no cross-references.** For story traceability, tag them A-1..A-6 and reference the ones stories consume (e.g., ffmpeg, gateway path form).

- **LOW — minor capitalization drift "gateway" vs "API Gateway"** throughout; acceptable, glossary anchor is clear.

---

## 7. Shape fit — **strong**

The shape is right for a hobby/solo capability spec: Vision → Target User → Glossary → Features (cluster + FR + testable consequences + out-of-scope) → Non-Goals → MVP → Success Metrics → Open Questions → Assumptions. It is not over-formalized (no acceptance-criteria boilerplate per FR, no weight/priority theater) and not under-formalized (every FR has a consequence). The UJ's persona/entry/path/climax/resolution structure is mildly more ceremony than a solo lab needs, but it is single-use and readable — acceptable. The addendum is a clean seam for technical/rationale detail. This is the right altitude.

No findings.

---

## Mechanical Notes

- **Glossary drift:** `VideoStatus` defined (line 50) and unused in body (MEDIUM, see dim 6). All other glossary terms (`videoId`, `eventId`, `Record store`, `Derived service`, `Shared contract`, `Stateless worker`, `ministack`, `API Gateway`) are used consistently.
- **ID continuity:** FR-1..FR-26 contiguous and unique (4.1:1-4, 4.2:5-7, 4.3:8-11, 4.4:12-15, 4.5:16-17, 4.6:18-20, 4.7:21-24, 4.8:25-26). UJ-1, SM-1, SM-2, SM-C1 unique. Cross-references resolve.
- **Assumption roundtrip:** A5 (ffmpeg) → FR-10 out-of-scope ✓; A2 (gateway path form) → §4.7 + addendum ✓; A4 (Bruno gRPC) → **conflicts** with FR-21/FR-23/SM-1 ✗ (HIGH, dim 6); A6 (H2) → spine only, fine.
- **Protagonist naming:** Kygor named in §0 and UJ-1. Consistent.
- **Spine binding:** addendum Known-Drift verified against `ARCHITECTURE-SPINE.md` — the spine's structural seed (line 173) indeed shows direct client→service HTTP with no gateway and no Terraform. The disclosure is accurate and honest.

---

## Finding Register

| # | Severity | Location | Finding | Suggested fix |
|---|----------|----------|---------|---------------|
| 1 | HIGH | FR-19 (l.263), FR-21 (l.283), FR-23 (l.299), §2.2 (l.33), Assumption 4 (l.391), SM-1 (l.374) | gRPC direct-testing surface contradicts gateway-only ingress; FR-19 ambiguous ("gRPC... through the gateway") | Add explicit carve-out in §4.7 (product surface vs test surface); restate FR-19 |
| 2 | HIGH | §0 (l.13) vs §4.7/§4.8 vs addendum Known-Drift | FR-21..FR-26 have no spine home; PRD's "binds to spine" claim not yet satisfied for two clusters | Make spine revision a gated prerequisite for story derivation; mark FR-21..26 "pending spine binding" |
| 3 | MEDIUM | FR-20 (l.265-270) | Rebuild trigger mechanism unspecified; search story cannot be derived | Name the trigger (Bruno-invokable action or script) or defer explicitly |
| 4 | LOW | FR-5 (l.115), FR-12 (l.189), FR-6 (l.123) | Open thresholds: "success" status, list limit, fallback-only test | Pin status codes / limit invariant / inverse assertion |
| 5 | LOW | Glossary (l.50) | `VideoStatus` defined, never used | Use in §4.4 or drop |
| 6 | LOW | §9 (l.386) | "Index" but assumptions un-ID'd, no cross-refs | Tag A-1..A-6 and reference where consumed |
| 7 | LOW | §4 intro (l.62), header (l.9) | Cluster enumeration omits 4.1/4.8; "Working title — confirm" untracked | Match enumeration to section list; move title confirm to §8 or resolve |
