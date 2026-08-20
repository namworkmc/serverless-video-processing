# Serverless Video Processing

Serverless video-processing platform (upload → transcode → status/search) running
entirely local on [floci](https://github.com/floci-io/floci), a free LocalStack-compatible
AWS emulator. Infrastructure is managed exclusively with **Terraform** — no `aws cli`
in setup/teardown.

## Stack

| Layer | Technology |
|---|---|
| Emulator | floci (`localhost:4566`, no auth token) |
| IaC | Terraform (AWS provider → `http://localhost:4566`) |
| Compute | AWS Lambda (ffmpeg transcode) — planned |
| Orchestration | Step Functions / EventBridge — bus live, processing leg planned |
| Storage | S3 (`video-uploads`) + DynamoDB (`video-metadata`) |
| Ingress | API Gateway v2 (`POST /videos/upload`) |

## Quick start

```bash
# 1. Start the emulator
docker compose up -d

# 2. Wait for health (all services "running")
curl -s http://localhost:4566/_localstack/health | python -m json.tool

# 3. Provision infrastructure
cd terraform
terraform init
terraform apply
```

Teardown: `terraform destroy`, then `docker compose down`.

## Upload journey (Story 1.3)

The Terraform invoke URL does not resolve locally — the gateway data plane
is reachable only through floci's `_aws/execute-api` mount:

```
http://localhost:4566/_aws/execute-api/{apiId}/{stage}/videos/upload
```

`api_id` is a Terraform output (`terraform output api_id`); the stage is
`local`. Upload a video:

```bash
API_ID=$(terraform -chdir=terraform output -raw api_id)
curl -s -X POST "http://localhost:4566/_aws/execute-api/$API_ID/local/videos/upload" \
  -F "file=@my-video.mp4;type=video/mp4" -F "title=My Video"
# -> 200 {"videoId": "<uuid4>"}
```

Side effects: the object lands in `video-uploads` under `{videoId}/{filename}`
(filename is sanitized — path components and control characters are
stripped), the `video-metadata` record is created with status `UPLOADED`
(title falls back to the filename when the `title` field is absent), and a
`video.uploaded` event with a deterministic UUID5 `eventId` is published to
the `video-bus` EventBridge bus. Malformed requests (missing file part,
unparseable body, empty file, invalid filename) return `400 {"error": ...}`.

**Known limits (lab scope):**

- **~6 MB payload ceiling.** API Gateway v2 proxy payloads cap at ~6 MB
  (hard limit on AWS; floci inherits the shape). The handler buffers the
  whole body in memory — fine for demo clips, not for real video. Presigned
  URLs are the production answer (deferred).
- **Partial-failure semantics.** The side-effect chain is S3 → record →
  event, strictly ordered. If a later step fails, earlier side effects
  persist (orphaned object/record) and the client gets `500` without a
  `videoId`; a retry mints a *new* `videoId`. There is no compensation and
  no client idempotency key — acceptable for the lab, documented here so
  nobody is surprised.
- **Event Detail shape.** The wire `Detail` is the envelope with the
  detail fields promoted to the top level:
  `{eventId, schemaVersion, videoId, status, bucket, key}`. The flat view
  is canonical for consumers; the nested `detail` object stays intact for
  envelope-shaped readers. Detail keys must never collide with envelope
  keys (`eventId`, `schemaVersion`).

### Bruno collection

`bruno/` holds the API collection — every request targets the gateway base
URL only, never backend endpoints. `bruno/sample.mp4` is a tiny text stub
(`fake-video-bytes-for-bruno`), not a real video — swap in a real file for
realistic testing. Set the `gatewayBaseUrl` variable in
`bruno/environments/Local.bru` (replace the `REPLACE_WITH_API_ID`
placeholder) from the `api_id` output after apply, then:

```bash
cd bruno
bru run --env Local
# or without editing the env file:
bru run --env Local --env-var "gatewayBaseUrl=http://localhost:4566/_aws/execute-api/$API_ID/local"
```

## Repository layout

```
docker-compose.yaml # floci emulator
terraform/          # all AWS resources (buckets, bus, tables, lambdas, gateway)
lambdas/            # Lambda function source code (one dir per function)
bruno/              # Bruno API collection (gateway data plane only)
_bmad-output/       # BMAD planning artifacts (PRD, architecture, epics)
```

## Status

Story 1.3 complete: the upload journey works end-to-end through the
gateway — `upload-handler` Lambda (raw multipart parse, UUID4 videoId
minted once, S3 put → idempotent `video-metadata` record → deterministic
`video.uploaded` event, all via the shared layer), `video-uploads` bucket,
`video-bus` EventBridge bus, and API Gateway v2 with `POST /videos/upload`
(`terraform/upload.tf`, `api_id` output). The 21 red-phase ATDD scaffolds
are green, plus 6 review-phase guard tests (54 tests total with the shared
layer). Bruno collection founded and passing. Next: Epic 2, the processing
leg — see `_bmad-output/`.
