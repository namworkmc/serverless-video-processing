---
title: Adversarial architecture review
substrate: ARCHITECTURE-SPINE.md
reviewer: adversarial reviewer (one level down)
date: 2026-08-15
gate: architecture spine gate
verdict: NO-GO — spine is internally consistent at headline level but leaves 4 shared-contract holes through which two fully AD-compliant builders will build incompatible services
---

# Adversarial Review — video-processing platform spine

**Method.** For every invariant (AD-1..AD-7, consistency conventions, Deferred) I construct two units one level down — two builders implementing the same slice of the platform. Each unit is required to obey **every** AD literally. Where the two units can both pass an AD yet produce mutually incompatible services, that AD has a hole. Each finding states the two units, their divergence, and the concrete AD/Deferred change that closes it.

**Confirmed as closed.** AD-1 (layered per service) is unambiguous; AD-5's `java_package` version-scoping decision (memlog note) is already reflected in the current contract. No finding against AD-1.

---

## FINDING 1 — CRITICAL — The deferred outbox mitigation contradicts AD-2, and the dual-channel write has no ordering rule at all

**ADs attacked:** AD-2, Deferred entry ("Transactional outbox").

**Two units:**

- **Unit A (processing-service):** obeys AD-2 literally — "processing-service is a stateless worker; metadata-service has the only DB." To publish `video.processed` reliably it therefore writes metadata *first* (`UpdateVideoStatus(PROCESSED)`), then publishes the event. If the publish fails, the metadata record says PROCESSED and search/notification never see it. No DLQ (deferred), no correction.
- **Unit B (processing-service):** wants the event to be the durable signal, so it publishes `video.processed` *first*, then calls `UpdateVideoStatus(PROCESSED)`. If the gRPC call fails (or races a redelivered `video.uploaded`), search/notification index a PROCESSED video that the owner says is PROCESSING/UPLOADED. Permanent divergence; nothing corrects it.

Both units comply with AD-2, AD-3, AD-4 letter-for-letter. They diverge on **write ordering across the two channels**, and the spine (a) gives no ordering rule and (b) points at the outbox as the fix — but an outbox requires `processing-service` to own a persistent store (the outbox table + relay), which **AD-2 forbids** ("processing-service is a stateless worker", "no shared storage", "metadata-service has the only DB"). The proposed mitigation cannot be implemented without breaking AD-2.

**Close it:**
- New AD (tighten AD-2): **the event-publishing writer is whichever service owns the state it reports.** `processing-service` reports PROCESSED only because it owns the transcode result — the event is a projection of that. Amended rule: an outbox, if ever adopted, lives **in metadata-service's DB and is written through its gRPC API** (`RecordEvent(payload)` or similar), never as a new store inside a stateless worker. That keeps single-writer intact.
- Or accept the failure mode *explicitly*: add a Deferred entry **"Reconciliation"** — a periodic job that reads metadata as truth and corrects derived stores — with an explicit trigger condition (currently the memlog says "revisit if divergence is observed", which is fine as a condition, but the mechanism must be specified as writing to owned stores only).
- Concrete AD-3/event-ordering rule: **`UpdateVideoStatus` must be called before the corresponding event is published** (status-first ordering), and the terminal event must not be published until metadata acknowledges the terminal transition. This makes the happy path deterministic; the failure mode is then only "owner advanced, event missing" and is covered by reconciliation.

---

## FINDING 2 — CRITICAL — AD-3 underspecifies the state machine: two producers of PROCESSING, and terminal-event semantics are silent on FAILED

**AD attacked:** AD-3.

**Two units:**

