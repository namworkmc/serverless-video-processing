---
title: Adversarial architecture review — round 3 (final re-check of round-2 fixes)
substrate: ARCHITECTURE-SPINE.md (2026-08-15 revision, N1–N9 applied)
reviewer: adversarial reviewer (one level down)
date: 2026-08-15
gate: architecture spine gate
round1: NO-GO (F1–F9, review-adversarial.md)
round2: NO-GO narrow (N1–N9, review-adversarial-round2.md)
verdict: NO-GO (narrow) — all nine round-2 findings are closed in letter, but the N6 fix opened a flat dependency contradiction (AD-8 mandates search/notification validate terminal events via metadata gRPC while the AD-8 dependency diagram grants them "consumes SQS events only" — the diagram even forbids the client the Deferred recovery path already requires), and three residuals remain (publisher eventId durability across restarts, same-status re-assertion event emission, reconciliation asymmetry on the ingest leg). Each closes with a one-line amendment; no structural flaw. Re-review after R1–R3 (ideally R4) land.
---

# Adversarial Review Round 3 — video-processing platform spine

**Method (unchanged).** For every invariant I construct two units one level down — two builders implementing the same slice, each required to obey **every** AD literally. Where both units can pass every AD yet build mutually incompatible services, the spine has a hole. Each finding states the two units, the divergence, and the concrete AD/Deferred change that closes it. Round-3 scope: (a) verify N1–N9 are actually closed, (b) hunt for seams the round-2 fixes opened, (c) stay proportionate — operational/deferred concerns (CI/CD, observability, DLQ, delivery channels, real AWS) are not re-raised.

**Contract amendments verified against** `spec-video-processing-microservices.md`: `CreateVideoRequest.video_id` is required; `VideoInfo` carries `failure_reason`; `UpdateVideoStatusRequest` carries `processed_key`, `duration_seconds`, `failure_reason` (note: **no** `event_id` — this becomes R2); both event DTOs carry `eventId` + `schemaVersion`. These match the spine text.

---

## Part A — Round-2 findings (N1–N9): closure verification

| # | Finding | Round-2 verdict | Round-3 status | Evidence in current spine | Residual |
| --- | --- | --- | --- | --- | --- |
| N1 | "Exactly one event" defeated by eventId-per-publish | HIGH | **CLOSED (letter)** | AD-4: "`eventId` is minted **once per logical event** (per transition) and **reused across publish retries** — a retry is an idempotent republish of the same `eventId`, never a new one; 'per publish' means per logical event, not per attempt." | **R2** — the guarantee assumes the publisher can *remember* the eventId; AD-2 makes it stateless, so the guarantee holds only within one process lifetime. |
| N2 | Outbox carve-out contradicts publisher allow-list | HIGH | **CLOSED (letter)** | AD-8: "A transactional-outbox relay inside metadata-service is **transport on the producer's behalf, not a second producer**: permitted only via the AD-2 outbox path and never invents an event metadata did not commit." | **R2** — the relay needs the producer's `eventId`, but `UpdateVideoStatusRequest` carries no `event_id`; eventId authority under the outbox is unstated. |
| N3 | Honours-or-generates reinstates a second minting path | HIGH | **CLOSED** | AD-2 Identity: "minted exactly once, at ingress (upload-service)… supplied via `CreateVideoRequest.video_id`, which is **required** from upload-service. Generate-if-absent is a **defensive fallback only**… A CreateVideo retry with one's own ingress-minted id is an **idempotent success**; `ALREADY_EXISTS` is a collision guard for a genuinely different caller." | Observation, not a finding: metadata has no caller identity, so "own id vs foreign caller" is practically undecidable — in effect every existing-id CreateVideo returns idempotent success and `ALREADY_EXISTS` is near-dead code. Harmless for the lab; noted, not raised. |
| N4 | Reconciliation cannot correct notification history | MEDIUM | **CLOSED** | Deferred "Dual-channel consistency": "Reconciliation corrects only stores whose natural key is metadata-derivable (search index by `videoId`); notification history is append-only, keyed on `eventId`, and is **not** reconciliation-corrected…" | **R4** — the scoping answer covers the PROCESSED leg's stores but silently leaves the UPLOADED leg (lost `video-uploaded` → video orphaned) with no mitigation. |
| N5 | Queue→consumer subscription only in a mermaid seed | MEDIUM | **CLOSED** | AD-4: "**Normative queue subscriptions:** processing-service subscribes only to `video-uploaded`; search-service and notification-service subscribe only to `video-processed`. A video becomes searchable only when its terminal `PROCESSED` event is consumed." | none |
| N6 | Poison conflates NOT_FOUND with UNAVAILABLE; unqualified validator set | MEDIUM | **CLOSED (letter)** | AD-8: "poison means a **successful negative lookup** — metadata returns `NOT_FOUND`…; `UNAVAILABLE`/deadline errors are transient and retried, never dropped. The **metadata-validation duty applies to derived-store consumers (search, notification)** on terminal events; processing-service's duplicate no-op is status-based and performs no metadata lookup." | **R1** — this duty was added on top of an unchanged dependency diagram that says search/notification "consume SQS events only" and grants them no client to metadata. |
| N7 | Event schemaVersion has no cutover procedure | MEDIUM | **CLOSED** | AD-5: "an event `schemaVersion` bump is a breaking change requiring a **coordinated cutover** — all consumers deploy before the publisher switches (verified by a named ops signal or a dual-publish window); rejection of an unknown version is the failure mode, not the plan." | none |
| N8 | AD-7 table not exhaustive; SSE error shape only | LOW | **CLOSED** | AD-7: "Any gRPC status not listed maps to 500. SSE error/close frames use `event: error`… AD-7 binds all client-facing surfaces; SSE non-error frames stay ungoverned until real delivery channels exist (Deferred)." | none |
| N9 | Same-status re-assertion undefined for new payload | LOW | **CLOSED (letter)** | AD-3: "Same-status re-assertion is idempotent for the state transition…; request-carried fields on a same-status re-assertion are **still applied (overwritten)**, because the **published event is a projection of the acknowledged payload**." | **R3** — the N9 rationale ("published event is a projection of the acknowledged payload") implies a re-assertion re-publishes, but AD-3 "every terminal transition emits exactly one event" + AD-4's per-transition eventId forbid a second event. Which wins is unstated. |

