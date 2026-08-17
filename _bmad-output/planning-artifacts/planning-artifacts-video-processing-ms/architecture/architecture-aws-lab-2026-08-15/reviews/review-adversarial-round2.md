---
title: Adversarial architecture review — round 2 (re-check of fixed spine)
substrate: ARCHITECTURE-SPINE.md (2026-08-15 revision)
reviewer: adversarial reviewer (one level down)
date: 2026-08-15
gate: architecture spine gate
round1: NO-GO (review-adversarial.md, findings F1-F9)
verdict: NO-GO — all nine round-1 findings are closed in letter, but the fixes opened three new high-severity seams (republish-with-new-eventId defeats the "exactly one event" invariant; the outbox carve-out contradicts AD-8's publisher allow-list; the honours-or-generates video_id rule reinstates a second minting path and leaves CreateVideo retry semantics ungoverned). None are structural; each closes with a one-line AD amendment.
---

# Adversarial Review Round 2 — video-processing platform spine

**Method (unchanged).** For every invariant I construct two units one level down — two builders implementing the same slice, each required to obey **every** AD literally. Where both units can pass every AD yet build mutually incompatible services, the spine has a hole. Each finding gives the two units, the divergence, and the concrete AD/Deferred change that closes it.

**Contract amendments verified against** `spec-video-processing-microservices.md`: `CreateVideoRequest` carries optional `video_id`; `VideoInfo` carries `failure_reason`; both event DTOs carry `eventId` + `schemaVersion`. These match the spine text.

---

## Part A — Round-1 findings (F1–F9): closure verification

| # | Finding | Round-1 verdict | Round-2 status | Evidence in current spine |
| --- | --- | --- | --- | --- |
| F1 | Dual-channel write ordering + outbox vs AD-2 | CRITICAL | **CLOSED** | AD-3 "Status-first ordering" (status write acknowledged before publish); AD-2 "Outbox carve-out" (lives in metadata's DB, written via gRPC); Deferred "Dual-channel consistency" names reconciliation with a "write only to owned stores" constraint. Residual interaction is N2/N4 below. |
| F2 | Unassigned PROCESSING producer; terminal-event silence on FAILED | CRITICAL | **CLOSED** | AD-3 "Producer assignment" (UPLOADED only via CreateVideo, PROCESSING only by processing-service, PROCESSED\|FAILED only by processing-service; upload never emits PROCESSING) + "Terminal-event emission" (PROCESSED and FAILED both emit exactly one event on `video-processed`, `failureReason` iff FAILED; notification notifies on both, search excludes FAILED). Residual: "exactly one event" is only true at the logical-publish level — N1. |
| F3 | No authoritative shape; `failureReason` event-only | HIGH | **CLOSED** | Consistency "Data & formats": `VideoInfo` is canonical for shared fields; entity now has `failureReason`; AD-5 extends additive-only to event DTOs. |
| F4 | Idempotency keyed on videoId; no eventId; no dedupe window | HIGH | **CLOSED** | AD-4: `eventId` per publish, idempotency keyed on `eventId`, `videoId` = domain key; search natural key = videoId, notification history = eventId (append); Deferred "Dedupe retention". Residual: republish-with-new-eventId — N1. |
| F5 | Versioning incoherent for wire/coexistence; events unversioned | HIGH | **CLOSED** | AD-5: field numbers/names immutable, additive-only; server MUST expose both versions during coexistence; JSON events carry `schemaVersion`, consumers reject unknown versions. Residual: no event-schema cutover mechanism — N7. |
| F6 | videoId minting split; `CreateVideo` couldn't carry ingress id | HIGH | **CLOSED** | AD-2 Identity: minted exactly once at ingress, supplied via `CreateVideoRequest.video_id`, honours valid UUID, generates if absent, rejects duplicate with `ALREADY_EXISTS`. Residual: the "generates if absent" clause reinstates a second minting path — N3. |
| F7 | No event trust model / publisher allow-list | HIGH | **CLOSED** | AD-8: events are owner-confirmed projections; status-first before publish; only upload publishes `video-uploaded`, only processing publishes `video-processed`; metadata is source of truth; un-findable videoId = poison (drop + log). Residuals: poison is NOT_FOUND-vs-UNAVAILABLE unqualified and the consumer set is unspecified — N5/N6. |
| F8 | AD-7 mapping unspecified; SSE errors ungoverned | MEDIUM | **CLOSED** | AD-7 table (`INVALID_ARGUMENT`→400, `NOT_FOUND`→404, `ALREADY_EXISTS`→409, `FAILED_PRECONDITION`→409, `INTERNAL`→500) + SSE `event: error` with the same body. Residual: table not exhaustive — N8. |
| F9 | Channel naming, profile-scoped path-style, port table not binding, logical vs physical names | LOW | **CLOSED** | AD-6 pins `channel.metadata`/`channel.search`; `path-style-access-enabled`, region, endpoints are profile-scoped (default ministack); port table is normative; logical names = contract (`Names`), physical = config. |

No round-1 finding survives. All nine are now closed at the letter level. The new seams below are opened **by the fixes themselves** — each is a residual of how a round-1 fix was worded.

---

## Part B — New findings (N1–N9)

### N1 — HIGH — "Exact one event" (AD-3) is defeated by "eventId per publish" (AD-4): a republish with a new eventId duplicates derived state

**ADs attacked:** AD-3 ("every terminal transition emits **exactly one** event"), AD-4 ("`eventId` … publisher-generated **per publish**"), Deferred reconciliation.

**Two units (processing-service):**

- **Unit A:** `eventId` is generated at each **publish attempt** (AD-4's literal "per publish"). Terminal publish #1: SQS accepted it, but the client got a timeout/error, so the SDK state is unknown. A retries → **new** `eventId`. Both publishes are on the queue (SQS at-least-once). notification-service appends **both** (history natural key = `eventId`, append per unique event). Client receives **two** `PROCESSED` SSE frames. Search upserts twice (harmless, videoId key). AD-3's "exactly one event" is false on the wire.
- **Unit B:** mints the `eventId` **once per logical terminal transition** and reuses it on every publish retry (idempotent publish). Redelivery of the same `eventId` is deduped; the client receives one frame.

Both obey AD-3 and AD-4 to the letter — A under the plain reading of "per publish", B under "exactly one event". Divergence: **duplicate derived rows / duplicate notifications vs single.** The Deferred reconciliation only fills "owner advanced, event missing"; it does not dedupe "one event, two eventIds" (see N4 for why it cannot, cleanly).

**Close it:** AD-4: *"`eventId` is minted by the publisher **once per logical event** (i.e. per transition), and **reused across publish retries** — a retry is an idempotent republish of the same `eventId`, never a new one. 'Per publish' means per logical event, not per attempt."* Deferred reconciliation: *"also dedupes duplicate history rows (republish of one logical event under two eventIds), not only fills gaps."*

---

### N2 — HIGH — The outbox carve-out (AD-2) contradicts AD-8's publisher allow-list: a compliant outbox makes metadata-service the publisher

**ADs attacked:** AD-2 ("Outbox carve-out: it lives in metadata-service's DB and is written through its gRPC API"), AD-8 ("**only** processing-service publishes `video-processed`").

**Two units (metadata-service, outbox adopted):**

- **Unit A:** implements the AD-2 carve-out faithfully: `UpdateVideoStatus(PROCESSED)` writes the outbox row **atomically with the status write** (this is the entire point of the carve-out — atomicity requires same-transaction), and a **relay inside metadata-service** reads the outbox and publishes `video.processed` to SQS. This is the only outbox shape consistent with AD-2's "written through its gRPC API" + "no service reads another service's record store" — processing-service cannot write metadata's outbox table directly.
- **Unit B:** honors AD-8's allow-list literally: metadata never touches SQS; processing-service publishes `video.processed` after the status write (plain status-first, AD-3).

Both are bound by ADs. **A cannot exist without metadata-service publishing `video.processed` — a flat violation of AD-8.** Conversely, B cannot provide the atomicity the carve-out advertises. The two round-1 fixes (outbox behind metadata's gRPC API; allow-list) are **mutually unsatisfiable**. The residual failure mode the outbox was meant to remove (status write succeeds, event publish fails → "owner advanced, event missing") is exactly the case where the allow-list forces the publish to live in the non-atomic stateless worker.

**Close it:** amend AD-8: *"The allow-list governs **who may cause** a publish. The transactional-outbox relay inside metadata-service publishes terminal events the producer enqueued atomically with the status write — metadata is transport on the producer's behalf, **not** a second producer; a relay publish is permitted only via the AD-2 outbox path, and it never invents an event metadata did not commit."*

---

### N3 — HIGH — The honours-or-generates `video_id` rule reinstates a second minting path and leaves CreateVideo retry semantics ungoverned

**AD attacked:** AD-2 Identity ("minted exactly once, at ingress … honours a present valid UUID, **generates one if absent** … rejects a duplicate with `ALREADY_EXISTS`") + the contract's `optional video_id`.

**Two units (upload-service):**

- **Unit A:** always mints at ingress, always sends `video_id`, names the S3 object key with it. On CreateVideo retry after a crash (object already in S3, `ALREADY_EXISTS` returned), A **reuses the same videoId** and treats the duplicate as idempotent success.
- **Unit B:** reads "optional `video_id` … generates one if absent" as license to defer minting: it does **not** send `video_id`, metadata mints `id2`. B's S3 object was put **before** CreateVideo, so it cannot be keyed by `id2` — B either (a) violates AD-2's "S3 keys … must all use the same `videoId`", or (b) does a post-create copy/rename (extra op, racing processing-service which reads by the record's key). On a retry that hits `ALREADY_EXISTS`, B surfaces 409 to the client and the S3 object is orphaned; if B instead **re-mints** per attempt, the S3 key (old id) and the record (new id) diverge and processing reads by the record's key and misses the object.

Both obey AD-2's letter — the "generates one if absent" clause actively licenses B's second minting path, contradicting the same AD's "minted exactly once, at ingress". Divergence: **one minting authority + stable keys vs a second minting authority + key/record divergence (data corruption).** AD-2 also never says what `ALREADY_EXISTS` means **to the consumer** (idempotent success vs error), which is exactly the upload-retry path.

**Close it:** AD-2: *"`video_id` is REQUIRED from upload-service — the sole minting point; metadata's 'generate if absent' is a defensive fallback only, never a normal path, and any such generated id must be returned to upload-service which then rekeys the object before publishing. upload-service MUST persist its minted `videoId` and reuse it across retries; a CreateVideo `ALREADY_EXISTS` for upload's own ingress-minted id is an idempotent success (record already exists), not a client error."*

---

### N4 — MEDIUM — Reconciliation (Deferred) cannot correct notification history: the eventId-keyed append store has no reconciliation write key

**ADs attacked:** Deferred "Dual-channel consistency" ("corrects derived stores"), AD-4 (notification history natural key = `eventId`, append).

**Two units (notification-service + reconciliation):**

- **Unit A:** reconciliation corrects **both** derived stores. For search (videoId key) an upsert works. For notification history it must synthesize an `eventId` and insert a row from metadata state — a notification that was **never published**, whose fabricated `eventId` duplicates the real one if the delayed event later arrives.
- **Unit B:** reconciliation corrects only search; notification history keeps the gap ("owner advanced, event missing" accepted for SSE).

Both are "reconcile, writing only to owned stores" compliant. Divergence: **fabricated/duplicated notifications vs silent notification loss.** The Deferred text says reconciliation "corrects derived stores" without noting that AD-4's own natural-key choice (append, `eventId`) makes history **unreconcilable** from metadata — metadata has no `eventId`, no publish time. When the terminal event was never published, "notification history from retained events" (Deferred) is empty by definition.

**Close it:** Deferred: *"reconciliation corrects only stores whose natural key is metadata-derivable (search index by videoId). Notification history is append-only, keyed on eventId, and is NOT reconciliation-corrected — a missing notification is accepted; SSE is a best-effort channel. Alternatively change history's natural key to `videoId` (last-wins) so reconciliation can upsert it."*

---

### N5 — MEDIUM — Queue→consumer subscription is only in a non-normative mermaid diagram: search may legally index `video.uploaded`

**ADs attacked:** AD-4 (binds search/notification as consumers, without limiting which queues), the structural-seed diagram (the only place `Q1→P`, `Q2→N,S` appears).

**Two units (search-service):**

- **Unit A:** subscribes to `video-processed` only (per the seed diagram). Index = terminal PROCESSED videos (FAILED excluded).
- **Unit B:** also subscribes to `video-uploaded` for early indexing. Index = every non-FAILED video from UPLOADED onward. Nothing in AD-2/4/8 forbids this: AD-8's trust model is satisfied (upload completes CreateVideo before publishing), AD-2 gives search the index, "search excludes FAILED" is the only status rule and both satisfy it.

Divergence: **index population and search results differ** — A returns only completed videos; B returns in-flight videos too (title-substring search over UPLOADED videos). The spine binds the queue topology only in a mermaid seed, which is illustrative, not an AD.

**Close it:** AD-4: *"queue subscription is normative — processing-service subscribes only to `video-uploaded`; search-service and notification-service subscribe only to `video-processed`. A video becomes searchable only when its terminal PROCESSED event is consumed."* (Folds in the user's poison-vs-early-index question: the conflict cannot arise once Q2-only is an AD; before that, B's early indexing is fully AD-compliant.)

---

### N6 — MEDIUM — The poison rule conflates NOT_FOUND with transient unavailability, and doesn't say which consumers must validate

**AD attacked:** AD-8 ("a consumer that cannot find the videoId in metadata treats the event as poison — drop and log").

**Two units (search-service):**

- **Unit A:** treats **both** `NOT_FOUND` and `UNAVAILABLE` (metadata briefly down) as "cannot find" → drop + log. A transient metadata blip permanently loses a legitimate terminal event (reconciliation may or may not repair, per N4).
- **Unit B:** retries `UNAVAILABLE` with backoff; only a successful negative lookup (`NOT_FOUND`) is poison.

Both "treat un-findable events as poison". Divergence: **data loss under transient outage vs recovery.** Relatedly, AD-4 names processing-service a consumer and gives it a no-op rule that needs **no** metadata lookup, while AD-8's validation duty is unqualified — does processing-service also validate every `video.uploaded` via GetVideo (a sync round-trip per event), or is validation a derived-store duty only? Unstated.

**Close it:** AD-8: *"poison means a successful negative lookup — metadata returns `NOT_FOUND` for a videoId the consumer has no evidence the owner wrote; `UNAVAILABLE`/deadline errors are transient and retried, never dropped. The metadata validation duty applies to derived-store consumers (search, notification) on terminal events; processing-service's duplicate no-op is status-based and does not perform a metadata lookup."*

---

### N7 — MEDIUM — AD-5 versioning is asymmetric: proto has a coexistence rule, JSON events have only rejection, no cutover

**AD attacked:** AD-5 ("during coexistence a gRPC server MUST expose both versions … JSON events carry `schemaVersion`; consumers reject unknown versions rather than misparse").

**Two units (video-common + processing-service):**

- **Unit A:** ships a breaking event change as `schemaVersion=2`. Per AD-5, a v1 processing-service **rejects** v2 `video.uploaded` → those videos never process. AD-5 gives no bridge or cutover signal for events (the proto path at least mandates dual-expose on the server until migration).
- **Unit B:** never bumps `schemaVersion`; treats event evolution as JSON-additive only (unknown fields preserved) and reserves bumps for coordinated multi-service deploys.

Divergence: **A opens a silent pipeline-stopping drop window; B sidesteps the question.** The spine's versioning rule is coherent for gRPC (dual-expose) but for events it stops at "reject", which is safe-but-brutal and lacks a defined migration procedure.

**Close it:** AD-5: *"an event `schemaVersion` bump is a breaking change requiring coordinated cutover — all consumers deploy before the publisher switches, verified by a named ops signal (e.g. a rollout doc or a dual-publish window); rejection of an unknown version is the failure mode, not the plan. State the cutover mechanism per event-family."*

---

### N8 — LOW — AD-7's status table is not exhaustive; the SSE error shape covers only the error frame

**AD attacked:** AD-7.

**Two units (upload-service / search-service HTTP facades):**

- **Unit A:** maps `UNIMPLEMENTED`→501, `UNAVAILABLE`→503.
- **Unit B:** maps everything not in the table→500.

Both obey the table for the five listed statuses. Divergence only for statuses the table omits — and AD-5's coexistence phase can transiently surface `UNIMPLEMENTED`. SSE is governed only for the error/close frame; `notification-service` is not bound as an "HTTP facade", so its other client-visible frames are ungoverned (acceptable today since delivery is Deferred, but the bind list is inconsistent).

**Close it:** AD-7: add *"any gRPC status not listed maps to 500"* and either drop "HTTP facades" from AD-7's binds in favor of "all client-facing surfaces" or add a Deferred line noting SSE non-error frames are ungoverned until real delivery channels exist.

---

### N9 — LOW — "Same-status re-assertion is idempotent" (AD-3) is undefined for same-status-with-new-payload

**AD attacked:** AD-3.

**Two units (metadata-service):**

- **Unit A:** a re-asserted `PROCESSED` (e.g. transcode retry re-completing) is a full no-op — keeps the old `processedKey`/`durationSeconds`.
- **Unit B:** applies the request payload (new `processedKey`/`durationSeconds`) while status remains PROCESSED — matches the spec's `UpdateVideoStatus` row ("`VideoInfo` with new status, `processed_key`, `duration_seconds`, `failure_reason`").

Both are "same-status idempotent" for the **transition**; they disagree on **field application**. Observable: the record's `processedKey` differs; the event mirrors the acknowledged store, so derived stores inherit the divergence.

**Close it:** AD-3: *"same-status re-assertion is idempotent for the state transition; request-carried fields are still applied (overwritten), because the published event is a projection of the acknowledged payload."*

---

## Answers to the four probing questions

1. **Does honours-or-generates create ambiguity?** Yes — N3. The clause licenses a second minting authority inside an AD that claims one, and it breaks the S3-key rule for any builder that uses it as a normal path.
2. **Does eventId-per-publish survive a retry that republishes with a new eventId?** No — N1. Idempotency survives SQS redelivery of the *same* publish, but not a publisher-level retry that mints a fresh `eventId`; AD-3's "exactly one event" holds only if the publisher reuses the `eventId`.
3. **Does poison conflict with search indexing upload events before CreateVideo completes?** Only if search subscribes to `video-uploaded` — which the spine currently permits (N5). Under the intended Q2-only binding, status-first (AD-3/AD-8) guarantees the record exists, so poison never legitimately fires; the real defects are the missing normative queue-binding (N5) and NOT_FOUND-vs-UNAVAILABLE conflation (N6).
4. **Do status-first and the outbox carve-out contradict anything?** Status-first and outbox are compatible with each other; both contradict AD-8's allow-list when the carve-out is actually implemented (N2).

---

## Verdict

Round 1's F1–F9 are all genuinely closed — the spine's headline seams (ordering, producers, contract authority, identity, trust, versioning) are now stated. But the fixes did not just close holes; they added seams: **N1** breaks the "exactly one event" invariant under a realistic republish, **N2** is a flat contradiction between two round-1 fixes (outbox behind metadata's gRPC API vs the publisher allow-list), and **N3** reinstates a second videoId minting path inside the very AD that bans it, with ungoverned CreateVideo retry semantics. All three are narrow, letter-level amendments away from closure; there is no structural flaw left. Re-review after N1–N3 (and ideally N4–N7) land.

### Priority fix list

1. (N1) AD-4: `eventId` minted once per logical event, reused across publish retries.
2. (N2) AD-8: outbox relay = transport on the producer's behalf, not a second producer.
3. (N3) AD-2: `video_id` required from upload-service; "generate if absent" defensive-only + rekey duty; `ALREADY_EXISTS` on one's own ingress id = idempotent success.
4. (N4) Deferred: reconciliation corrects only videoId-keyed stores (search), not eventId-keyed append history.
5. (N5) AD-4: normative queue subscriptions (processing→uploaded; search/notification→processed only).
6. (N6) AD-8: poison = NOT_FOUND only; UNAVAILABLE retried; validation is a derived-store duty.
7. (N7) AD-5: event-schema cutover procedure, not just rejection.