- **Unit A (upload-service):** on S3 put completion, calls `UpdateVideoStatus(PROCESSING)` immediately (AD-3's bind list literally names **upload-service/processing-service as producers** of the state machine), treating PROCESSING as "uploaded and queued." Then publishes `video.uploaded`.
- **Unit B (processing-service):** is the only service that emits PROCESSING, and does so only when a worker actually picks up the transcode job.

Both obey AD-3's letter — no regression, terminal respected, same-status idempotent. But **who observes the record sees different pipeline positions**: A's PROCESSING arrives before `video.uploaded` is on the queue (a redelivered `video.uploaded` is then a no-op per AD-4, because status is already PROCESSING); B's PROCESSING arrives seconds-to-minutes later. Any polling or "PROCESSING means ready to read from S3" logic in processing-service breaks differently under A vs B. AD-3 assigns **transitions** but never **transition responsibility**.

Second half of the hole — terminal events. `VideoProcessedEvent` carries a `status` field and a `failureReason`, so its author intended it to carry FAILED. AD-3's "PROCESSED | FAILED" transition is silent on whether FAILED produces an event, on which queue, and with what payload.

- **Unit A (notification/search):** `video.processed` is published only for `status=PROCESSED`; FAILED emits nothing (terminal, dead-end). SSE never tells clients a video failed; search never drops it.
- **Unit B (notification/search):** `video.processed` is the generic terminal event carrying `status=PROCESSED|FAILED`; SSE shows failure banners; search removes FAILED from the index.

Both are AD-4-compliant (upsert keyed on videoId; nothing forbids or requires delete). Observable behavior diverges completely.

**Close it:**
- Tighten AD-3: **assign each transition to exactly one producer** — `UPLOADED` minted only by `CreateVideo` (metadata on upload's behalf); `PROCESSING` minted **only** by `processing-service` at transcode start; `PROCESSED|FAILED` minted **only** by `processing-service` at completion. Explicitly state upload-service **never** emits PROCESSING.
- Add AD (or extend AD-3): **terminal-event emission rule** — every terminal transition (PROCESSED **and** FAILED) emits exactly one event on the `video-processed` queue; the event's `status` field mirrors the acknowledged store status; `failureReason` is populated iff FAILED. State what derived stores do with FAILED (notification = notify; search = remove or exclude) so A and B cannot diverge.

---

## FINDING 3 — HIGH — Event DTOs and gRPC messages overlap with no authoritative shape; `failureReason` exists only on the event

**ADs attacked:** AD-2, AD-3, consistency conventions (Data & formats).

**Two units (notification-service):**

- **Unit A:** builds its SSE notification entirely from the `VideoProcessedEvent` payload — so on FAILED it includes `failureReason`.
- **Unit B:** builds its SSE notification from a `GetVideo` call after consuming the event — `VideoInfo` has **no `failureReason` field**, so the failure message is empty or guessed.

Both obey AD-2 (derived from events, own store, rebuildable), AD-4 (idempotent upsert), AD-7 (same error envelope). The payloads they emit to clients differ in shape and content because the **same logical field (`status`, `processedKey`, `durationSeconds`, `failureReason`) lives in two contracts that may drift** — the spine never says which shape is canonical. `VideoUploadedEvent` carries `videoId`/`title`/`contentType`/`sizeBytes` that also exist in `VideoInfo`; nothing says the event is a projection of the store vs. an independent statement.

**Close it:**
- New AD (or extend AD-2): **"The gRPC `VideoInfo` is the canonical shape for any field present in both the proto message and an event DTO; events may carry fields the store does not (e.g. `failureReason`), and consumers MUST source overlapping fields from one declared source."** Pick one: (a) add `failureReason` to `VideoInfo` and make consumers read state from the store, keeping events as pure triggers; or (b) declare events authoritative for notification and stop mirroring status/keys into the store. The spine currently leaves it undecided.
- The event must then carry **`schemaVersion`** (see Finding 5) so a drifted JSON shape is detectable, not silently misparsed.

---

## FINDING 4 — HIGH — AD-4's "idempotent keyed on videoId" is the wrong key for a multi-row derived store, and there is no event id / dedupe window

**AD attacked:** AD-4.

**Two units (notification-service):**

- **Unit A:** history is per-notification rows keyed on `(videoId, status, processedAtEpochMs)` — a video yields 2+ rows (UPLOADED, PROCESSED, FAILED); SSE replay shows the full journey.
- **Unit B:** obeys AD-4's literal text — upsert keyed on **videoId alone** — so the history collapses to one row per video, last-wins; SSE replay shows only the terminal state.

Both are AD-4-compliant ("upsert, never append-blind"). The notification UX diverges. Deeper: `videoId` is a *domain* key, not a *delivery* key. SQS at-least-once redelivers the **same event** with the same `videoId`, and no event DTO carries an event id, so a redelivered `video.processed` is indistinguishable from a distinct event — and there is no stated dedupe window/retention (a restart loses an in-memory dedupe table and replays everything).

**Close it:**
- Extend AD-4: **"Every event DTO carries `eventId` (UUID, publisher-generated per publish). Idempotency is keyed on `eventId`; `videoId` is the domain key for upsert-merge semantics. Each derived store declares its natural key (search index: `videoId`; notification history: `eventId`/`(videoId,status,at)`)."**
- Add Deferred (or AD-4 line): **dedupe store retention ≥ SQS visibility/redelivery window**, and whether dedupe may be in-memory in the lab (ministack) vs durable for real AWS.

---

## FINDING 5 — HIGH — AD-5's "coexistence until consumers migrate" is wire-incoherent: a v2-pinned client calling a v1-pinned server gets UNIMPLEMENTED; events have no versioning at all

**AD attacked:** AD-5.

**Two units:**

- **Unit A (upload-service):** pins `videolab.v2` (a field was added), keeps gRPC target pointing at metadata-service.
- **Unit B (metadata-service):** pins `videolab.v1`, exposing only `videolab.v1.VideoMetadataService` (server "hasn't migrated yet" — coexistence is supposed to allow this).

gRPC routes on the **full method path** `package.Service/Method`. A v2 client calls `videolab.v2.VideoMetadataService/CreateVideo`; the v1 server does not expose that service name → **`UNIMPLEMENTED`**, not a graceful compatibility window. Even if it were reachable, protobuf wire forwards compatibility depends on **field numbers never changing inside a version** — AD-5 says nothing about field-number immutability, so a v2 author can renumber freely and silently corrupt v1 peers. And AD-5 covers **only .proto**: the JSON events live in an unversioned package (`com.videolab.events`) with no schemaVersion, so an event schema break has no coexistence mechanism at all.

**Close it:**
- Tighten AD-5: **field numbers and message names are immutable within a version (additive-only); a renumber/rename/remove is a breaking change → new version.**
- Add migration rule: **during coexistence, a server MUST expose BOTH versions** (spring-grpc serves any `@GrpcService` bean — the migration is "run both impl beans until all consumers migrate, then drop v1"). Coexistence is a server-side duty, not a client-side hope.
- Add AD (or extend AD-5): **events are versioned too** — envelope field `schemaVersion` (or package `com.videolab.events.v1`); consumers reject unknown versions rather than misparse.

---

## FINDING 6 — HIGH — videoId authority is split between spine (ingress-minted) and the actual contract (server-minted), and the current `CreateVideo` request cannot carry an ingress id

**ADs attacked:** AD-2, consistency conventions (identity).

**Two units:**

- **Unit A (upload-service):** follows the spine/memlog — "videoId is a UUID generated at ingress (upload-service)". It mints `id1`, stores the object under a key containing `id1`, and expects to tell metadata-service the id.
- **Unit B (metadata-service):** follows the current contract (story-1 spec, and the proto as written) — `CreateVideo(title, bucket, originalKey, contentType, sizeBytes)` has **no `video_id` field**; metadata-service mints `id2` server-side and returns it.

Both obey every AD. But Unit A has no wire channel to supply `id1` (proto change required, i.e. it is blocked), and if it publishes `video.uploaded` with `id1` while the record is `id2`, the S3 object key and the record disagree — processing-service reads by the record's key and misses. Identity has **two potential minting points and no declared owner**, and the spine's stated rule is unimplementable against the contract it binds.

**Close it:**
- Extend AD-2: **"videoId is minted exactly once, in `upload-service` (ingress), and is supplied to metadata-service via `CreateVideo` (add optional `video_id` to the request; metadata honours a present, valid UUID and rejects a duplicate with `ALREADY_EXISTS`)."** This makes the spine's own rule true and the event/object/record keys agree. If server-side minting is preferred instead, the spine's "generated at ingress" line must be amended — pick one, the current text and contract disagree.

---

## FINDING 7 — MEDIUM — AD-2 never says whether derived stores must validate events against the owner; events are published by non-owners and can carry state the owner never ratified

**AD attacked:** AD-2.

**Two units (search-service):**

- **Unit A:** indexes directly from events (fast path, no owner call) — so it will index a video whose `CreateVideo` never completed (upload-service crashed between S3 put and gRPC create, or processing-service published `video.processed` for a videoId metadata has never seen).
- **Unit B:** validates via `GetVideo` before indexing (rejects phantom events, different latency and failure mode).

Both obey AD-2's literal text — "search index owned by search-service, derived from events, disposable/rebuildable" — but they implement **different trust models** for the same event stream, and the spine names no source that is allowed to publish (`video-uploaded` is published by upload-service, `video-processed` by processing-service — neither is the record owner, and nothing authenticates the publisher).

**Close it:**
- Add AD (or extend AD-4/AD-2): **"event trust + ordering"** — events are projections of owner-confirmed state, not independent assertions; publishers MUST complete the owning write (AD-2 / status-first ordering from Finding 1) before publishing. Optionally: consumers MAY validate via `GetVideo` but must not fail-closed differently than the spine allows — the spine must say which. State the trust model explicitly: **metadata-service is the source of truth for existence and status; a consumer that cannot find the videoId in metadata treats the event as poison (drop + log), not as evidence of creation.**
- Add AD line: **only `upload-service` publishes `video-uploaded`; only `processing-service` publishes `video-processed`** — prevents a derived service accidentally becoming a second producer (the "two owners of one entity" hazard for the search index / notification history).

---

## FINDING 8 — MEDIUM — AD-7's "matching 4xx/5xx" mapping is unspecified and SSE errors have no shape

**AD attacked:** AD-7.

**Two units (upload-service / search-service HTTP facades):**

- **Unit A:** maps `FAILED_PRECONDITION` → HTTP **409 Conflict**.
- **Unit B:** maps `FAILED_PRECONDITION` → HTTP **412 Precondition Failed**.

Both return "matching 4xx with `{"error": ...}`" per AD-7's literal text; a client cannot rely on the status code. AD-7 also binds "HTTP facades" but notification-service's **SSE channel is not an HTTP facade** — its error/termination frames are ungoverned.

**Close it:**
- Extend AD-7 with an **explicit status-code table**: `NOT_FOUND→404`, `INVALID_ARGUMENT→400`, `ALREADY_EXISTS→409`, `FAILED_PRECONDITION→409` (pick one), `INTERNAL→500`; error body `{"error":"<gRPC description>"}`. State that the message is the gRPC status description verbatim.
- Add AD-7 line (or Deferred note): **SSE error/close frames** — define a client-visible shape (e.g. `event: error` with `{"error": ...}`) before real channels land.

---

## FINDING 9 — LOW — AD-6 loose ends: gRPC channel naming, profile-dependent property, code/constants vs config, and the port table is not binding

**AD attacked:** AD-6.

**Two units:**

- **Unit A:** names its gRPC client channel `metadata` (upload-service yml: `spring.grpc.client.channel.metadata.target=localhost:9090`).
- **Unit B:** names it `metadata-svc`. Both are "config not code" compliant; nothing pins channel *names*, only targets. Spring gRPC also needs the `@GrpcClient` name to match — divergent names are harmless here but a free degree of freedom.
- **Unit B (second axis):** copies AD-6's literal `spring.cloud.aws.s3.path-style-access-enabled=true` into its real-AWS profile, where path-style is wrong. AD-6 bakes a ministack-necessity as a universal constant instead of a profile-specific value.
- **Third axis:** AD-6's spirit is "config not code", yet queue/bucket names live in **code** (`com.videolab.common.Names`) — defensible as contract, but the spine doesn't say the *physical* SQS/S3 mapping (URL, account) is config while the *logical* name is contract.
- **Fourth axis:** the fixed port table lives only in prose; AD-6 does not make the table binding on each service's `application.yml`, so a builder can change ports and silently break every gRPC target.

**Close it:**
- Extend AD-6: **pin gRPC channel names** (`channel.metadata` for metadata-service, `channel.search` for search-service) in the consistency conventions table.
- Add AD-6 line: **`path-style-access-enabled`, region, endpoints are profile-scoped config**; the spine states the default profile is ministack and real-AWS profile overrides it — never a universal literal.
- Add AD-6/consistency line: **the port table is normative** — every service's yml must match the spine table; changing a port is a spine change.

---

## Verdict

The spine's headline invariants (AD-1, AD-2 single-writer, AD-3 core chain, AD-5 versioning intent) are sound, but at the "one level down" they leave **four ungoverned seams** — dual-channel ordering (F1), transition/terminal-event responsibility (F2), contract authority (F3/F6), and consumer trust model (F4/F7). Under each, two builders that both obey the letter of every AD produce mutually incompatible services. **The state machine, event DTOs, and gRPC contract must be promoted from prose intent to a shared, authoritative artifact with explicitly assigned producers and ordering**, and AD-2/AD-4/AD-5 need the tightening listed above. Re-review after the findings are applied.

### Priority fix list

1. (F1) Status-first write ordering + outbox must live behind metadata's gRPC API, or AD-2 is violated by the Deferred plan.
2. (F2) AD-3: assign each transition to exactly one producer; mandate terminal-event emission for PROCESSED **and** FAILED.
3. (F3) Declare one authoritative shape (gRPC `VideoInfo`) for overlapping fields; add `failureReason` to proto or to the event-only list explicitly.
4. (F4) Add `eventId` to event DTOs; key dedupe on it; fix notification-history natural key.
5. (F5) AD-5: field-number immutability; servers expose both versions during migration; version events with `schemaVersion`.
6. (F6) Declare the single videoId minting point and make `CreateVideo` carry it.
7. (F7) Event trust model: owner-confirmed ordering + publisher allow-list.