**Closure summary.** No round-2 finding survives as written. All nine are closed at the letter level. The new seams below are residuals of how the round-2 fixes were worded.

---

## Part B — New findings (R1–R4)

### R1 — HIGH — AD-8's mandatory metadata validation contradicts its own dependency diagram: search/notification cannot comply with the trust model they are told to implement

**ADs attacked:** AD-8 ("The metadata-validation duty applies to derived-store consumers (search, notification) on terminal events") vs the AD-8 dependency diagram (`S -->|consumes SQS events only| Q`, `N -->|consumes SQS events only| Q`, no S→M / N→M edge) — and, independently, the Deferred recovery path ("search index rebuildable from metadata-service.ListVideos").

**Two units (search-service):**

- **Unit A:** obeys AD-8's validation duty literally. It builds a metadata gRPC client (`channel.metadata`, `GetVideo`) and validates every `video.processed` before indexing; a `NOT_FOUND` is poison (drop + log), `UNAVAILABLE` is retried. This is a **search→metadata dependency** — the very edge the AD-8 diagram forbids ("consumes SQS events **only**").
- **Unit B:** obeys the dependency diagram literally. It depends on SQS only — no metadata client, no `channel.metadata` in its `application.yml`. It therefore **cannot perform the validation AD-8 mandates**; it indexes from the event payload alone, silently accepting phantom events the trust model says it must reject (and cannot use `ListVideos` for the rebuild the Deferred recovery path promises).

Both are fully AD-compliant under one authoritative clause of the same AD. Divergence: **A carries a metadata client + config + a validation code path; B has none and drops the duty.** The two services are wired differently, behave differently under a phantom/poison event, and the spine cannot be implemented as written — whichever clause a builder reads first, the other clause is violated. Note the diagram was not touched by the N6 fix; N6 added the mandatory duty *on top of* it, so this contradiction is opened by the fix. The prose immediately after the diagram ("every cross-service sync dependency is gRPC through video-common") even *contemplates* S/N→M edges, making the "SQS events only" annotation actively misleading. Notification-service has the identical seam (its validation duty is equally unconditional).

