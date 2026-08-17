# Input Reconciliation — PRD vs Architecture Spine

- **Review type:** source-extract (research/review agent; no code/file edits beyond this review)
- **Date:** 2026-08-15
- **Source (normative):** `planning-artifacts/architecture/architecture-aws-lab-2026-08-15/ARCHITECTURE-SPINE.md` (status: final)
- **Under review:** `planning-artifacts/prds/prd-aws-lab-2026-08-15/prd.md` (status: draft) + `addendum.md`
- **Method:** mapped each spine AD / convention / deferred item to PRD features and FRs; reverse-scanned each PRD requirement for spine contradictions; verified the addendum's drift claims against spine text.

---

## 1. Verdict

The PRD and spine **agree** on the core service/contract architecture (AD-1..AD-8 all have a faithful PRD counterpart). The PRD introduces **two additive divergences** the finalized spine does not yet bind — the API Gateway single ingress (4.7 / FR-21..FR-24) and Terraform provisioning (4.8 / FR-25/26). Both are **correctly and accurately documented** in the addendum's "Known Drift" section. **No PRD requirement contradicts any spine AD.** Three lower-severity gaps remain where spine-mandated behavior is under-specified in the PRD. Spine revision is a prerequisite for epics/stories derivation.

---

## 2. Agreement Register — spine decisions faithfully carried into the PRD

| Spine decision | PRD coverage | Verdict |
| --- | --- | --- |
| AD-1 layered per-service (boundary → service → persistence) | JTBD 2.1; §4 preamble ("each service demonstrates a named pattern") | Covered; delegated to spine by design (§0 binding policy) |
| AD-2 single-writer ownership; `videoId` minted once at ingress; `CreateVideo.video_id` required; generate-if-absent = fallback | FR-6 (+ consequence: "required … fallback only"); FR-12; FR-13 | Covered |
| AD-2 S3 object ownership (upload writes uploads bucket; processing reads uploads, writes processed bucket) | FR-5, FR-9 | Covered |
| AD-2 idempotent create by ingress id; `ALREADY_EXISTS` = foreign collision | FR-13 (both consequences) | Covered |
| AD-3 state machine `UPLOADED → PROCESSING → PROCESSED|FAILED`; terminal final; regression/terminal-exit → `FAILED_PRECONDITION`; same-status re-assertion idempotent | FR-10, FR-14 (all consequences) | Covered |
| AD-3 producer assignment (only processing mints PROCESSING/terminal; upload never emits PROCESSING) | FR-10, §4.3 description, FR-7/FR-11 | Covered |
| AD-3 terminal event: exactly one `video.processed` per transition; `failureReason` iff FAILED | FR-11 (+ consequences); FR-1 carries failureReason | Covered |
| AD-3 status-first ordering (status write before publish) | FR-7 "after the record is created", FR-11 "on terminal completion" | Covered (implied, consistent) |
| AD-4 deterministic `eventId` from `(videoId, status)`, restart-proof dedupe | Glossary, FR-2 consequence, FR-8/FR-11/FR-16 | Covered |
| AD-4 derived-store natural keys (search = videoId, history = eventId append) | FR-16 ("duplicate eventId does not append"), FR-18 | Covered |
| AD-5 additive-only versioning; new proto version + coexistence; both versions exposed during migration | FR-1, FR-2, FR-4 (+ consequences) | Covered |
| AD-6 config-not-code; normative port table; `channel.metadata`/`channel.search` | §6.1 "fixed port table; config-not-code"; §0 binding | Covered (delegated, not restated as FRs — see §4 gap G-3) |
| AD-7 error mapping table (incl. `FAILED_PRECONDITION`→409) | FR-15 (all four codes) | Covered |
| AD-8 publisher allow-list (only upload publishes `video.uploaded`; only processing publishes `video.processed`) | FR-7, FR-11 | Covered |
| AD-8 duplicate `video.uploaded` for processing/terminal video = acked no-op, no metadata lookup | FR-8 consequence | Covered |
| Deferred: no outbox (carve-out documented), no reconciliation job, no DLQ policy | §4.2, §4.4 out-of-scope; §6.2 | Covered |

**Agreement is strong.** No spine AD is contradicted.

---

## 3. Divergences (PRD introduces decisions the spine does not bind)

