"""history-consumer Lambda — video.processed -> status-history (Story 3.1,
AD-1/AD-3/AD-6, FR-14/FR-15).

The history leg: video.processed rule -> history-queue (SQS) -> this
consumer -> one status-history entry per unique eventId.

For each SQS record the consumer:

1. Parses `Records[].body` — the FULL EventBridge event delivered by the
   rule — and unwraps its `detail` (tolerating a JSON-stringified detail).
   The detail is the flat payload published by the event-publisher:
   `{eventId, schemaVersion, videoId, status, bucket, originalKey,
   processedKey, detail}` (AD-6 as-built wire shape).
2. Validates the record: `eventId`, `videoId`, and `status` must be
   present non-empty strings, and `status` must be a legal status
   (`shared.status.STATUSES`). Anything less is a malformed record:
   logged and acked (skipped) — never raised, never retried (no DLQ in
   v1; a deterministic poison message would retry forever).
3. Validates the videoId against `video-metadata` via the shared layer
   (FR-15): `status.get_record` raising NotFoundError is a SUCCESSFUL
   NEGATIVE LOOKUP — the event is poison, dropped (logged + acked, never
   retried). Any other error raises so the ESM retries the message.
4. Appends the history entry `{eventId, videoId, status, timestamp}`
   with `ConditionExpression: attribute_not_exists(eventId)` — the
   condition IS the dedupe (FR-14, NFR-1): eventId is the deterministic
   UUID5 of (videoId, status), restart-proof, so republish, SQS retry,
   and redelivery all collide on the same key. ConditionalCheckFailed is
   logged as a dedupe and acked. Any other write error raises.

Returns a per-batch summary `{processed, recorded, deduped, dropped,
skipped}`.

Config-not-code (NFR-4): table names come strictly from the Terraform-set
METADATA_TABLE / HISTORY_TABLE env vars; the endpoint from
AWS_ENDPOINT_URL (via shared.clients). The consumer builds ONLY DynamoDB
table handles — no S3, no EventBridge, no Step Functions, no SQS access.
"""

import json
import logging
import os

from shared import clients, status
from shared.errors import (MalformedInputError, NotFoundError,
                           is_conditional_check_failed)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Fields the record must carry to be processable. Missing/empty ->
# malformed record -> skip (same policy as the sfn-trigger-shim).
_REQUIRED_FIELDS = ("eventId", "videoId", "status")


# ---------------------------------------------------------------------------
# Client accessors — thin wrappers over shared.clients with the env-var
# names for THIS function (config-not-code, NFR-4). Module-level functions
# so tests can monkeypatch them (mirrors sfn_trigger_shim/handler.py).
# ---------------------------------------------------------------------------

def _metadata_table():
    name = os.environ.get("METADATA_TABLE")
    if not name:
        raise RuntimeError("METADATA_TABLE env var is not set")
    return clients.dynamodb_table(name)


def _history_table():
    name = os.environ.get("HISTORY_TABLE")
    if not name:
        raise RuntimeError("HISTORY_TABLE env var is not set")
    return clients.dynamodb_table(name)


# ---------------------------------------------------------------------------
# Record parsing
# ---------------------------------------------------------------------------

def _parse_detail(body_obj):
    """Return the EventBridge event's detail as a dict, or None if the
    body is not a parseable EventBridge event. Tolerates a detail that
    arrives JSON-stringified (mirrors sfn_trigger_shim)."""
    if not isinstance(body_obj, dict):
        return None
    detail = body_obj.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return None
    return detail if isinstance(detail, dict) else None


def _process_record(record):
    """Process one SQS record.

    Returns 'recorded' | 'deduped' | 'dropped' | 'skipped'.
    Malformed records are logged and acked (skipped). Poison events
    (unknown videoId) are dropped and acked (FR-15). Transient errors
    raise so the ESM retries the message (FR-15).
    """
    body = record.get("body") if isinstance(record, dict) else None
    try:
        body_obj = json.loads(body) if isinstance(body, str) else None
    except ValueError:
        body_obj = None

    detail = _parse_detail(body_obj)
    if detail is None:
        logger.warning(
            "skipping malformed record: no parseable EventBridge detail")
        return "skipped"

    fields = {}
    for name in _REQUIRED_FIELDS:
        value = detail.get(name)
        if not isinstance(value, str) or not value.strip():
            logger.warning(
                "skipping malformed record: missing or empty field %s", name)
            return "skipped"
        fields[name] = value.strip()

    event_id = fields["eventId"]
    video_id = fields["videoId"]
    record_status = fields["status"]

    # Status validation: only legal statuses enter the audit trail. A
    # fabricated event with a known videoId but an arbitrary status string
    # is malformed — skipped, not persisted (tightens the malformed-record
    # row of the I/O matrix).
    if record_status not in status.STATUSES:
        logger.warning(
            "skipping malformed record: unknown status %r", record_status)
        return "skipped"

    # Poison detection (FR-15): NotFoundError is a successful negative
    # lookup — drop the event, ack it, never retry. Any other error is
    # transient and must raise so SQS redelivers.
    try:
        status.get_record(_metadata_table(), video_id)
    except NotFoundError:
        logger.warning(
            "dropping poison event: unknown videoId=%s eventId=%s",
            video_id, event_id)
        return "dropped"

    # Dedupe is the write, not a read-then-write (FR-14, NFR-1): the
    # table rejects a second entry for the same eventId.
    try:
        _history_table().put_item(
            Item={
                "eventId": event_id,
                "videoId": video_id,
                "status": record_status,
                "timestamp": status._now_iso(),
            },
            ConditionExpression="attribute_not_exists(eventId)",
        )
    except Exception as exc:  # noqa: BLE001 - dedupe check decides fate
        if is_conditional_check_failed(exc):
            logger.info(
                "duplicate eventId — dedupe videoId=%s eventId=%s",
                video_id, event_id)
            return "deduped"
        raise

    logger.info(
        "history recorded videoId=%s eventId=%s status=%s",
        video_id, event_id, record_status)
    return "recorded"


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
        "recorded": outcomes.count("recorded"),
        "deduped": outcomes.count("deduped"),
        "dropped": outcomes.count("dropped"),
        "skipped": outcomes.count("skipped"),
    }
    logger.info("batch summary %s", summary)
    return summary