**Close it:** in AD-8's dependency diagram add `S -->|gRPC client (GetVideo / ListVideos)| M` and `N -->|gRPC client (GetVideo)| M`, and change the annotations from "consumes SQS events only" to "consumes SQS events; validates via metadata gRPC". In AD-6, state that search-service and notification-service also use the pinned `channel.metadata` for validation/rebuild (only upload and processing are named today).

---

### R2 — MEDIUM — AD-4's "mint once, reuse across retries" is unimplementable by a stateless publisher: nothing survives a restart, and no contract channel exists to recover the eventId

**ADs attacked:** AD-4 ("`eventId` … minted once per logical event and reused across publish retries — never a new one"), AD-2 ("`processing-service` is a **stateless worker**"), and the outbox carve-out's need for the producer's eventId.

**Two units (processing-service):**

- **Unit A:** honors AD-4 literally — mints the terminal `eventId` once per transition and reuses it on every publish retry. Being stateless (AD-2), it keeps that id in memory. Now crash A between the `UpdateVideoStatus(PROCESSED)` write and the SQS **ack** of `video.uploaded`. SQS at-least-once redelivers the same `video.uploaded`; a fresh A instance consumes it, its no-op memory (AD-4's "already-processing/terminal video" rule) is empty, it re-transcodes, re-asserts PROCESSED (idempotent, N9), and **cannot know the old eventId** — it mints a new one and republishes. notification-service (natural key = `eventId`, append) stores **two** rows; the client receives **two** `PROCESSED` SSE frames. AD-3's "exactly one event" and AD-4's "never a new one" both fail, on the exact redelivery path the ADs exist to cover.
- **Unit B:** recognizes the guarantee requires memory and quietly adds one — a local `eventId` table in processing-service — which **violates AD-2's stateless-worker rule**, or cheats by re-deriving deterministically (which contradicts AD-4's "UUID, publisher-generated").

Both obey some clause of the spine and violate another. The spine claims N1 closed, but its fix assumed the publisher can *remember* the eventId; AD-2 forbids the memory, and the one service that *could* remember it (metadata, the acknowledged store) has **no contract field for it** — `UpdateVideoStatusRequest` carries no `event_id`. The same hole breaks the N2 outbox carve-out: its relay "publishes the producer's event," but the producer's eventId never crosses the gRPC boundary, so under the outbox path either metadata mints the eventId (silently becoming the eventId authority AD-4 assigns to the publisher) or the contract grows a field. The Deferred "Dedupe retention" item covers **consumer** dedupe durability only; publisher eventId retention is unmentioned anywhere.

**Close it** (pick one, one sentence in AD-2/AD-4): **(a)** make the terminal eventId deterministic and stateless — "the terminal event's `eventId` is derived as `UUID5(videoId, 'video.processed')` (deterministic, restart-proof; the 'UUID' in AD-4 is satisfied by derivation)" — zero contract change; or **(b)** add required `event_id` to `UpdateVideoStatusRequest` and persist it in the record so the publisher can recover it after restart (and the outbox relay can relay it, closing the N2 residual in the same stroke); or **(c)** explicitly extend the Deferred dedupe-retention entry to the publisher side — "in the ministack lab the publisher's eventId may be in-memory; the reuse guarantee then holds per-process, and duplicate notification history across a restart is accepted (deduped per R2's N4 clause)". Recommend (a) for the lab.

---

### R3 — MEDIUM — AD-3's "the published event is a projection of the acknowledged payload" vs AD-3/AD-4's "exactly one event per transition": a same-status re-assertion with new payload is ungoverned, and the two round-2 fixes read differently

**ADs attacked:** AD-3 (N9 line: "request-carried fields … are still applied (overwritten), because the **published event is a projection of the acknowledged payload**" vs "every terminal transition … emits **exactly one** event"), AD-4 ("`eventId` minted once per logical event").

**Two units (processing-service):**

- **Unit A:** reads "exactly one event per terminal transition" as total — a same-status PROCESSED re-assertion (a re-transcode, or a corrected completion with a new `processedKey`/`durationSeconds`) applies the fields to the store (N9) but **publishes nothing**. Derived stores keep the stale `processedKey`; the record and the derived stores permanently disagree.
- **Unit B:** reads N9's rationale ("the published event is a projection of the acknowledged payload") as "every acknowledged payload change is projected" — a re-assertion with a new payload is a new logical event (new `eventId`, which N1's "per logical event" permits) → a **second** `video.processed`. notification history gets two rows and the client a second `PROCESSED` frame; search upserts fresh.

