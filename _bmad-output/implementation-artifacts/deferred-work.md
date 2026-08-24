# Deferred work

Findings surfaced during review that are real but not this story's problem to fix now.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-shared-access-layer-lambdas-_shared.md`
  summary: hashicorp/archive provider (used for Lambda zip packaging) is archived/deprecated upstream — pick a long-term packaging strategy before more functions adopt it
  evidence: Story 1.2 review (blind hunter) noted .terraform.lock.hcl pins hashicorp/archive 2.8.0; the provider works fine today and is the standard TF 5.x zip-packaging choice, but every future function's zip will inherit the dependency, so the alternative (external data source or local-exec zip) deserves a conscious decision at Epic 2 when packaging multiplies
- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-transcode-worker-lambda-pure-s3-in-s3-out.md`
  summary: deployed transcode wiring (hand-maintained zip layout, handler string, env-var names, IAM policy) has no automated backstop — a dropped zip source block or renamed env var ships green through the whole CI mirror and fails only on first real invoke
  evidence: Story 2.1 review (verification-gap layer): unit tests monkeypatch the client and env vars and import from the source tree, never the zip; terraform validate is syntax-only; the smoke stage invokes only the `smoke` function. Natural fix is a transcode scenario in the smoke Lambda gated by ci-local.sh stage 5 — deferred because the Story 2.1 Code Map marks CI read-only and the smoke Lambda is out of scope; Story 2.2 (ASL invokes transcode) is the natural home
  status: superseded 2026-08-23 (not done) — integration test T7 (ad-hoc deployed-transcode invoke) is now the backstop; the smoke scenario built for this was retired with `lambdas/smoke/`
- source_spec: `_bmad-output/implementation-artifacts/spec-2-2-processing-state-machine-event-publisher.md`
  summary: Add a `transcode` scenario to the smoke Lambda (fixture object → invoke transcode → assert payload + processed object → cleanup) with the smoke role/env extensions, so deployed transcode wiring is backstopped in ci-local.sh stage 5 and CI smoke
  evidence: Story 2.2 spec exceeded the 1600-token scope guideline (~2,240 est.); user chose [S] Split at checkpoint 1 — the smoke backstop is an independently shippable test-infrastructure deliverable separable from the state-machine + publisher goal
  status: superseded 2026-08-23 (not done) — built in the 2026-08-21 retro batch, retired with `lambdas/smoke/`; coverage now integration test T7
- source_spec: `_bmad-output/implementation-artifacts/spec-2-2-processing-state-machine-event-publisher.md`
  summary: Add a state-machine runtime smoke scenario (create UPLOADED record + fixture object → StartExecution → poll to PROCESSED with processedKey set → assert processed object + exactly one video.processed with the deterministic eventId → re-run StartExecution fails at MarkProcessing with no regression/no second event → cleanup), gated by ci-local.sh stage 5
  evidence: Story 2.2 review (verification-gap layer): the ASL's runtime behavior — data-flow mappings ($.key→originalKey, $.processedKey), task chaining, FR-11 no-regression-on-rerun — is never executed by any automated test; test_asl_definition.py is source-text-only and the CI smoke stage runs shared-layer scenarios only. A broken mapping ships green through the whole CI mirror and fails only on first real StartExecution. Invoking the deployed chain also backstops the event-publisher zip/handler wiring (same unbackstopped pattern recorded for transcode). Live ACs passed manually (19/19) but are not committed/re-runnable
  status: superseded 2026-08-23 (not done) — built in the 2026-08-21 retro batch, retired with `lambdas/smoke/`; coverage now integration tests T5/T6 (end-to-end in T3)
- source_spec: `_bmad-output/implementation-artifacts/spec-2-2-processing-state-machine-event-publisher.md`
  summary: Reconcile updatedAt timestamp format — the ASL writes $$.State.EnteredTime (ISO-8601 with milliseconds) while the shared layer's _now_iso() writes second-precision Z strings, so updatedAt drifts between the two writers
  evidence: Story 2.2 review (verification-gap + edge-case layers): no consumer or test observes the format today, but a future consumer comparing/sorting updatedAt across writers would see mixed precision; fix is one coordinated change (normalize in the ASL or the shared layer)
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-trigger-leg-eventbridge-rule-queue-and-shim.md`
  summary: Update README.md and lambdas/README.md with the trigger-leg documentation — architecture diagram update (rule → queue → shim), upload-now-auto-processes flow, shim dedupe semantics (eb-{eventId}, ExecutionAlreadyExists ack), and Status section refresh
  evidence: Story 2.3 spec exceeded the 1600-token scope guideline (3,046 tokens); user chose [S] Split at checkpoint 1 — the docs update is an independently shippable deliverable separable from the trigger-leg implementation goal (queue, rule, shim, ESM, tests)
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-trigger-leg-eventbridge-rule-queue-and-shim.md`
  summary: Add a smoke scenario covering the trigger leg (publish video.uploaded → queue → shim → StartExecution) so the rule/queue/ESM/zip wiring has a CI regression net
  evidence: Story 2.3 review (verification-gap layer): smoke scenarios cover the shared layer only; a dropped zip source block or broken ESM surfaces only at runtime — all 5 CI stages stay green; the wiring was verified solely by a manual ad-hoc upload journey
  status: superseded 2026-08-23 (not done) — built in the 2026-08-21 retro batch, retired with `lambdas/smoke/`; coverage now integration tests T3/T4
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-trigger-leg-eventbridge-rule-queue-and-shim.md`
  summary: Unit-test the shared.clients factory service names (states_client → "stepfunctions"; the sibling factories are equally untested)
  evidence: Story 2.3 review (verification-gap layer): lambdas/_shared/tests/test_shared.py covers only _endpoint_url/_region; a service-name typo fails first at live invoke
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-trigger-leg-eventbridge-rule-queue-and-shim.md`
  summary: Consolidate the ClientError-code duck-typing pattern — shared.errors.is_conditional_check_failed and the shim's _is_execution_already_exists — into one shared helper
  evidence: Story 2.3 review (blind-hunter layer): duplicated type(exc).__name__ pattern; this story's spec capped the shared-layer change at the single states_client() factory
- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-history-consumer-recording-terminal-events.md`
  summary: Consolidate the duplicated _parse_detail EventBridge-unwrap helper (sfn_trigger_shim/handler.py and history_consumer/handler.py are verbatim copies) into the shared layer
  evidence: Story 3.1 review (blind-hunter layer): second identical copy now exists; same duplication class epic-2-retro-item-6 resolved for _require_field; the Story 3.1 spec's "no new shared-layer code" boundary blocked consolidation in-story
  status: resolved 2026-08-24 (epic-3 retro AI-4) — shared.events.parse_detail is the single definition; both handlers call it, pre-empting search-consumer's third copy
- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-history-consumer-recording-terminal-events.md`
  summary: Harden the history consumer to cross-check incoming eventId against the deterministic derivation events.event_id(videoId, status) and treat a mismatch as malformed
  evidence: Story 3.1 review (blind-hunter layer): dedupe rests on eventId being the UUID5 of (videoId, status), but the handler trusts the arriving eventId; a fabricated eventId paired with a KNOWN videoId is written as-is. Mitigated today by AD-6's closed publisher allow-list (only event-publisher constructs video.processed envelopes); adding the check changes frozen I/O-matrix behavior, so it needs a spec-level decision
- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-history-consumer-recording-terminal-events.md`
  summary: Add a history-leg smoke scenario (fresh video → PROCESSED → poll status-history by deterministic eventId → assert exactly one entry → cleanup) with smoke IAM GetItem on status-history, so the deployed consumer wiring is gated by ci-local.sh stage 5
  evidence: Story 3.1 review (verification-gap layer): no committed check reads status-history — deleting the PutItem IAM statement or a zip source block ships green through all 5 CI stages and fails only at runtime; the live ACs were manual runs. The spec's Design Notes explicitly deferred this ("Epic 3 retro candidate") and smoke.tf is an Ask-First boundary
  status: superseded 2026-08-23 (never built) — coverage now integration test T8 (dedupe/poison in T9/T10)
- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-history-consumer-recording-terminal-events.md`
  summary: Clean up status-history residue from smoke runs — the state-machine and trigger-leg smoke scenarios now emit video.processed events whose history entries persist after their metadata records are deleted
  evidence: Story 3.1 review (verification-gap layer): every smoke run writes entries for ephemeral smoke-* videoIds that are never observed or cleaned; harmless today (derived, disposable table, AD-3) but a history-leg smoke scenario should own cleanup of its own entries
  status: obsolete 2026-08-23 — smoke runs no longer exist (`lambdas/smoke/` retired); the integration suite owns its own test residue
- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-history-consumer-recording-terminal-events.md`
  summary: Update root README.md for the history leg — mermaid architecture diagram (bus → history-queue → history-consumer → status-history) and Status section refresh ("Next: Epic 3" is stale)
  evidence: Story 3.1 review (blind-hunter layer): root README still shows only the trigger leg; docs refresh batched with the retro action-item pattern (lambdas/README.md WAS updated in-story per Story 2.3 precedent)
- source_spec: `_bmad-output/implementation-artifacts/spec-3-2-history-query-through-the-gateway.md`
  summary: Extend the same root README refresh with Story 3.2's gateway surface — ingress table and mermaid diagram show only `POST /videos/upload`; the `GET /videos/{videoId}/history` route and history-query Lambda are absent, and the "⏭️ Next: Epic 3" status log is now two stories stale
  evidence: Story 3.2 review (blind-hunter layer, inline run): README.md:30/73/321 unchanged by this story per its read-only boundary; lambdas/README.md was updated in-story, root README was not (same batching decision as the Story 3.1 entry above)
- source_spec: `_bmad-output/implementation-artifacts/spec-3-2-history-query-through-the-gateway.md`
  summary: IAM policy/permission scoping is unobservable on floci — the history-query role's least-privilege claims (dynamodb:GetItem on video-metadata, dynamodb:Scan on status-history, logs) and the route-scoped `source_arn` on the invoke permission are not enforced by the emulator, and `terraform validate` is syntax-only, so a wrong action list or over-broad/typo'd source_arn applies green
  evidence: Story 3.2 review (verification-gap layer): floci runs with no IAM enforcement (dummy creds); inherited posture, not newly introduced — identical to the upload leg (upload.tf) and the Story 3.1 consumer role. Actionable only if/when the project adopts an IAM-enforcing emulator or real-AWS validation

## Resolved

- 2026-08-21 (retro action-item batch, branch `bmad/retro-action-items`): the transcode / state-machine / trigger-leg smoke scenarios (now in `lambdas/smoke/handler.py`, gated by ci-local.sh stage 5), the `shared.clients` service-name unit tests, the ClientError duck-typing consolidation (`shared.errors.is_client_error_code`), the duplicated `_require_field` consolidation (`shared.errors.require_field`), and the trigger-leg README/lambdas-README documentation. The updatedAt-format and archive-provider entries remain open.
- 2026-08-23 (integration-test-plan Step 2 — smoke retirement, branch `bmad/integration-test-plan`): the smoke-scenario entries marked above are closed SUPERSEDED (not done) per `_bmad-output/test-artifacts/integration-test-plan.md` §6.3: transcode backstop/scenario → T7, state-machine scenario → T5/T6, trigger-leg scenario → T3/T4, history-leg scenario → T8 (never built). The smoke scenarios built by the 2026-08-21 batch were deleted with `lambdas/smoke/`; their CI coverage is now the `tests/integration/` suite (T1–T10, CI stage 5). The status-history-residue entry is obsolete — no more smoke runs.