### D-1 — API Gateway as single client ingress — **HIGH** (KNOWN DRIFT)
- **Location:** spine Structural Seed (lines 172–185: `C → U`, `N → C SSE`, `S → C HTTP+gRPC`); spine paradigm/scope (lines 6–7); AD-6 normative port table (no gateway); spine Stack (line 165: ministack listed as "S3 + SQS" only). PRD §4.7 (FR-21..FR-24), Glossary "API Gateway", §6.1, §7 SM-1.
- **Nature:** additive topology change. The spine's client-facing surfaces (direct HTTP to upload/notification/search) are replaced by a single gateway; gRPC stays internal (consistent with spine). No spine AD is broken, but the spine's structural seed, dependency diagram, scope line, and ministack emulation list are now stale.
- **Addendum accuracy:** **confirmed accurate.** The "Known Drift" note (lines 28–31) correctly identifies that the spine "does not yet reflect" the gateway and Terraform additions and that the structural seed / dependency diagram / deployment-envelope sections still show direct client→service HTTP. Substance is correct. Minor wording nit: the note says the **dependency diagram** shows direct client→service HTTP, but that appears in the **structural seed**; the dependency diagram (flowchart LR, lines 119–132) shows only service-to-service edges with no client at all. Not a material error.
- **Ripple the spine must bind on revision:** gateway in scope line + structural seed; gateway URL/data-plane (`/_aws/execute-api/...`) vs the AD-6 normative port table (gateway is reached via ministack:4566, not a service port — AD-6's "changing a port is a spine change" must not be misread to cover it); ministack Stack row should gain "API Gateway v2"; AD-8/AD-7 continue to govern the facades behind the gateway unchanged.
- **SSE nuance:** spine shows a direct client SSE channel (`N → SSE → C`); the PRD explicitly excludes live SSE (§4.5 out-of-scope, §5) and the gateway routes only upload/history/search HTTP facades. So the spine's SSE channel is effectively dropped, not relocated behind the gateway. Consistent with PRD scope, but a spine-revision should state the gateway deliberately does **not** expose SSE.

### D-2 — Terraform-managed ministack provisioning — **HIGH** (KNOWN DRIFT)
- **Location:** PRD §4.8 (FR-25/26), §6.1, §5, Assumptions Index. Spine: **zero mention of any provisioning tool** (only "ffmpeg provisioning", unrelated, line 245). Spine scope (line 7) claims to bind "the deployment envelope (ministack now, real AWS later)", and AD-6 covers env wiring, yet nothing in the spine states *how* backing resources (buckets, queues) come to exist.
- **Nature:** gap, not contradiction. The spine's compose/ministack is presented as pre-existing backing services with no creation mechanism; the PRD hard-rules `terraform apply` as the sole creator and bans `aws cli` and out-of-band control-plane calls.
- **Addendum accuracy:** **confirmed accurate** (drift listed; Terraform rationale at lines 14–18; provisioned-resource list at line 24).
- **Spine revision needed:** the "Deployment envelope" / Deferred section should bind Terraform as the provisioning mechanism (ministack endpoint `http://localhost:4566`, dummy creds, region `us-east-1`), so FR-25/26 have a spine-level home rather than living only in the PRD.

---

## 4. Gaps — spine-mandated behavior absent or thin in the PRD

### G-1 — AD-8 metadata-validation duty for derived consumers — **MEDIUM**
- **Spine:** AD-8 (line 115) mandates search/notification validate terminal events against metadata (`GetVideo`) before indexing/history; processing's duplicate no-op is status-based and performs **no** metadata lookup.
- **PRD:** FR-16 and FR-18 describe consume-and-record/index with no validation step; FR-8 correctly captures the no-lookup no-op. The derived-store validation duty is absent.
- **Recommendation:** add a consequence to FR-16/FR-18 ("a terminal event whose videoId fails metadata validation is not indexed/recorded; UNAVAILABLE/deadline are retried, never dropped") or explicitly delegate to AD-8.

### G-2 — AD-4 normative queue-subscription exclusivity — **LOW**
- **Spine:** AD-4 (line 80) — processing subscribes **only** to `video-uploaded`; search/notification subscribe **only** to `video-processed`.
- **PRD:** FR-8/FR-16/FR-18 state each subscription but never assert exclusivity. Not contradicted, but the exclusivity rule (a structural guard) has no testable consequence.
- **Recommendation:** add "…and no other queue" to FR-8/FR-16/FR-18 consequences.

### G-3 — AD-5 coordinated cutover / dual-publish dedupe for event `schemaVersion` bumps — **LOW**
- **Spine:** AD-5 (line 87) — a JSON-event `schemaVersion` bump is a coordinated cutover; dual-publish emits one `eventId` (dedupe, not a second row).
- **PRD:** FR-4 covers proto versioning and coexistence but stops at proto; the event-DTO half of AD-5 (coordinated cutover, dedupe across a dual-publish window) is not reflected.
- **Recommendation:** extend FR-4 consequences to the event DTOs (`schemaVersion` bump requires coordinated cutover; republish under two schemaVersions still yields one `eventId`).

### G-4 — Gateway client URL not pinned (informational) — **LOW**
- PRD Assumptions (line 389) describes the ministack gateway data-plane path form but never pins the concrete client-facing gateway URL. Since FR-21's testable consequence forbids direct service ports, the builder needs the exact gateway URL. Suggest pinning it (e.g., `http://localhost:4566/_aws/execute-api/{apiId}/$default/...` with `ms-custom-id`, per addendum line 23).

---

## 5. Contradiction scan (PRD requirements vs spine ADs)

- **Gateway vs AD-6 normative ports:** none. Services keep their facades on the normative ports; the gateway integrates to them; FR-21 only removes the *client* from the direct path. AD-6's "changing a port is a spine change" is untouched.
- **Gateway vs AD-7:** none. FR-22 requires HTTP responses (status codes, `{"error": ...}` bodies) to pass through unchanged — the PRD's most likely contradiction point, and it reinforces AD-7.
- **FR-23 (gRPC stays internal) vs spine:** consistent — the spine already treats all gRPC as service-to-service.
- **Terraform vs AD-6 (config-not-code):** none. Provider config (endpoint/region/dummy creds) lives in `.tf`, not in service code; AD-6 continues to govern the services' `application.yml`. Note the gateway's integration config (target service URLs) lives in Terraform rather than `application.yml` — outside AD-6's stated bindings, another spine-revision item.
- **FR-25 "no resource created out-of-band" vs spine Deferred (outbox/reconciliation etc.):** none — those are deferred, not contradicted.
- **Port-table/SSE exclusions:** none (see D-1 SSE nuance).

**Result: zero contradictions.** All divergences are additive; the spine is behind the PRD, not the reverse.

---

## 6. Reverse scan — spine items with no PRD counterpart (deliberate, by binding policy)

- AD-1 structure rule, AD-6 full config rule, AD-7 SSE error frames, port table numerics, AD-2 outbox carve-out, AD-3 reconciliation semantics, AD-8 allow-list mechanism: all intentionally delegated to the spine per PRD §0 ("binds to it and does not duplicate it") and §4 preamble ("cross-cutting rules … governed by the architecture spine"). This delegation is sound; §4 gaps above are the ones that *should* surface as testable consequences anyway.

---

## 7. Addendum accuracy — final check

- Line 28–31 "Known Drift": **accurate** (verified against spine §1 paradigm, Structural Seed, AD-6/Stack). Only nit: attributes direct client→service to the dependency diagram; it is actually the structural seed (dependency diagram shows no client).
- Lines 22–23 ministack endpoint / gateway data-plane form: consistent with the spine's ministack port (4566) and the PRD Assumptions.
- Line 24 provisioned-resource list (2 buckets, 2 queues, API GW v2 + routes/integrations/`$default` stage): consistent with PRD FR-25.
- No factual drift in the addendum's options-considered sections.

---

## 8. Required spine revisions (downstream, for epics/stories derivation)

1. Add API Gateway (v2 HTTP API, ministack, data plane `/_aws/execute-api/{apiId}/{stage}/{path}`) to: scope line, Structural Seed (client → gateway → facades; SSE channel dropped), Stack (ministack row gains API Gateway), and note gateway is reached via ministack:4566, not a normative service port.
2. Bind Terraform as the sole provisioning mechanism for the deployment envelope (FR-25/26), including the `terraform apply`-brings-up-fresh-ministack invariant and no-`aws-cli` rule; move the addendum's provisioning facts into a spine AD or the deployment section.
3. Optionally surface G-1 (AD-8 validation duty), G-2 (subscription exclusivity), G-3 (event-schemaVersion cutover) in the spine's normative statements so the PRD and spine read together cleanly.