Both obey AD-3's letter under one of the two clauses. Divergence: **stale derived stores vs duplicate notifications.** The trigger is dormant today — AD-4's no-op rule (and the deferred FAILED-retry feature) block most re-transcode paths — but it activates the moment reprocessing or transcode-retry-with-new-output exists, and it is a genuine contradiction between two round-2 fixes, exactly the class this round is hunting for.

**Close it:** one AD-3 sentence: *"a same-status re-assertion applies request-carried fields to the store only and does **not** emit an event — the event for a transition is emitted once, with the payload acknowledged at the transition; a derived store may therefore lag the record after a re-assertion (accepted)."*

---

### R4 — LOW — Reconciliation's N4 scoping covers only the PROCESSED leg; a lost `video-uploaded` orphans the video in UPLOADED with no stated mitigation

**ADs attacked:** Deferred "Dual-channel consistency" ("the residual failure mode is 'owner advanced, event missing'. A reconciliation job … is the accepted mitigation"), N4's scoping fix ("Reconciliation corrects only stores whose natural key is metadata-derivable").

**Two units (ops/reconciliation):**

- **Unit A:** reconciliation reads metadata as truth, finds videos stuck in `UPLOADED` (CreateVideo acknowledged, `video-uploaded` lost — the *ingest leg's* "owner advanced, event missing"), and **re-enqueues `video-uploaded`** to re-drive the pipeline. This writes SQS (transport), not a store, so it does not touch the "corrects only stores" rule, but the spine never sanctions it.
- **Unit B:** reconciliation corrects only stores (search upsert), per the N4 fix's literal text. A lost `video-uploaded` leaves the video in `UPLOADED` **forever** — processing never fires, search never sees a terminal event, notification never sees anything; nothing in the spine repairs or even acknowledges the orphan.

Both obey the Deferred text as scoped by N4. Divergence: **pipeline repair vs permanent orphan.** The ingest leg has the identical dual-channel write (CreateVideo + publish) as the terminal leg, but the accepted mitigation was scoped — correctly, per N4 — to *stores*, which the pipeline trigger is not; the Deferred entry gives the impression "reconciliation is the accepted mitigation" for the whole residual when it only covers half of it.

**Close it:** one Deferred line: *"the ingest leg is not reconciled — a lost `video-uploaded` orphans the video in `UPLOADED` (accepted for the lab; the FAILED/reprocessing story is the future retry path). If re-drive is desired, reconciliation may re-enqueue `video-uploaded` as transport, never as a store write."*

---

## Verdict

Round 2's N1–N9 are all genuinely closed in letter — the spine's invariants are now stated and mostly coherent. But the fixes opened one **flat contradiction** — **R1**: AD-8's N6 fix mandated metadata validation for search/notification while the AD-8 dependency diagram (untouched by the fix) grants them "SQS events only" and no client to metadata, so two builders reading the same AD build one service with a metadata client and one without, with different trust behavior. That is the same severity class as round-2's N2 (which kept the gate at NO-GO), so the gate stays NO-GO — narrow. Three residuals are letter-level: **R2** (the N1 guarantee needs publisher eventId recovery that AD-2's statelessness forbids and the contract cannot carry), **R3** (N9's "projection of acknowledged payload" vs "exactly one event per transition" leaves same-status re-assertion emission ungoverned), **R4** (N4's scoping silently leaves the ingest leg unreconcilable). All four findings close with one-line amendments; there is no structural flaw left. Re-review after R1–R3 land.

### Priority fix list

1. (R1, HIGH) AD-8 diagram + AD-6: grant search/notification a `channel.metadata` gRPC client (GetVideo/ListVideos) and restate "SQS events only".
2. (R2, MEDIUM) AD-4/AD-2: define where the publisher's terminal `eventId` lives across restarts (deterministic derivation recommended) — closes N1's restart hole and the N2 outbox eventId gap together.
3. (R3, MEDIUM) AD-3: same-status re-assertion applies fields, emits no event.
4. (R4, LOW) Deferred: acknowledge the UPLOADED orphan (or allow reconciliation to re-enqueue transport).
