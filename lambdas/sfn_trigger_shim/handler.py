"""sfn-trigger-shim Lambda — queue -> StartExecution (Story 2.3, AD-5).

floci's EventBridge cannot target Step Functions state machines, so the
trigger leg is: video.uploaded rule -> processing-trigger-queue (SQS) ->
this shim -> StartExecution.

For each SQS record the shim:

1. Parses `Records[].body` — the FULL EventBridge event delivered by the
   rule — and unwraps its `detail` (tolerating a JSON-stringified detail).
   The detail is the flat payload published by the upload handler:
   `{eventId, schemaVersion, videoId, status, bucket, key}`.
2. Validates the trigger: `eventId` (the deterministic UUID5 of
   (videoId, UPLOADED)) plus the ASL-required `videoId` and `key` must be
   present. Anything less is a malformed record: logged and acked
   (skipped) — never raised, never retried (no DLQ in v1; a deterministic
   poison message would retry forever).
3. Calls `StartExecution` with the deterministic execution name
   `eb-{eventId}` and EXACTLY the domain payload `{videoId, status,
   bucket, key}` — the ASL input contract frozen by Story 2.2. The
   eventId comes from `detail.eventId`, NEVER the EventBridge top-level
   `id` (random per emission on real AWS — would break dedupe).
4. Treats `ExecutionAlreadyExists` as success (dedupe ack, FR-9/NFR-1/2):
   eventId is restart-proof, so republish, SQS retry, and redelivery all
   derive the same execution name — the collision IS the idempotency.
   Any other StartExecution error raises so the ESM retries the message.

Returns a per-batch summary `{processed, started, deduped, skipped}`.

Config-not-code (NFR-4): the state machine ARN comes strictly from the
Terraform-set STATE_MACHINE_ARN env var; the endpoint from
AWS_ENDPOINT_URL (via shared.clients). The shim builds ONLY a Step
Functions client — no DynamoDB, no S3, no EventBridge access.
"""

import json
import logging
import os
import re

from shared import clients, events
from shared.errors import MalformedInputError, is_client_error_code

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Deterministic execution-name prefix (AD-5): eb-{eventId}.
EXECUTION_NAME_PREFIX = "eb-"

# The ASL input contract frozen by Story 2.2 — the shim passes exactly
# these fields (extras in the detail are dropped, keeping the contract
# explicit even though the ASL would tolerate them). Every one is
# REQUIRED: a partial input would start an execution that fails
# mid-flight with the record already acked — missing/empty -> malformed
# record -> skip.
_ASL_INPUT_FIELDS = ("videoId", "status", "bucket", "key")

# SFN execution names allow only [a-zA-Z0-9-_], max 80 chars (real AWS).
# An eventId violating this makes StartExecution raise InvalidName on
# every retry — a deterministic poison message; skip it instead.
_VALID_EXECUTION_NAME = re.compile(r"[A-Za-z0-9_-]+\Z")


# ---------------------------------------------------------------------------
# Client accessors — thin wrappers over shared.clients with the env-var
# names for THIS function (config-not-code, NFR-4). Module-level functions
# so tests can monkeypatch them (mirrors event_publisher/handler.py).
# ---------------------------------------------------------------------------

def _states_client():
    return clients.states_client()


def _state_machine_arn():
    arn = os.environ.get("STATE_MACHINE_ARN")
    if not arn:
        raise RuntimeError("STATE_MACHINE_ARN env var is not set")
    return arn


# ---------------------------------------------------------------------------
# Record parsing
# ---------------------------------------------------------------------------

def _is_execution_already_exists(exc):
    """Duck-typed via the shared layer: boto3 raises a dynamically
    generated ClientError subclass named after the error code, so the
    class name is the stable signal."""
    return is_client_error_code(exc, "ExecutionAlreadyExists")


def _process_record(record):
    """Process one SQS record. Returns 'started' | 'deduped' | 'skipped'.

    Malformed records are logged and acked (skipped). Real StartExecution
    errors raise so the ESM retries the message.
    """
    body = record.get("body") if isinstance(record, dict) else None
    try:
        body_obj = json.loads(body) if isinstance(body, str) else None
    except ValueError:
        body_obj = None

    detail = events.parse_detail(body_obj)
    if detail is None:
        logger.warning(
            "skipping malformed record: no parseable EventBridge detail")
        return "skipped"

    event_id = detail.get("eventId")
    if not isinstance(event_id, str) or not event_id.strip():
        logger.warning("skipping malformed record: detail has no eventId")
        return "skipped"

    execution_name = f"{EXECUTION_NAME_PREFIX}{event_id.strip()}"
    if len(execution_name) > 80 or not _VALID_EXECUTION_NAME.match(
            execution_name):
        logger.warning(
            "skipping malformed record: eventId unusable as execution "
            "name")
        return "skipped"

    missing = [
        name for name in _ASL_INPUT_FIELDS
        if not isinstance(detail.get(name), str) or not detail[name].strip()
    ]
    if missing:
        logger.warning(
            "skipping malformed record: missing or empty fields %s", missing)
        return "skipped"

    # Strip the values too (not just validate them): a whitespace-padded
    # field would otherwise start an execution that fails mid-flight at
    # MarkProcessing's DynamoDB Key match with the record already acked.
    # Same normalization as the transcode/publisher validators.
    asl_input = {name: detail[name].strip() for name in _ASL_INPUT_FIELDS}

    try:
        _states_client().start_execution(
            stateMachineArn=_state_machine_arn(),
            name=execution_name,
            input=json.dumps(asl_input),
        )
    except Exception as exc:  # noqa: BLE001 - dedupe check decides fate
        if _is_execution_already_exists(exc):
            # FR-9/NFR-1/2: same eventId -> same execution name -> the
            # collision IS the idempotency. Ack it.
            logger.info(
                "execution already exists — dedupe videoId=%s eventId=%s "
                "executionName=%s",
                detail.get("videoId"), event_id, execution_name)
            return "deduped"
        raise

    logger.info(
        "execution started videoId=%s eventId=%s executionName=%s",
        detail.get("videoId"), event_id, execution_name)
    return "started"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handler(event, context):  # noqa: ARG001 - Lambda signature
    if not isinstance(event, dict) or not isinstance(
            event.get("Records"), list):
        raise MalformedInputError(
            "expected an SQS event with a Records list")

    outcomes = [_process_record(record) for record in event["Records"]]

    summary = {
        "processed": len(outcomes),
        "started": outcomes.count("started"),
        "deduped": outcomes.count("deduped"),
        "skipped": outcomes.count("skipped"),
    }
    logger.info("batch summary %s", summary)
    return summary
