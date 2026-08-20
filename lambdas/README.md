# Lambda function source code

One directory per function. `_shared/` is the shared access layer imported by
every function — the single enforcement point for status transitions, event
envelopes, error mapping, and service clients.

```
lambdas/
  _shared/            # shared access layer (package name: shared)
    __init__.py
    status.py         # status state machine via DynamoDB conditional writes
    events.py         # deterministic event envelopes (UUID5 eventId)
    errors.py         # domain errors + HTTP error mapping (409/404/400/500)
    clients.py        # boto3 client factories (env-driven, config-not-code)
    tests/            # local pytest suite (never shipped in zips)
  smoke/              # Story 1.2 smoke fixture (ad-hoc invoke only)
  upload_handler/     # Story 1.3 upload journey (dir/package: upload_handler,
                      # Terraform function name: upload-handler)
  transcode/          # Story 2.1 transcode worker (pure S3 in -> S3 out;
                      # dir/package/function name: transcode)
  ...
```

## Packaging

Functions are zip-packaged. `terraform/smoke.tf` shows the pattern: one
`archive_file` places the `_shared` package at the zip root as `shared/`
alongside the function's `handler.py`, so handlers do
`from shared import status, events, errors, clients`. No Lambda layer
resources.

## boto3 availability — CONFIRMED (Story 1.2)

The smoke Lambda ran inside floci 1.6.0's real Docker runtime and reported
`boto3_available: true` — boto3 ships in the runtime image. `shared.clients`
uses boto3; the stdlib/urllib fallback was not needed.

## Smoke fixture (Story 1.2)

Declared by `terraform/smoke.tf`: the `video-metadata` table (reused by
Story 1.3 — do not redeclare it), an IAM role, and the `smoke` function.
Invoke ad-hoc (inspection only, never part of setup):

```bash
# bash / git-bash
aws lambda invoke --endpoint-url http://localhost:4566 \
  --function-name smoke --payload '{"scenario":"all"}' out.json
```

```powershell
# PowerShell (Windows host) — the aws CLI shim on this machine is broken,
# so invoke via local boto3 instead:
python -c "import boto3, json; c = boto3.client('lambda', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test'); print(json.dumps(json.load(c.invoke(FunctionName='smoke', Payload=json.dumps({'scenario':'all'}))['Payload']), indent=2))"
```

Scenarios: `create`, `create-idempotent`, `transition-legal`,
`transition-illegal`, `reassert`, `envelope`, `all`. The handler deletes its
fixed test record after every run, so the table stays empty for Story 1.3.

## Upload handler (Story 1.3)

`upload_handler/` (Terraform function name `upload-handler`, declared in
`terraform/upload.tf`) is the ingest leg behind the gateway route
`POST /videos/upload`:

1. Parses the multipart body RAW (floci's gateway delivers
   `isBase64Encoded: false` — never base64) with stdlib `email.parser`.
2. Mints `videoId` (UUID4) exactly once; the same id appears in the
   response, the S3 key (`{videoId}/{filename}`), the record, and the event.
3. Side effects in order: S3 `put_object` → `shared.status.create_record`
   (idempotent UPLOADED create) → `events.put_events` with the
   deterministic `video.uploaded` envelope on `video-bus`.
4. Responds `200 {"videoId": ...}`; parse failures → `400 {"error": ...}`,
   downstream failures → `500 {"error": ...}` via `shared.errors.map_error`.

Config-not-code: `UPLOADS_BUCKET`, `METADATA_TABLE`, `EVENT_BUS_NAME`,
`AWS_ENDPOINT_URL` are all Terraform-set env vars. The gateway data plane
is reachable only at
`http://localhost:4566/_aws/execute-api/{apiId}/{stage}/videos/upload`
(`api_id` Terraform output; see the root README for curl/Bruno usage).

## Transcode worker (Story 2.1)

`transcode/` (Terraform function name `transcode`, declared in
`terraform/transcode.tf`) is the processing leg's first worker — a PURE
worker (AD-4): S3 object in -> S3 object out, **no** status writes, **no**
events; the module does not even import `shared.status` or `shared.events`.

1. Validates the domain payload: `videoId` + `originalKey` required
   (missing/empty -> `MalformedInputError`); extra fields are tolerated —
   Story 2.2's state machine passes the full `video.uploaded` detail
   unchanged.
2. Demo-mode transcode: streams the object body from `video-uploads` to
   `video-processed` (no ffmpeg — real transcoding is a documented future
   extension). Processed key shape: `processed/{videoId}/{basename}`,
   tied to the same videoId (FR-6), deterministic — a re-invoke overwrites
   the same key (idempotent).
3. Returns the domain payload for the ASL result:
   `{videoId, originalKey, processedKey, sizeBytes}`.

Error semantics: this is not a client-facing function — no HTTP response
mapping. Malformed payload raises `MalformedInputError`; S3 failures
propagate raw. Either way the invocation fails, which is exactly what the
ASL task needs (Story 2.2 fails the execution).

Config-not-code: `UPLOADS_BUCKET`, `PROCESSED_BUCKET`, `AWS_ENDPOINT_URL`
are all Terraform-set env vars. The role is least-privilege: logs only +
`s3:GetObject` on `video-uploads/*` + `s3:PutObject` on
`video-processed/*` — no DynamoDB, no EventBridge.

Invoke ad-hoc (inspection only — the local aws CLI shim is broken, use
local boto3):

```bash
python -c "import boto3, json; c = boto3.client('lambda', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test'); print(json.dumps(json.load(c.invoke(FunctionName='transcode', Payload=json.dumps({'videoId': '<uuid>', 'originalKey': '<uuid>/<filename>'}))['Payload']), indent=2))"
```

## Local tests

```bash
# either (requirements-dev.txt is the dev dependency list):
uv run --with pytest python -m pytest lambdas/ -q
# or install the dev requirements into a venv first:
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest lambdas/ -q
```

Suites: `lambdas/_shared/tests/` (27 shared-layer tests),
`lambdas/upload_handler/tests/` (21 upload-handler ATDD tests, activated
from the red-phase scaffolds — assertions unchanged from the TEA run), and
`lambdas/transcode/tests/` (20 transcode-worker ATDD tests encoding the
Story 2.1 I/O matrix incl. the AD-4 purity guarantee).
Each `tests/conftest.py` registers the local `_shared/` directory as the
`shared` package so imports match the zip layout.
