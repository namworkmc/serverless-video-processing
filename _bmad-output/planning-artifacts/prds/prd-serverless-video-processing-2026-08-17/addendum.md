# Addendum — Serverless Video Processing Platform PRD

Technical decisions and rationale captured during PRD discovery (2026-08-17). These belong to downstream documents (architecture, implementation), preserved here so they survive the PRD distillation.

## AD-A: Re-architecture decision

The inherited `aws-lab` artifacts (Spring Boot microservices, gRPC, PostgreSQL, SQS-glued services on ministack) were deliberately **not** carried forward as the plan. The builder chose to re-architect serverless-native around Lambda + Step Functions + DynamoDB + EventBridge on floci. Inherited artifacts remain as **domain reference only**: the pipeline shape (upload → transcode → status/search), the `UPLOADED → PROCESSING → PROCESSED | FAILED` state machine, deterministic `eventId` idempotency, poison-event handling, and single-ingress pattern all survive as domain requirements; the technology stack does not.

## AD-B: Transcode compute — zip-packaged Python Lambda with copy fallback

**Decision:** v1 uses a zip-packaged Python Lambda with demo-mode copy fallback (no real ffmpeg). Real ffmpeg transcoding via container-image Lambda is a documented future extension.

**Rationale:** time-saver chosen explicitly by the builder. Zip packaging means no Docker image builds, no ECR, just a `.py` file and `aws_lambda_function` in Terraform. floci runs Lambdas in real Docker, so a container-image upgrade path exists later without architectural change.

**Rejected alternative:** container-image Lambda with ffmpeg baked in — more realistic but adds image-build tooling to the learning surface before the core AWS patterns are exercised.

## AD-C: Pure event-driven, no synchronous RPC

**Decision:** all coordination between components is via events (EventBridge) + DynamoDB reads/writes. No gRPC, no sync service-to-service calls — there are no long-lived services.

**Rationale:** serverless-native model chosen by the builder; also matches how real AWS serverless architectures work.

## AD-D: Event schema not a learning feature

**Decision:** event schemas (`video.uploaded`, `video.processed` with `eventId` + `schemaVersion`) exist and are shared, but schema design/versioning is **not** a standalone feature or learning surface.

## AD-E: FAILED path deferred

**Decision:** same as the old lab — `FAILED` exists only as state-machine rules; nothing produces it in v1.

## AD-F: Emulator & IaC constraints

- **floci** (floci-io/floci): LocalStack-compatible, `localhost:4566`, no auth token, any region, dummy credentials. Runs Lambda in real Docker. No MediaConvert/Elastic Transcoder — hence transcode compute is Lambda.
- **Terraform-only** for all infrastructure (AWS provider → `http://localhost:4566`, `s3_use_path_style=true`, dummy creds, skip credential validation). `aws cli` allowed for ad-hoc PoC/inspection only, never in documented setup/teardown.
- Phase 0 verified: floci healthy (s3, sqs, dynamodb, lambda, states, events, apigatewayv2 all running); Terraform S3 round-trip (apply → verify via S3 API → destroy) passed.

## AD-G: Goal is purely personal learning

The teaching/explaining-to-others angle from the old lab was dropped. Success is self-understanding of each AWS service's role, not demonstrability to an audience.
