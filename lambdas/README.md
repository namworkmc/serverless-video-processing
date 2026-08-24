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
  upload_handler/     # Story 1.3 upload journey (dir/package: upload_handler,
                      # Terraform function name: upload-handler)
  transcode/          # Story 2.1 transcode worker (pure S3 in -> S3 out;
                      # dir/package/function name: transcode)
  event_publisher/    # Story 2.2 event publisher (sole constructor of the
                      # video.processed envelope; dir/package: event_publisher,
                      # Terraform function name: event-publisher)
  history_consumer/   # Story 3.1 history consumer (video.processed ->
                      # status-history, dedupe by eventId; dir/package:
                      # history_consumer, Terraform function name:
                      # history-consumer)
  history_query/      # Story 3.2 history query (GET /videos/{videoId}/
                      # history through the gateway; dir/package:
                      # history_query, Terraform function name:
                      # history-query)
  search_consumer/    # Story 4.1 search consumer (video.processed ->
                      # search-index, dedupe by videoId; dir/package:
                      # search_consumer, Terraform function name:
                      # search-consumer)
  search_query/       # Story 4.2 title search (GET /videos/search?title=
                      # through the gateway; dir/package: search_query,
                      # Terraform function name: search-query)
  search_rebuild/     # Story 4.3 admin-only index rebuild (direct invoke
                      # ONLY; dir/package: search_rebuild, Terraform
                      # function name: search-rebuild)
  ...
