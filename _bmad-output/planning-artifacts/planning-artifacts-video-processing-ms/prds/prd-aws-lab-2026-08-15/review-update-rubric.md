# PRD Quality Review — Video processing platform (update run, 2026-08-15)

## Overall verdict

The update run successfully reconciled the PRD to the revised spine: AD-9 (single ingress) and AD-10 (Terraform) now have a faithful PRD home (FR-21..26), the client-facing surface is enumerated to exactly upload/history/search, the stage is consistently `dev`, the search rebuild trigger is gRPC/admin-only, and names authority sits in Terraform with the AD-6 enforcement hook. The two highest-severity findings from the pre-update review (FR-21..26 with no spine home; the unreconciled gRPC-vs-gateway contradiction) are resolved. What remains is phrasing-level: an absolute "Bruno never calls a service directly" sentence in §4.7/glossary that still contradicts the FR-21 gRPC carve-out that the update itself added, one unqualified "HTTP facades" consequence in the metadata cluster that the spine (AD-9) now explicitly forbids for metadata, and a few mechanical nits in the addendum. No critical contradictions; the PRD is decision-ready for story derivation.

## Update reconciliation check (task-specific)

**Do PRD and spine contradict anywhere?**

- **Client-facing surface (upload/history/search only):** consistent. §4.7 description, FR-21, FR-22, §2.2, §6.1, and SM-1 all enumerate exactly the three journeys. No stray client surface anywhere; §4.6 search keeps its gRPC `VideoSearchService` as internal/debugging only. ✓
- **Gateway path == facade path:** consistent. FR-22 states route path == facade client path, no edge rewriting, per AD-9's authoritative path table; consequences require pass-through unchanged (status codes + `{"error": ...}` bodies), matching AD-7/AD-9. ✓
- **No HTTP facade for metadata/processing:** PRD is silent (does not claim one), and the spine imposes it in AD-9. Silence is acceptable given §0's bind-and-don't-duplicate stance — *except* FR-15's consequence says gRPC error codes "map to 404/400/409/409 over HTTP facades" while living in the Metadata cluster. Metadata has no HTTP facade per AD-9 (web port = actuator/health only), so an unqualified "HTTP facades" in the metadata cluster invites a story to build one. See finding F2.
- **gRPC-only rebuild:** consistent. FR-20 consequence explicitly states "No HTTP rebuild surface — the rebuild trigger is gRPC/admin only (spine AD-9…)". Matches AD-9 and spine Deferred item. ✓
- **Stage `dev`:** consistent. A-2, §0 NOTE FOR PM, and addendum (lines 23, 24, 36) all pin `dev` with a deployment and `terraform output` handoff. No active `$default` remains (addendum line 36 references it only as a superseded note — see F4). ✓
- **Names authority:** consistent in substance. FR-3 consequence mirrors AD-6 (Names == `terraform output`, build fails on divergence, rename is a coordinated change). One citation nit: FR-3's header cites "(spine AD-10)" where the names authority actually lives in AD-6 (AD-10 governs provisioning, not naming authority). See F3.

**Does the PRD still read coherently after the edits?**

Yes, with one wording-level exception. FR numbering is intact (FR-1..FR-26, contiguous across 4.1→4.8); cross-references resolve (FR-10→FR-14, FR-16↔FR-18 poison rule, §0→§4.7/FR-21..24 and §4.8/FR-25/26, SM-1→FR-1..FR-26). §0 NOTE FOR PM reads correctly as "bound … spine wins on conflict," which matches the re-scope. The exception is the §4.7 description (line 284) and glossary (line 61) still carry the absolute "Bruno never calls a service directly" / "no client calls a service directly" phrasing, which sits one line away from the FR-21 carve-out that explicitly permits direct Bruno gRPC. See F1.

**Does the addendum now match the spine?**

Yes. Provisioned-resource list (line 24) matches AD-10 (2 buckets, 2 queues, gateway + routes/integrations/`dev` stage); client routes (line 25) match AD-9 path table with route-path==facade-path; `host.docker.internal` addressing (lines 26, 33) matches AD-9; stage `dev` + `terraform output` (lines 23, 36) matches AD-10; payload-format 1.0 passthrough (line 34) matches AD-9; bring-up order (line 37) matches AD-10. The "Known Drift" section is reframed as resolved (header: "resolved 2026-08-15"), no leftover follow-up framing. Two nits: (a) the header is now a misnomer — the section body reads as a spine-binding summary, not drift; (b) line 36's "replaces the addendum's earlier `$default`-stage / `ms-custom-id` note below" points the wrong direction (the note is *above*, line 23, and still exists there in downgraded form). See F4. Also, AD-10's "a running service that survives a `terraform destroy` + re-apply MUST be restarted" caveat is absent from both the addendum and FR-25's consequence — see F5.

