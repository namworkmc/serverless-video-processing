---
title: Adversarial architecture review — round 4 (final gate decision)
substrate: ARCHITECTURE-SPINE.md (2026-08-15 revision, R1–R4 applied)
reviewer: adversarial reviewer (one level down)
date: 2026-08-15
gate: architecture spine gate
round1: NO-GO (F1–F9, review-adversarial.md)
round2: NO-GO (N1–N9, review-adversarial-round2.md)
round3: NO-GO narrow (R1–R4, review-adversarial-round3.md)
verdict: PASS — R1–R4 are genuinely closed in letter; five residual seams remain, all one-line amendments, none a flat contradiction, none blocking happy-path integration. Recommend applying G1–G3 before the five services are independently built; G4–G5 are latent and can ride the Deferred log.
---

# Adversarial Review Round 4 — video-processing platform spine (final gate)

**Method (unchanged).** For every invariant I construct two units one level down — two builders implementing the same slice, each required to obey **every** AD (and the bound contract in `spec-video-processing-microservices.md`) literally. Where both units can pass every AD yet build mutually incompatible services, the spine has a hole. Each finding states the two units, the divergence, and the concrete change that closes it. Round-4 scope: (a) verify R1–R4 are actually closed, (b) hunt for seams the round-3 fixes opened, (c) stay proportionate — operational/deferred concerns (CI/CD, observability, DLQ, delivery channels, reprocessing, real AWS) are not re-raised.