```

## Packaging

Functions are zip-packaged. `terraform/upload.tf` shows the pattern: one
`archive_file` places the `_shared` package at the zip root as `shared/`
alongside the function's `handler.py`, so handlers do
`from shared import status, events, errors, clients`. No Lambda layer
resources.

## boto3 availability — CONFIRMED (Story 1.2)

boto3 ships in the floci runtime image (confirmed by the Story 1.2
fixture run, re-proven by every `tests/integration/` run of the deployed
functions). `shared.clients` uses boto3; the stdlib/urllib fallback was
not needed.

## Upload handler (Story 1.3)

`upload_handler/` (Terraform function name `upload-handler`, declared in
`terraform/upload.tf`) is the ingest leg behind the gateway route
`POST /videos/upload`:

1. Parses the multipart body with stdlib `email.parser`. The gateway
   delivers non-text bodies base64 with `isBase64Encoded: true` (matches
   real AWS); the handler decodes first, then parses raw bytes.
   Plain-text bodies are parsed as-is.
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

## Event publisher (Story 2.2)

`event_publisher/` (Terraform function name `event-publisher`, declared in
`terraform/processing.tf`) is the processing state machine's terminal task
and the **sole constructor** of the `video.processed` envelope (AD-4/AD-6):

1. Validates the domain payload: `videoId` + `originalKey` +
   `processedKey` required (missing/empty -> `MalformedInputError`); the
   ASL passes it the transcode result
   `{videoId, originalKey, processedKey, sizeBytes}`.
2. Builds the envelope via the shared layer:
   `events.build_envelope(EVENT_PROCESSED, processed_detail(...))` —
   deterministic UUID5 `eventId` of `(videoId, PROCESSED)`, `schemaVersion`,
   fixed detail shape. The detail's `bucket` comes from the
   `PROCESSED_BUCKET` env var, not the ASL (the ASL carries domain payload
   only).
3. Publishes exactly one entry on `video-bus` with the flat wire Detail
   (`{**envelope, **envelope["detail"]}`, mirroring the upload handler).
   `FailedEntryCount > 0` raises — a dropped terminal event must not
   masquerade as success.
4. Returns the envelope for the ASL result.

No DynamoDB access of any kind — the module does not import
`shared.status` and never constructs a DDB client (enforced by AST +
client-recorder tests). Error semantics mirror the transcode worker:
malformed payload raises `MalformedInputError`, a rejected entry raises
`RuntimeError`; either fails the invocation, which fails the ASL execution.

Config-not-code: `PROCESSED_BUCKET`, `EVENT_BUS_NAME`, `AWS_ENDPOINT_URL`
are all Terraform-set env vars. The role is least-privilege: logs +
`events:PutEvents` on `video-bus` only. Invoked only by the state machine
— no ad-hoc invoke needed (see the root README for `StartExecution`).

## sfn-trigger-shim (Story 2.3)

`sfn_trigger_shim/` (Terraform function name `sfn-trigger-shim`, declared
in `terraform/trigger.tf`) is the trigger leg's bridge: floci's
EventBridge cannot target Step Functions, so the `video.uploaded` rule
targets `processing-trigger-queue` (SQS) and this shim consumes it
(event-source mapping, batch_size=1), calling `StartExecution` with the
deterministic name `eb-{eventId}` and exactly the ASL input contract
`{videoId, status, bucket, key}` (whitespace-stripped). Dedupe:
`ExecutionAlreadyExists` is treated as success (acked) — the
deterministic name makes the collision the idempotency. Malformed
records are logged and acked (skipped); real errors raise so the ESM
retries. The module builds ONLY a `states` client — no DynamoDB, no S3,
no EventBridge (enforced by a client-recorder purity test).

Config-not-code: `STATE_MACHINE_ARN`, `AWS_ENDPOINT_URL` are
Terraform-set env vars. The role is least-privilege: logs +
`states:StartExecution` on the processing state machine + the standard
SQS event-source-mapping set on the trigger queue only.

## history-consumer (Story 3.1)

`history_consumer/` (Terraform function name `history-consumer`, declared
in `terraform/history.tf`) is the first `video.processed` consumer: the
`video-processed-to-history` rule targets `history-queue` (SQS) and this
consumer eats it (event-source mapping, batch_size=1). Per record it
unwraps `Records[].body` → EventBridge event → flat `detail`, validates
the `videoId` against `video-metadata` via `shared.status.get_record`,
and appends `{eventId, videoId, status, timestamp}` to `status-history`
with `ConditionExpression: attribute_not_exists(eventId)` — the condition
IS the dedupe (eventId is the deterministic UUID5 of (videoId, status)).
Poison handling: `NotFoundError` from the metadata lookup drops the event
(acked, never retried); any other error raises so the ESM retries.
Malformed records are logged and acked (skipped). The module builds ONLY
DynamoDB table handles — no S3, no EventBridge, no Step Functions, no SQS
(enforced by a client-recorder purity test).

Config-not-code: `METADATA_TABLE`, `HISTORY_TABLE`, `AWS_ENDPOINT_URL`
are Terraform-set env vars. The role is least-privilege: logs +
`dynamodb:GetItem` on `video-metadata` + `dynamodb:PutItem` on
`status-history` + the standard SQS event-source-mapping set on the
history queue only.

## history-query (Story 3.2)

`history_query/` (Terraform function name `history-query`, declared in
`terraform/history.tf`) is the second client journey: the gateway route
`GET /videos/{videoId}/history` (integration + route + scoped invoke
permission on the existing API Gateway v2 from `upload.tf`). Per request
it extracts `pathParameters.videoId` (missing/empty/non-string → 400 via
`MalformedInputError`), runs the 404 gate — `shared.status.get_record`
on `video-metadata`, `NotFoundError` → 404 `{"error": ...}`, no scan —
then reads `status-history` with a filtered `Scan`
(`FilterExpression videoId = :vid`; the table's PK is `eventId` only,
AD-3 binds the key schema, lab scale) and returns
`200 {"videoId", "entries": [{"status", "eventId", "timestamp"}, ...]}`
sorted by timestamp ascending. A KNOWN videoId with zero entries is
200 + empty entries, not 404 — the consumer leg is async, so "no
entries yet" is not "no video"; the Bruno poll-with-timeout depends on
exactly that distinction. The module builds ONLY DynamoDB table handles
— no S3, no EventBridge, no Step Functions, no SQS (enforced by a
client-recorder purity test).

Config-not-code: `METADATA_TABLE`, `HISTORY_TABLE`, `AWS_ENDPOINT_URL`
are Terraform-set env vars. The role is least-privilege: logs +
`dynamodb:GetItem` on `video-metadata` + `dynamodb:Scan` on
`status-history`.

## search-rebuild (Story 4.3)

`search_rebuild/` (Terraform function name `search-rebuild`, declared in
`terraform/search-rebuild.tf`) repopulates the derived, disposable
`search-index` table from `video-metadata` (FR-19/AD-3). It is an
ADMIN tool reachable ONLY by direct invoke: the Terraform file
deliberately contains no gateway integration/route/permission, no SQS
queue, no EventBridge rule/target, and no event-source mapping — the
admin-only constraint holds by structural absence, and a unit test
(`test_terraform_admin_only.py`) re-proves it on every run. The Bruno
collection does not expose it.

One pass: Scan `video-metadata` with FilterExpression `#s = :st` bound
to `status.PROCESSED` (selection in the query; single scan — pagination
out of scope per NFR-7 lab scale, truncation logged), then upsert each
hit as `{videoId, title, processedKey, indexedAt}` — the exact entry
shape search-consumer writes, all string fields whitespace-stripped.
The PK is the dedupe: re-invocation overwrites, never duplicates,
never deletes. Unusable records are skipped + logged, not fatal;
transient errors propagate raw — no HTTP mapping for a non-HTTP tool.