**Are FR-21..26 consequences still testable and consistent with AD-9/AD-10?**

Yes. FR-21 (Bruno targets gateway URL only; direct-port request outside exercised path; gRPC carve-out with "does not relax FR-23") — testable, consistent. FR-22 (route reaches correct facade; responses unchanged) — testable, consistent with AD-9. FR-23 (gRPC service-to-service only; gateway exposes HTTP routes only) — consistent. FR-24 (open gateway; requests succeed without credentials) — testable. FR-25 (apply creates buckets/queues/gateway with routes/integrations/stage; fresh ministack up by apply alone; destroy/re-apply rebuilds) — testable, consistent with AD-10. FR-26 (setup/teardown steps contain no `aws` invocations; all creation attributable to Terraform) — testable. One caveat: FR-25's "destroy/re-apply rebuilds the environment" consequence does not carry AD-10's restart caveat (F5).

**Any requirement the spine now imposes that the PRD still contradicts or is silent on?**

Silent-but-governed (acceptable, spine is authoritative via §0): port table, channel names (`channel.metadata`/`channel.search`), AD-6 profile-scoped config, AD-5 event-DTO coordinated-cutover half. Not silent on the load-bearing ones. The only real near-contradiction is FR-15 vs AD-9's no-metadata-HTTP-facade rule (F2). The one genuinely dropped detail is the destroy/re-apply restart caveat (F5).

---

## 1. Decision-readiness — strong

The update turns the prior gated-uncertainty callout into a real decision: §0 NOTE FOR PM now states the features are *bound* (AD-9/AD-10) and that "the spine's pinned details (path table, stage `dev`, rebuild surface) win" — that is a decision with a conflict rule, not a hedge. Trade-offs stay in the addendum (v2 vs v1 HTTP API; Terraform vs `aws cli`) with the rejected option's cost named. The single Open Question (§8, FAILED-demo) is genuinely open for a learning lab. Not a "smooths everything to neutral" PRD.

### Findings
- none critical/high.

## 2. Substance over theater — strong

One named persona (Kygor) who actually drives UJ-1's journey and the teaching JTBD; no persona zoo. NFRs are thresholded ("not production-ready", "no auth, no multi-tenancy", SM-C1 anti-metrics), not boilerplate. The two new feature clusters earn their place: §4.7 is the pattern the lab exists to demonstrate (single ingress), §4.8 is a hard user rule (no `aws cli`). No furniture found.

### Findings
- none.

## 3. Strategic coherence — strong

Thesis holds: "learning and confidence to teach the patterns — not production readiness." Every cluster names the pattern it demonstrates and maps to a spine AD (consistent with §0's bind-don't-duplicate). Prioritization follows the thesis (happy-path `PROCESSED` end-to-end is the bar; FAILED/SSE/search-richness all deferred with reasons). SM-2 validates the teaching JTBD; SM-C1 names the anti-goal (throughput/scale polish = scope creep). MVP scope kind is coherent (platform/lab).

### Findings
- none.

## 4. Done-ness clarity — strong

Every FR (1..26) carries a "Consequences (testable)" block; the earlier open thresholds (FR-5 status code, FR-12 limit, FR-20 trigger) were tightened in the prior finalize pass and the update narrowed FR-20 further to a named surface (gRPC/admin, startup + explicit). FR-3's "verification asserts Names == `terraform output`, build fails on divergence" is crisp and testable. No vague "graceful/reasonable/user-friendly" phrasing found. The one soft spot is FR-15's unqualified "HTTP facades" (F2).

### Findings
- **[medium]** FR-15 consequence implies metadata has an HTTP facade (§4.4, FR-15) — "NOT_FOUND, INVALID_ARGUMENT, ALREADY_EXISTS, FAILED_PRECONDITION map to 404/400/409/409 over HTTP facades" sits in the Metadata cluster, but AD-9 forbids a metadata HTTP facade (web port = actuator/health only). The mapping legitimately applies to the *client-facing* facades (upload/notification/search). *Fix:* qualify as "the client-facing HTTP facades (the AD-9 enumerated set)" or add a half-line noting metadata/processing expose no HTTP facade.

