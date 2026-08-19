# BMad Review — `_bmad-output/planning-artifacts/epics.md`

**Date:** 2026-08-18 · **Content class:** docs (behavior-defining epic/story breakdown, 5,361 words)
**Lenses run:** adversarial, edge-case-hunter, structure, prose (prose ran on structure's findings)
**Skipped:** verification-gap (code-only lens) · **Cross-checked against:** PRD + addendum, ARCHITECTURE-SPINE.md
**Header note:** `persistent_facts` glob `**/project-context.md` matched no file — continued without it.

---

## Adversarial (18 findings)

**1.** location: general (Epic 4 summary L159; Coverage Map L138; Story 4.4 L465)
trigger: SM-1 invoked as "the definition of done" but never defined in this document; SM-2/SM-3 get no coverage mapping at all
guard: define SM-1 inline at first use (or add a Success Metrics section restating PRD §6); note SM-2 = FR-23/NFR-8, SM-3 = self-assessed
consequence: a reader or dev agent without the PRD cannot evaluate the lab's definition of done; SM-2/SM-3 silently drop out of scope

**2.** location: Story 1.3 (L222–251) / FR-10 (L37)
trigger: `title` is required by FR-10, indexed by Story 4.1, searched by Story 4.2 — but no story says how it enters the metadata record
guard: specify title provenance in Story 1.3 (multipart form field with filename fallback, or filename-only) and assert it in the upload AC
consequence: the search journey is built on a field nothing populates; Story 4.2's title-substring search has nothing to match

**3.** location: FR Coverage Map L127 / Story 1.2 (L191–220)
trigger: coverage map credits the shared layer with FR-11 enforcement, but no v1 function calls the layer's transition helper — the ASL's inline conditions are the live enforcement
guard: reword to "transition table authored in Epic 1 (shared layer); the v1 enforcement path is Epic 2's ASL inline conditions"; state the helper has no v1 caller
consequence: a dev agent may wire functions through the unused helper or believe the pipeline is enforced in code it isn't

**4.** location: Story 1.2 L203 vs Story 2.2 L295–297
trigger: same-status re-assertion succeeds idempotently in the shared layer (FR-11) but fails the execution in the ASL — divergent semantics reconciled only by "unreachable in practice"
guard: state the divergence explicitly in Story 2.2: the ASL deliberately fails re-assertion (strict from-state condition pairs); the shim dedupe is what makes it unreachable
consequence: two encodings of one transition table behave differently; a future caller of the layer helper gets success where the pipeline gives failure

**5.** location: FR Coverage Map L129 / FR-13 (L40)
trigger: FR-13's update-half (update for unknown videoId → not-found) has no exercisable surface in v1 — no client update route exists; the ASL updateItem just fails its condition
guard: mark FR-13 partially covered (read-half via Story 3.2's 404; update-half realized only in the shared layer, unexercised) or add a layer-test AC
consequence: the coverage map claims FR-13 for Epic 1 but no acceptance criterion ever verifies the update path

**6.** location: Story 1.3 L241 / Story 2.2 L292
trigger: ACs assert an event "is on the EventBridge bus", but EventBridge has no bus-history API — and at Story 1.3's point no rule or target exists yet, so the event has no observable effect anywhere
guard: verify via observable proxies: PutEvents success in the handler's log/response; from Epic 2 onward, downstream delivery (queue message, consumer writes)
consequence: Story 1.3's FR-4 criterion is unverifiable as written; a dev agent improvises a check that proves nothing

**7.** location: Story 4.4 L477–480 (also 3.2, 4.2)
trigger: the pipeline is async (queue → shim → SFN → publisher → rules → consumers) but the Bruno collection runs upload → history → search with no wait/poll strategy specified
guard: add polling-with-timeout assertions (or wait steps) between upload and the history/search requests in the collection's design
consequence: the collection fails intermittently on fresh bring-up depending on race timing; SM-1 verification becomes flaky
*(overlaps edge-case E11 — kept in both as signal)*

**8.** location: Story 4.4 L486–488
trigger: Story 4.4 reviews the README/setup documentation as an acceptance criterion, but no story produces it
guard: add README authoring as an explicit deliverable (its own story, or a named deliverable inside Story 4.4's Given)
consequence: the final verification gates on a document nobody was tasked to write

**9.** location: Story 1.2 (L191–220) / Additional Requirements L78
trigger: how `lambdas/_shared/` lands inside each function's zip (Lambda layer, copy-at-package-time, Terraform archive trick) is unspecified, yet every Lambda story's Terraform declaration depends on it
guard: specify the packaging mechanism in Story 1.2 as a decision with a one-line rationale
consequence: each Lambda story re-decides ad hoc; functions import the layer inconsistently or Terraform zips miss it

**10.** location: Story 1.2 L218–220
trigger: the boto3 smoke test needs a Lambda running in floci's Docker runtime, but the story declares no Terraform resources and doesn't say how the smoke Lambda is deployed or removed — ad-hoc creation sits in tension with NFR-8
guard: state the mechanism: a temporary Terraform-declared smoke function removed afterward, or an explicitly labeled PoC exception to NFR-8 with cleanup
consequence: the story's verification step is unexecutable as specified, or it silently violates the Terraform-only rule

**11.** location: Story 1.3 L243–247 / Story 4.4 L475
trigger: the Bruno environment file needs the `apiId` from Terraform output, but no story says how it gets there after a fresh apply — and Story 4.4 claims the rebuild has "no manual steps"
guard: specify the propagation (script reading `terraform output` into the Bruno env, or a documented manual copy — and amend 4.4's wording accordingly)
consequence: fresh-apply reproducibility silently depends on an unscripted manual copy; Story 4.4's "no manual steps" claim is false as written

**12.** location: Story 1.2 L209–212 / Story 2.1 L269 / Story 2.2 L283
trigger: the `video.uploaded` event's `detail` payload shape is unspecified anywhere, yet the shim → ASL → transcode chain requires it to carry at least videoId, original key, and bucket
guard: fix the detail shape in Story 1.2's envelope AC (e.g. `detail = {videoId, status, bucket, key}`) and reference it from Stories 2.1/2.2
consequence: the trigger leg is built on an event contract that exists only by implication; shim and ASL payloads get guessed

**13.** location: Story 4.1 L405–407
trigger: the hand-crafted FAILED test event also fans out to the history queue, whose consumer records every terminal event — injecting a FAILED entry the real pipeline never produced
guard: specify the test videoId choice and expected residue (or inject directly into `search-queue` only), and account for the extra history entry in Story 4.4's assertions
consequence: the test pollutes status-history; Story 4.4's "one upload produces" verification sees a phantom FAILED entry

**14.** location: Story 4.4 L484 / SM-1 restatement
trigger: SQS is absent from the exercised-services verification list even though the flow crosses three queues — and the PRD names SQS a first-class learning surface
guard: add SQS to the demonstrably-exercised list with its evidence (queue messages, ESM invocations)
consequence: the lab's definition of done under-verifies the very transport that carries every event

**15.** location: Story 1.3 (L228–247)
trigger: upload partial failure is unaddressed: S3 put succeeds then record-create fails leaves an orphan object; only the lost-event case is deferred (ingest-leg reconciliation)
guard: add an AC or an explicit acceptance note covering the S3-orphan case (accept residue, or order create-before-put)
consequence: an unacknowledged failure mode ships as a surprise during build

**16.** location: Story 2.2 (L283–301)
trigger: failure aftermath unspecified: transcode failure leaves the record stuck in PROCESSING with no terminal event; publisher failure after the PROCESSED ack leaves a terminal record with no event and a re-run blocked by the first condition
guard: acknowledge both as accepted v1 outcomes (FAILED path deferred) in the story's notes, or note the publisher-retry gap explicitly
consequence: a dev agent hits these mid-build and improvises recovery logic that violates the deferred-scope guard
*(overlaps edge-case E6/E7 — kept in both as signal)*

**17.** location: Story 1.3 L237 / Additional Requirements L88
trigger: raw multipart parsing in stdlib python3.11 (`cgi` deprecated) is an unacknowledged implementation risk, while boto3 gets an explicit "must be confirmed" note
guard: give multipart parsing the same treatment: name the approach and verify it in Story 1.3's first AC, or spike it
consequence: the upload story stalls on an unanticipated parsing problem mid-implementation

**18.** location: Story 1.1 L176–179
trigger: docker-compose image pinning is unspecified — "floci 1.6.0 is healthy" is asserted but nothing requires pinning the tag, and NFR-6 reproducibility depends on it
guard: add to Story 1.1's AC: compose pins `floci/floci:1.6.0` (not `latest`)
consequence: a silent upstream image update breaks the lab between destroy/apply cycles

---

## Edge-Case Hunter (11 findings)

**E1.** location: Story 1.2 AC (L214–216) + FR-13
trigger: transition on unknown videoId raises ConditionalCheckFailed → mapped 409, not FR-13's 404
guard: distinguish unknown-id (GetItem pre-check) before mapping 409
consequence: layer error contract contradicts FR-13 for unknown ids

**E2.** location: Story 1.3 AC (L236–241)
trigger: multipart with multiple files or a 0-byte file — behavior unspecified
guard: AC: reject multi-file with 400; decide accept/reject for empty file
consequence: ambiguous uploads pass silently or crash the parser

**E3.** location: Story 1.3 AC (L238–241)
trigger: S3 put succeeds then PutItem fails → orphan object, no compensation
guard: order create-before-put, or accept residue with an explicit note
consequence: orphan objects accumulate; failure mode unhandled

**E4.** location: Story 2.3 AC (L316–317)
trigger: malformed/non-JSON SQS body or missing eventId in the shim — no handling specified
guard: spec: unparseable record → log + ack (poison); missing eventId → drop + log
consequence: one bad message wedges the trigger queue in infinite retries

**E5.** location: Story 2.3 AC (L316–317)
trigger: ESM batch with 2+ Records[] — per-record processing unspecified
guard: AC: shim iterates all Records[]; each handled independently
consequence: Records[0]-only handling silently drops the rest of the batch

**E6.** location: Story 2.2 ASL (L283–297)
trigger: transcode throws → execution fails, record stuck in PROCESSING, no terminal event
guard: note as accepted (FAILED deferred) or add Catch → FAILED updateItem
consequence: video stuck mid-state forever, invisible to history/search

**E7.** location: Story 2.2 ASL (L283–297)
trigger: publisher fails after PROCESSED ack → terminal record, no event, re-run blocked by first condition
guard: spec a recovery (republish path) or accept and document
consequence: PROCESSED video never reaches history/search; unrecoverable by re-run

**E8.** location: Story 3.2 AC (L373–379)
trigger: known videoId with zero history entries — 200-empty vs 404 unspecified
guard: AC: known video with no entries → 200 with empty list
consequence: query for an in-flight video returns an ambiguous error

**E9.** location: Story 3.1 / Story 4.1 consumer ACs
trigger: malformed envelope (non-JSON body, missing fields) — drop vs retry unspecified (FR-15 covers only unknown videoId)
guard: extend FR-15 semantics: unparseable → drop + log; missing required field → drop + log
consequence: poison message retries forever, or junk gets stored

**E10.** location: Story 4.3 AC (L457–459)
trigger: rebuild merge-vs-replace semantics unspecified
guard: spec: rebuild = full replace (or upsert-merge and accept stale entries)
consequence: stale index entries survive rebuild for deleted/regressed videos

**E11.** location: Story 4.4 AC (L477–480)
trigger: history/search requests issued before the async pipeline completes — race
guard: poll with timeout in the collection; assert eventual consistency
consequence: intermittent false failures on fresh bring-up
*(overlaps adversarial #7)*

---

## Editorial — Structure + Prose

**Purpose/audience read:** this document exists to help the builder (Kygor) and downstream BMAD dev agents implement the serverless video-processing lab incrementally, with full traceability from PRD requirements to stories.
**Structure model:** Prompt/Task Definition (Functional) — stories are task specs; meta-first (requirements inventory precedes stories), explicit flow (epic dependency chain stated). Fits cleanly.

| Pass | Original Text | Revised Text | Changes |
| --- | --- | --- | --- |
| structure | §Epic List epic descriptions (L146–160) vs each Epic section opener (L166, L255, L330, L387) | CONDENSE — keep descriptions in Epic List; drop the verbatim-repeated paragraph under each epic heading | True redundancy: identical paragraphs repeated ~20 lines later, no reinforcement value (saves ~180 words) |
| structure | §Requirements Inventory — full FR restatement (632 words) | PRESERVE | Looks like PRD duplication, but self-containment serves story generation; keep, with a one-line "source of truth = PRD" note to manage drift risk |
| structure | §Additional Requirements (802 words — largest section) | QUESTION — consider replacing the AD-1…AD-9 digest with a pointer to ARCHITECTURE-SPINE.md plus only story-relevant deltas | Architecture content restated in a planning doc; two copies can drift. Author's call — self-containment vs single source of truth |
| structure | §FR Coverage Map (353 words, H3 under Requirements Inventory) | MOVE — promote to its own H2 or place at the head of §Epic List | It bridges inventory → epics; buried as an H3 it reads like part of the requirements restatement |
| structure | §UX Design Requirements (19 words) | CONDENSE — already stated in Overview L14 ("no UX design requirements apply"); reduce to "None." or drop if the template allows | True redundancy with the Overview sentence (saves ~15 words) |
| structure | SM-1 definition of done (buried in Story 4.4 AC) | MOVE — restate SM-1 in Overview or §Epic List | The document's success criterion first appears as an undefined term in a story AC (overlaps adversarial #1) |
| prose | L32: "a republish is a dedupe, never a duplicate, and holds across restarts" | "a republish is a dedupe, never a duplicate — the guarantee holds across restarts" | Unclear antecedent: "holds" had no subject |
| prose | L127: "legal-transition enforcement via conditional writes (shared layer); exercised by Epic 2's ASL condition pairs" | Consider: "transition table authored in the shared layer (Epic 1); live enforcement is Epic 2's ASL condition pairs" | Current wording misleads about where enforcement lives (see adversarial #3) |
| prose | L136: "(founded in E1; routes added in E3/E4)" | "(established in E1; routes added in E3/E4)" | Odd diction ("founded") |
| prose | L342: "the video.processed rule now targets the history queue in addition to any existing targets" | "the video.processed rule targets the history queue (Story 4.1 adds the search-queue target)" | Inaccurate at Story 3.1 — the rule is created there, so no existing targets exist yet |
| prose | L405: "(rules only in v1 — exercised by a hand-crafted test event)" | "(FAILED is rules-only in v1 — exercised by a hand-crafted test event)" | Cryptic — subject missing |
| prose | L455: "the function exists reachable only by direct invoke" | "the function exists and is reachable only by direct invoke" | Grammar |

**Structure summary:** 6 recommendations (2 CONDENSE, 2 MOVE, 1 PRESERVE, 1 QUESTION). Estimated reduction if all cuts accepted: ~195 words ≈ 3.6% of 5,361 — the document is mostly functional; no length target was given. No comprehension trade-offs: nothing proposed for cutting carries engagement value, and the two PRESERVE/QUESTION items err on the side of keeping content.

---

## Overlap notes

- Adversarial #7 ↔ Edge-case E11 (async race in the Bruno collection)
- Adversarial #15 ↔ Edge-case E3 (S3 orphan on partial upload failure)
- Adversarial #16 ↔ Edge-case E6/E7 (ASL failure aftermath)
- Adversarial #1 ↔ Structure MOVE row (SM-1 undefined/buried)
- Adversarial #3 ↔ Prose L127 row (FR-11 enforcement wording)