Config-not-code: `METADATA_TABLE`, `SEARCH_INDEX_TABLE`,
`AWS_ENDPOINT_URL` are Terraform-set env vars. The role is
least-privilege: logs + `dynamodb:Scan` on `video-metadata` +
`dynamodb:PutItem` on `search-index`.

Invoke ad-hoc after clearing the index (local boto3 — inspection/admin
only, never setup; same pattern as transcode):

```bash
python -c "import boto3, json; c = boto3.client('lambda', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test'); print(json.dumps(json.load(c.invoke(FunctionName='search-rebuild', Payload=json.dumps({}))['Payload']), indent=2))"
```

## Local tests

```bash
# either (requirements-dev.txt is the dev dependency list):
uv run --with pytest python -m pytest lambdas/ -q
# or install the dev requirements into a venv first:
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest lambdas/ -q
```

Suites: `lambdas/_shared/tests/` (31 shared-layer tests),
`lambdas/upload_handler/tests/` (27 upload-handler ATDD tests, activated
from the red-phase scaffolds — assertions unchanged from the TEA run),
`lambdas/transcode/tests/` (32 transcode-worker ATDD tests encoding the
Story 2.1 I/O matrix incl. the AD-4 purity guarantee),
`lambdas/event_publisher/tests/` (45 tests: the Story 2.2 I/O matrix incl.
the AD-4/AD-6 purity probes, plus `test_asl_definition.py` — the
ASL↔transition-table mirror backstop that parses
`terraform/processing.asl.json` and asserts its condition pairs against
`shared.status.LEGAL_TRANSITIONS`), and
`lambdas/sfn_trigger_shim/tests/` (43 tests: the Story 2.3 I/O matrix
incl. the dedupe ack, poison-record skip, and states-only purity probe),
and `lambdas/history_consumer/tests/` (54 tests: the Story 3.1 I/O matrix
incl. the conditional-write dedupe, poison drop vs transient retry, and
the dynamodb-only purity probe),
and `lambdas/history_query/tests/` (25 tests: the Story 3.2 I/O matrix
incl. the 404-gate-before-scan ordering, filtered-scan binding, timestamp
sort, entry projection, and the dynamodb-only purity probe).
Each `tests/conftest.py` registers the local `_shared/` directory as the
`shared` package so imports match the zip layout.