## 5. Scope honesty — strong

Non-Goals list and per-cluster Out-of-Scope blocks are explicit; `[NOTE FOR PM]` sits at a real tension (§0 spine binding), not a safe checkpoint. Assumptions are indexed A-1..A-6; the two spine-alignment assumptions (A-2 stage `dev` + `terraform output`; A-3 `host.docker.internal`) were correctly updated and now match AD-10/AD-9. Open-items density (1 OQ + 6 assumptions + 1 NOTE) is proportionate to a green-light-to-build solo lab. The prior "Known Drift follow-up" framing is gone from the addendum (resolved).

### Findings
- none critical/high.

## 6. Downstream usability — adequate

IDs are contiguous and unique (FR-1..FR-26; UJ-1; SM-1/SM-2/SM-C1); cross-references resolve (FR-10→FR-14, FR-16↔FR-18, §0→clusters, addendum→FR-25/26, FR-20). Glossary is stable (dot-events vs dash-queues explained; "status history" standardized). Protagonist Kygor is named and consistent. A story extractor can pull each cluster alone. Three items pull this to *adequate* rather than *strong*: the §4.7/glossary absolute phrasing vs the FR-21 carve-out (F1), the FR-15 facade ambiguity (F2), and the assumption tags existing only in the index (Mechanical note M1).

### Findings
- **[medium]** Absolute "no direct client call" phrasing contradicts the FR-21 carve-out (§4.7 description line 284; glossary line 61; UJ-1 line 44 "every client call made through the API Gateway") — the update added the gRPC debugging carve-out to FR-21 and §2.2/A-4 but left the surrounding prose absolute. A downstream story writer could re-derive the contradiction the update meant to kill. *Fix:* soften to "no direct client **HTTP** call" / "client HTTP traffic through the gateway only; direct gRPC is the FR-21 debugging surface" in the description and glossary.

## 7. Shape fit — strong

Correct shape for the stakes: solo builder + internal teaching lab = capability-spec format with light UJ (UJ-1 with named persona is proportionate, not overhead). Feeds architecture → epics/stories, and the bind-don't-duplicate stance plus the addendum's downstream "Technical Facts" make that handoff clean. Not over-formalized.

### Findings
- none.

## Mechanical notes

- **[low]** M1 — Assumptions roundtrip is index-only: A-1..A-6 exist in §9 but no inline `[ASSUMPTION: …]` tags appear in the body. All six are faithful to the text they annotate (A-2/A-3 updated correctly), but the roundtrip convention is one-directional. *Fix:* inline-tag where each is first assumed, or state the index-is-the-source convention once.
- **[low]** M2 — FR-3 header cites "(spine AD-10)" (prd.md line 91) for names declared in Terraform; the names authority explicitly lives in AD-6 ("Names authority … declared in Terraform … a rename is a coordinated change"), AD-10 governs provisioning. The consequence correctly cites AD-6 — only the header parenthetical is mis-attributed.
- **[low]** M3 — Addendum line 36: "this replaces the addendum's earlier `$default`-stage / `ms-custom-id` note below" — direction is wrong (the note is *above*, line 23, and still present there in downgraded form), and it preserves the superseded `$default` token in a doc that is otherwise clean. *Fix:* drop the historical reference or point it at line 23.
- **[low]** M4 — Addendum §"Known Drift — Architecture Spine (resolved 2026-08-15)" is now a misnomer: the body reads as a binding summary ("Spine details that now govern this PRD and win on conflict"). Consider renaming to "Spine binding" so future readers don't re-open a resolved-drift ledger.
- **[low]** M5 — AD-10's restart caveat ("a running service that survives a `terraform destroy` + re-apply MUST be restarted; queue/bucket URLs change") is not carried into FR-25's consequence or the addendum. FR-25 says "destroy/re-apply rebuilds the environment from the same configuration," which is true but can mislead a verification story that reuses a running service. *Fix:* append the restart caveat to the addendum's bring-up-order bullet (line 37).

## Dimension summary

| Dimension | Verdict |
| --- | --- |
| Decision-readiness | strong |
| Substance over theater | strong |
| Strategic coherence | strong |
| Done-ness clarity | strong |
| Scope honesty | strong |
| Downstream usability | adequate |
| Shape fit | strong |