**Contract facts verified against the frozen spec (this review's ground truth):** `CreateVideoRequest.video_id` required (sole minting point, generate-if-absent defensive only); `UpdateVideoStatusRequest` carries `processedKey`/`durationSeconds`/`failureReason` and **no** `event_id`; `VideoInfo` carries `failure_reason`; `VideoMetadataService` exposes `CreateVideo`/`UpdateVideoStatus`/`GetVideo`/`ListVideos` (all statuses, unfiltered), `VideoSearchService` exposes `SearchVideos`; `VideoUploadedEvent.eventId` = deterministic name-based UUID from `(videoId, "UPLOADED")`; `VideoProcessedEvent.eventId` = deterministic from `(videoId, status)`. These match the spine text.

---

## Part A — Round-3 findings (R1–R4): closure verification

| # | Finding | Round-3 verdict | Round-4 status | Evidence in current spine + contract | Residual |
| --- | --- | --- | --- | --- | --- |
| R1 | AD-8 mandates metadata validation for search/notification but the AD-8 dependency diagram grants them "SQS events only" and no client | HIGH | **CLOSED** | Diagram now has `S --> gRPC client GetVideo/ListVideos --> M` and `N --> gRPC client GetVideo --> M`, annotations read "consumes SQS events; validates via metadata gRPC"; prose "search-service/notification-service depend on metadata-service for event validation (AD-8) and search rebuild (ListVideos)"; AD-6 names `channel.metadata` for search/notification validation/rebuild reads. Spec has both `GetVideo` and `ListVideos`. | **G1** — the fix made the ListVideos rebuild a first-class normative dependency without binding it to the terminal-only index rule that N5 imposed on the consume path. |
| R2 | Deterministic "mint once, reuse across retries" unimplementable by a stateless publisher; outbox relay has no eventId source | MEDIUM | **CLOSED** | AD-4: "`eventId` is **deterministic** — `UUID.nameUUIDFromBytes((videoId + ":" + status).getBytes())` — so it is stateless and restart-proof: redelivery and publish retry automatically reuse the same id". Contract DTOs specify the same derivations for both event families (upload's from `(videoId,"UPLOADED")`). Outbox relay computes the same value — no contract change needed; option (a) from round 3 was taken. | **G4** — the formula omits `schemaVersion`, which collides with AD-5's dual-publish cutover option (latent, not today). |
| R3 | Same-status re-assertion emission ungoverned ("projection of acknowledged payload" vs "exactly one event per transition") | MEDIUM | **CLOSED** | AD-3: "A same-status re-assertion applies request-carried fields to the store **only** and emits **no event** — the event for a transition is emitted once, with the payload acknowledged at the transition; a derived store may therefore lag the record after a re-assertion (accepted)." | none |
| R4 | N4's scoping silently leaves the ingest leg (lost `video-uploaded` → orphan) with no mitigation | LOW | **CLOSED** | Deferred "Ingest-leg orphan": "a lost `video-uploaded` orphans the video in `UPLOADED` (accepted for the lab…). If re-drive is desired later, reconciliation may re-enqueue `video-uploaded` as transport — never as a store write." | **G5** — the re-enqueue option opens a third `video-uploaded` publisher against AD-8's "Only upload-service publishes" (latent, Deferred-on-Deferred). |

**Closure summary.** No round-3 finding survives. All four are closed at the letter level and match the frozen contract. The five seams below are residuals of how the round-3 fixes were worded — the R1 fix elevated the rebuild channel (G1), the R2 fix's formula interacts with versioning (G4), the R4 fix sanctioned a third publisher (G5), and two long-dormant promise-vs-statelessness gaps became visible now that the surrounding clauses are pinned (G2, G3).

---

## Part B — New findings (G1–G5)

### G1 — MEDIUM — R1 made the `ListVideos` rebuild a first-class search channel but left its index filter ungoverned: N5's terminal-only rule governs only the consume path, so a compliant rebuild re-opens N5's in-flight-video divergence

**ADs attacked:** AD-8 (diagram: `S --> gRPC client ListVideos --> M`; Deferred "Derived-store recovery: search index rebuildable from metadata-service.ListVideos") vs AD-3 ("search excludes FAILED") and AD-4 ("A video becomes searchable only when its terminal PROCESSED event is consumed"). The consume-path rule is written as an event-consumption fact; the rebuild path is a second, R1-sanctioned population mechanism with no filter rule of its own.

**Two units (search-service):**

- **Unit A:** rebuilds from `ListVideos` by indexing only `status=PROCESSED` (generalizes the consume-path invariant "searchable only when terminal PROCESSED" to the rebuild).
- **Unit B:** rebuilds from `ListVideos` by indexing everything that is **not FAILED** — the only content rule the spine states is AD-3's "search excludes FAILED", and `ListVideos` returns all statuses (spec: no status filter).

Both obey every AD to the letter. Divergence: **after any rebuild, A exposes only completed videos; B also exposes UPLOADED and PROCESSING videos** — the exact "search returns in-flight videos" divergence N5 closed for the consume path (subscription binding), reopened on the rebuild path by R1's fix, which elevated `ListVideos` to a normative dependency without binding it to the terminal-only rule. Today-reachable: the rebuild is a stated recovery duty and R1 makes it a first-class channel; two builders writing the rebuild code today write different filters.

**Close it:** one AD-3/AD-8 sentence — *"search indexes only `status=PROCESSED`, on the consume path (terminal `video-processed` events) and on the `ListVideos` rebuild path alike."*

---

### G2 — MEDIUM — The AD-4 duplicate no-op is only executable through the transition-rejection channel, and the producer-side handling of `FAILED_PRECONDITION` is ungoverned anywhere

**ADs attacked:** AD-4 ("`processing-service` treats a duplicate `video.uploaded` for an already-processing/terminal video as a no-op"), AD-8 ("processing-service's duplicate no-op is status-based and **performs no metadata lookup**"), AD-3 (regression / transition out of a terminal state → `FAILED_PRECONDITION`), AD-7 (the table maps gRPC→HTTP for facades; it does not govern how a gRPC **client** handles a status).

**Two units (processing-service) on the at-least-once redelivery path** (crash between terminal publish and ack of `video-uploaded` → redelivered `video-uploaded`, fresh process, empty memory, record already terminal):

- **Unit A:** calls `UpdateVideoStatus(PROCESSING)` → metadata rejects with `FAILED_PRECONDITION` → reads this as the AD-4 "already terminal, no-op" signal → acks the message and stops.
- **Unit B:** receives the same `FAILED_PRECONDITION` → treats it as an unexpected error → does not ack (SQS redelivery loop, or an error policy that drops a message AD-4 says must be a no-op), or re-transcodes and re-asserts.

Both obey AD-3 (terminal is terminal), AD-4 ("duplicate … is a no-op"), AD-7 (table governs HTTP mapping only), AD-8 (poison = `NOT_FOUND`, retry = `UNAVAILABLE` — neither covers `FAILED_PRECONDITION` on the `UpdateVideoStatus` side). Divergence: **clean no-op vs redelivery/poison loop**, on exactly the redelivery path AD-4 exists to cover. Root cause: AD-8 forbids processing's metadata lookup, so the only sanctioned way to *detect* "already terminal" is the `FAILED_PRECONDITION` response — and how the producer must treat that response is stated nowhere. This crystallized as the R-rounds pinned down the surrounding clauses (status-first, statelessness, no-lookup); it is today-hittable and is a state-machine ownership ambiguity, not an ops concern.

**Close it:** one AD-4/AD-8 sentence — *"`processing-service` treats `FAILED_PRECONDITION` on its own `UpdateVideoStatus` attempt for a redelivered `video-uploaded` as the AD-4 no-op signal: ack the message and take no further action (no metadata lookup, no retry, no re-transcode)."*

---

### G3 — MEDIUM — AD-2's "CreateVideo retry with one's own ingress-minted id is an idempotent success" is unexercisable: the id never crosses the HTTP boundary and upload-service is stateless, so the promise is vacuous and `ALREADY_EXISTS` is near-dead code

**ADs attacked:** AD-2 Identity ("minted exactly once, at ingress … A CreateVideo retry with one's own ingress-minted id is an **idempotent success**; `ALREADY_EXISTS` is a collision guard") vs AD-2 ("`upload-service` … is a **stateless worker**"). Round-2's N3 close recommended *"upload-service MUST persist its minted videoId and reuse it across retries"* — that persistence clause was **not** carried into AD-2, and AD-2's statelessness bans it anyway.

**Two units (upload-service) on a client retry after a crash/restart** (upload mints id, puts S3 object, CreateVideo succeeds, process dies before the HTTP response; client re-POSTs the same multipart — the request carries no id):

- **Unit A:** keeps a transient in-process map (request → minted videoId) and reuses it, exercising the promised idempotent-success path — but only within a process lifetime; a restart breaks it.
- **Unit B:** mints a fresh UUID per request. CreateVideo succeeds again → **two records, two S3 objects, two `video-uploaded` events** for one logical upload. `ALREADY_EXISTS` never fires (a fresh random UUID never collides).

Both obey AD-2's letter: "minted exactly once, at ingress" holds per request; "idempotent success with one's own ingress-minted id" is true but *unexercisable* because no actor can supply the id (the client never sees it; upload can't remember it). Divergence: **one video vs a silent duplicate** on the client-retry path, and the spine's own idempotency promise is satisfiable by no implementation. Today-reachable: any retry of the upload endpoint.

**Close it:** one AD-2 line, pick one: **(a)** accept the scope — *"upload-idempotency holds only within a process lifetime; a client retry after upload-service restart is a new ingress (new videoId), and duplicate videos from such a retry are accepted for the lab"*; or **(b)** *"upload-service derives videoId deterministically from a client-supplied idempotency key, making 'minted exactly once' hold across restarts."* Recommend (a) for the lab.

---

### G4 — LOW — R2's deterministic eventId formula omits `schemaVersion`, which collides with AD-5's dual-publish cutover option

**ADs attacked:** AD-4 (`UUID.nameUUIDFromBytes((videoId + ":" + status).getBytes())` — the R2 fix) vs AD-5 ("verified by a named ops signal **or a dual-publish window**").

**Two units (video-common + notification-service) at a future breaking cutover:**

- **Unit A:** dual-publishes v1 + v2 of the same terminal transition. Both carry the same eventId (the formula ignores `schemaVersion`). notification's append-by-eventId dedupes the second — the retained row's schemaVersion is arrival-order nondeterministic.
- **Unit B:** derives eventId including `schemaVersion` — then dual-publish produces two distinct eventIds and notification appends **two** rows for one transition, contradicting AD-3's "exactly one event".

Both obey the letter of the R2 formula / AD-5. No bump is planned today, so this is latent — but the R2 fix created the interaction. The eventId formula should not change; the cutover mechanism must.

**Close it:** one AD-5 line — *"a dual-publish window still emits one eventId per transition — the republish is a dedupe, not a second row; `schemaVersion` is payload, not event identity."*

---

### G5 — LOW — R4's "reconciliation may re-enqueue `video-uploaded` as transport" sanctions a third `video-uploaded` publisher that AD-8's "Only upload-service publishes" does not carve out

**ADs attacked:** AD-8 ("**Only** `upload-service` publishes `video-uploaded`") vs Deferred R4 ("reconciliation may re-enqueue `video-uploaded` as transport — never as a store write"). The N2 carve-out explicitly authorises the outbox relay ("permitted only via the AD-2 outbox path"); the R4 reconciler is a distinct third publisher with no stated sanction.

**Two units (ops/reconciliation, when re-drive is later adopted):**

- **Unit A:** re-enqueues `video-uploaded` per the R4 text (transport-on-behalf, N2-style reasoning).
- **Unit B:** reads AD-8's "Only upload-service publishes" as absolute (the only sanctioned exception is the outbox path) and refuses to publish.

Both obey a clause of the same AD pair. Divergence: **pipeline re-drive vs permanent UPLOADED orphan**. Deferred-on-Deferred (re-drive is "later"), hence LOW — but it is a genuine letter-level conflict the R4 fix opened and costs one line to close.

**Close it:** extend AD-8's transport principle — *"re-enqueue by a reconciliation job is likewise transport on the producer's behalf, permitted only via the R4 path and never a store write."*

---

## Verdict

**PASS.** R1–R4 are genuinely closed in letter and match the frozen contract. No flat AD-vs-AD contradiction remains (the class that held this gate at NO-GO in rounds 2 and 3 is gone), and no finding breaks happy-path integration or the shared gRPC/event contract — each of G1–G5 is a one-line amendment to a single AD or Deferred entry. **G1–G3** are real today-hittable divergences between two fully compliant builders (search index content after a rebuild; `FAILED_PRECONDITION` handling on the at-least-once path; upload retry idempotency) and should be applied before the five services are independently built. **G4–G5** are latent (no version bump, no re-drive) and can ride the Deferred log. The state machine, event DTOs, and gRPC contract are now coherent enough to build against.

### Priority fix list

1. (G1, MEDIUM) AD-3/AD-8: search indexes only `status=PROCESSED`, on the consume path and the `ListVideos` rebuild path alike.
2. (G2, MEDIUM) AD-4/AD-8: `FAILED_PRECONDITION` on processing's own transition attempt = AD-4 no-op signal (ack, no lookup, no retry).
3. (G3, MEDIUM) AD-2: state explicitly that upload-idempotency holds within a process only (or make videoId derivable from an idempotency key).
4. (G4, LOW) AD-5: dual-publish emits one eventId per transition; `schemaVersion` is payload, not identity.
5. (G5, LOW) AD-8: reconciliation re-enqueue = transport on the producer's behalf, never a store write.
