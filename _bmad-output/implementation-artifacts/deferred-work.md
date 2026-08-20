# Deferred work

Findings surfaced during review that are real but not this story's problem to fix now.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-shared-access-layer-lambdas-_shared.md`
  summary: hashicorp/archive provider (used for Lambda zip packaging) is archived/deprecated upstream — pick a long-term packaging strategy before more functions adopt it
  evidence: Story 1.2 review (blind hunter) noted .terraform.lock.hcl pins hashicorp/archive 2.8.0; the provider works fine today and is the standard TF 5.x zip-packaging choice, but every future function's zip will inherit the dependency, so the alternative (external data source or local-exec zip) deserves a conscious decision at Epic 2 when packaging multiplies
- source_spec: `_bmad-output/implementation-artifacts/spec-2-1-transcode-worker-lambda-pure-s3-in-s3-out.md`
  summary: deployed transcode wiring (hand-maintained zip layout, handler string, env-var names, IAM policy) has no automated backstop — a dropped zip source block or renamed env var ships green through the whole CI mirror and fails only on first real invoke
  evidence: Story 2.1 review (verification-gap layer): unit tests monkeypatch the client and env vars and import from the source tree, never the zip; terraform validate is syntax-only; the smoke stage invokes only the `smoke` function. Natural fix is a transcode scenario in the smoke Lambda gated by ci-local.sh stage 5 — deferred because the Story 2.1 Code Map marks CI read-only and the smoke Lambda is out of scope; Story 2.2 (ASL invokes transcode) is the natural home
