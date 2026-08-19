# Deferred work

Findings surfaced during review that are real but not this story's problem to fix now.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-shared-access-layer-lambdas-_shared.md`
  summary: hashicorp/archive provider (used for Lambda zip packaging) is archived/deprecated upstream — pick a long-term packaging strategy before more functions adopt it
  evidence: Story 1.2 review (blind hunter) noted .terraform.lock.hcl pins hashicorp/archive 2.8.0; the provider works fine today and is the standard TF 5.x zip-packaging choice, but every future function's zip will inherit the dependency, so the alternative (external data source or local-exec zip) deserves a conscious decision at Epic 2 when packaging multiplies
