# Epic 1 Context: Lab Foundation & Video Upload Ingest

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Bring up the local serverless lab and make the first pipeline leg work end-to-end: `docker compose up` starts floci, `terraform apply` creates the environment, and a multipart video POST through API Gateway v2 lands the object in S3, creates an `UPLOADED` record in DynamoDB, and puts a `video.uploaded` event on the EventBridge bus. This epic also delivers the foundations every later epic builds on: the Terraform provider skeleton, the shared access layer (status state machine, event envelopes, error mapping, clients), the `video-metadata` table, and the gateway with its upload route. This is a personal AWS-learning lab on the floci emulator — correctness and understanding are the goals; performance and production polish are explicitly non-goals.

## Stories

- Story 1.1: Lab Environment Bootstrap (floci + Terraform skeleton)
- Story 1.2: Shared Access Layer (`lambdas/_shared/`)
- Story 1.3: Upload Journey Through the Gateway

## Requirements & Constraints

- Upload journey: POST multipart/form-data to `POST /videos/upload` via the gateway returns HTTP 2xx with the minted `videoId` (UUID); the object exists in `video-uploads` under a key containing that same `videoId`; the `video-metadata` record exists with status `UPLOADED` and created/updated timestamps; a `video.uploaded` event with deterministic `eventId` is on the bus. `videoId` is minted exactly once at ingress and reused across S3 key, record, and event.
- The handler reads an optional `title` multipart form field, falling back to the uploaded filename; title is stored in the record (Epic 4's title search matches on it).
- Metadata record shape: videoId (PK), title, status, bucket, originalKey, processedKey, contentType, sizeBytes, durationSeconds, failureReason, createdAt, updatedAt (ISO-8601 UTC).
- State machine rules: only legal transitions accepted (`UPLOADED → PROCESSING → PROCESSED | FAILED`, terminals final); same-status re-assertion idempotent; create idempotent by videoId (retry returns existing record unchanged); unknown videoId → not-found error, never silent success.
- Error semantics on all client-facing surfaces: 400/404/409/500 with body `{"error": "<message>"}`; the gateway passes status codes and bodies through unchanged; no auth anywhere. Malformed upload (missing file / unparseable multipart) → 400.
- Every resource is declared in Terraform and created by `terraform apply`; documented setup/teardown contains no `aws` CLI invocations (ad-hoc inspection only, never setup).
- A Bruno collection (`bruno/`) is founded with the upload request: environment file holding the gateway base URL (from the Terraform `apiId` output), assert blocks per request, runnable via `bru run`, and every request targets the gateway only — never backend endpoints directly.

## Technical Decisions

- **Platform:** floci 1.6.0 via Docker at `localhost:4566`, dummy credentials, us-east-1, local Terraform state. `docker-compose.yaml` MUST mount `/var/run/docker.sock` into the floci container or no Lambda runs. Greenfield repo — scaffold the structural seed: `docker-compose.yaml` (floci only), `terraform/`, `lambdas/_shared/` + one dir per function, `bruno/`.
- **Terraform:** >= 1.6.0, hashicorp/aws ~> 5.0 targeting `http://localhost:4566`, `s3_use_path_style=true`, skip credential validation. The provider `endpoints{}` block must list every service used (s3, sqs, dynamodb, lambda, stepfunctions, events, apigatewayv2, iam, cloudwatch, sts) — a missing endpoint yields `InvalidClientTokenId` 403s. Story 1.1 creates the substrate only (no resources yet).
- **Gateway data plane:** reachable only at `http://localhost:4566/_aws/execute-api/{apiId}/{stage}/{path}` — the Terraform invoke URL does not resolve locally. Expose `apiId` as a Terraform output. The gateway delivers multipart bodies RAW (`isBase64Encoded: false`) — the upload handler parses multipart itself, never assumes base64.
- **Lambdas:** python3.11, zip-packaged. Config-not-code: no endpoint, credential, or resource name hardcoded — all via Lambda env vars set by Terraform; function-to-floci calls use `AWS_ENDPOINT_URL` (resolves to `host.docker.internal:4566` inside Lambda containers). boto3 is CONFIRMED present in the floci 1.6.0 runtime image (verified by Story 1.2's smoke run); the stdlib/urllib fallback was not needed.
- **Shared layer (`lambdas/_shared/`) is the single enforcement point:** it alone knows the legal-transition table (transitions via `UpdateItem` with `ConditionExpression: #s = :expected`; create via `PutItem` with `attribute_not_exists(videoId)`); it constructs event envelopes — `eventId` = deterministic name-based UUID5 of `(videoId, status)`, every envelope carries `eventId` + `schemaVersion` + `detail`, event names verb-in-past; it maps errors (transition conflict → 409, unknown videoId → 404, malformed input → 400, else 500); client factories read `AWS_ENDPOINT_URL` and resource names from env vars only. The `video.uploaded` detail shape is fixed here and consumed unchanged downstream: `{videoId, status, bucket, key}`.
- **Identity & publishing:** only the upload handler publishes `video.uploaded` and mints `UPLOADED`; later statuses belong to the processing state machine (Epic 2). There is no metadata service-Lambda — the table rejects illegal transitions via conditional writes. One custom EventBridge bus; `video.uploaded` routes to the processing-trigger queue only (queue/shim/state machine are Epic 2 — Epic 1 declares the bus and publishes the event).
- **Naming:** buckets `video-uploads` / `video-processed`; tables `video-metadata` / `status-history` / `search-index`; one dir per function under `lambdas/`; all names declared in Terraform, consumed via env vars, never string-typed in code.
- **Local-lab CI:** a GitHub Actions pipeline (`.github/workflows/ci.yml`) is in scope for the lab — lint (ruff E,F + terraform fmt), pytest unit suite, terraform validate, and an ephemeral floci smoke stage (compose up → apply → smoke invoke → destroy). New code, tests, and Terraform changes must keep it green.
- **Deferred (do not build):** presigned-URL ingest, FAILED-producing path, real ffmpeg, single-table DynamoDB, event-schema versioning policy, SQS DLQ/retry, ingest-leg reconciliation, real AWS, remote state.

## Cross-Story Dependencies

- Strict order within the epic: Story 1.1 (substrate) → Story 1.2 (shared layer) → Story 1.3 (upload journey, needs both).
- Epic 2 consumes this epic's outputs directly: the `video.uploaded` event shape, the bus, and the shared layer's transition table — the detail payload shape must not drift once fixed.
- The Bruno collection founded here grows in Epics 3–4 and is verified end-to-end in Story 4.4 (clean `terraform destroy` + `apply` reproduces the whole pipeline).
