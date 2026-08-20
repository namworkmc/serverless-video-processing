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
| Compute | AWS Lambda (`transcode` worker, demo-mode copy) — ffmpeg transcode planned |
| Orchestration | Step Functions / EventBridge — bus live, state machine planned (Story 2.2) |
| Storage | S3 (`video-uploads`, `video-processed`) + DynamoDB (`video-metadata`) |
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

## Transcode worker (Story 2.1)

The `transcode` Lambda (`terraform/transcode.tf`) is the processing leg's
first worker — a PURE worker (AD-4): S3 object in → S3 object out, no
status writes, no events. It reads the uploaded object from
`video-uploads` and streams it to `video-processed` under
`processed/{videoId}/{basename}` (demo-mode copy — no ffmpeg; real
transcoding is a documented future extension), then returns
`{videoId, originalKey, processedKey, sizeBytes}` for the state machine
(Story 2.2). Malformed payloads (missing/empty `videoId` or
`originalKey`) raise `MalformedInputError`; unknown source objects fail
the invocation — either failure is exactly what the ASL task needs.
Re-invoking with the same payload overwrites the same processed key
(idempotent). Invoke ad-hoc via local boto3 (the aws CLI shim is broken):

```bash
python -c "import boto3, json; c = boto3.client('lambda', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test'); print(json.dumps(json.load(c.invoke(FunctionName='transcode', Payload=json.dumps({'videoId': '<uuid>', 'originalKey': '<uuid>/<filename>'}))['Payload']), indent=2))"
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

Story 2.1 complete: the `transcode` worker Lambda exists and works
ad-hoc — pure S3 in → S3 out (demo-mode copy, no ffmpeg), no status
writes, no events (AD-4). Declared in `terraform/transcode.tf`:
`video-processed` bucket, least-privilege role (logs + GetObject on
uploads + PutObject on processed), and the `transcode` function
(python3.11, env `UPLOADS_BUCKET`/`PROCESSED_BUCKET`/`AWS_ENDPOINT_URL`).
Verified against a real gateway upload: processed object lands under
`processed/{videoId}/{basename}`, the metadata record stays UPLOADED,
re-invokes are idempotent, and the run is traceable in CloudWatch Logs.
20 new ATDD tests (74 total with the shared layer and upload handler).
Story 1.3 remains complete: the upload journey works end-to-end through
the gateway — `upload-handler` Lambda (raw multipart parse, UUID4 videoId
minted once, S3 put → idempotent `video-metadata` record → deterministic
`video.uploaded` event, all via the shared layer), `video-uploads` bucket,
`video-bus` EventBridge bus, and API Gateway v2 with `POST /videos/upload`
(`terraform/upload.tf`, `api_id` output). Bruno collection founded and
passing. Next: Story 2.2, the processing state machine + event publisher
— see `_bmad-output/`.
