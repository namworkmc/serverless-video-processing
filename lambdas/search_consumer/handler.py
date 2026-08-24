"""search-consumer Lambda — video.processed -> search-index (Story 4.1,
FR-17, AD-1/AD-3/AD-6, NFR-1).

The search leg: video.processed rule -> search-queue (SQS) -> this
consumer -> one search-index entry per PROCESSED event, upserted by
videoId. This is the index Story 4.2's title search reads.

For each SQS record the consumer:

1. Parses `Records[].body` — the FULL EventBridge event delivered by the
   rule — and unwraps its `detail` via the shared layer's parse_detail
   (tolerating a JSON-stringified detail). The detail is the flat payload
   published by the event-publisher: `{eventId, schemaVersion, videoId,
   status, bucket, originalKey, processedKey}` (AD-6 as-built wire shape).
2. Validates the record: `eventId`, `videoId`, `status`, and
   `processedKey` must be present non-empty strings. Anything less is a
   malformed record: logged and acked (skipped) — never raised, never
   retried (no DLQ in v1; a deterministic poison message would retry
   forever).
3. Status filter BEFORE any table access: only `status == PROCESSED`
   events are indexed. FAILED / UPLOADED / PROCESSING events — and
   unknown status strings, which are equally a status decision made
   before any legality concern — are filtered (logged + acked). FR-17's
   core promise: FAILED is never indexed.
4. Validates the videoId against `video-metadata` (FR-15) and takes the
   entry's `title` from that record (AD-6: the event detail carries no
   title): `status.get_record` raising NotFoundError is a SUCCESSFUL
   NEGATIVE LOOKUP — poison, dropped (logged + acked, never retried).
   A record without a usable string title is equally deterministic
   poison (dropped). Any other error raises so the ESM retries the
   message.
5. Upserts the entry `{videoId, title, processedKey, indexedAt}` with a
   plain PutItem keyed by videoId — the PK IS the dedupe (NFR-1):
   redelivery overwrites, no duplicates, no ConditionExpression. Any
   other write error raises.

Returns a per-batch summary `{processed, indexed, filtered, dropped,
skipped}`.

Config-not-code (NFR-4): table names come strictly from the Terraform-set
METADATA_TABLE / SEARCH_INDEX_TABLE env vars; the endpoint from
AWS_ENDPOINT_URL (via shared.clients). The consumer builds ONLY DynamoDB
table handles — no S3, no EventBridge, no Step Functions, no SQS access.
"""

import json
import logging
import os

from shared import clients, events, status
from shared.errors import MalformedInputError, NotFoundError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Fields the record must carry to be processable. Missing/empty ->
# malformed record -> skip (same policy as the history-consumer).
_REQUIRED_FIELDS = ("eventId", "videoId", "status", "processedKey")


# ---------------------------------------------------------------------------
# Client accessors — thin wrappers over shared.clients with the env-var
# names for THIS function (config-not-code, NFR-4). Module-level functions
# so tests can monkeypatch them (mirrors history_consumer/handler.py).
# ---------------------------------------------------------------------------

def _metadata_table():
    name = os.environ.get("METADATA_TABLE")
    if not name:
        raise RuntimeError("METADATA_TABLE env var is not set")
    return clients.dynamodb_table(name)


def _index_table():
    name = os.environ.get("SEARCH_INDEX_TABLE")
    if not name:
        raise RuntimeError("SEARCH_INDEX_TABLE env var is not set")
    return clients.dynamodb_table(name)


# ---------------------------------------------------------------------------
# Record parsing
# ---------------------------------------------------------------------------

def _process_record(record):
    """Process one SQS record.

    Returns 'indexed' | 'filtered' | 'dropped' | 'skipped'.
    Malformed records are logged and acked (skipped). Non-PROCESSED
    statuses are filtered before any table access. Poison events
    (unknown videoId) are dropped and acked (FR-15). Transient errors
    raise so the ESM retries the message (FR-15).
    """
    body = record.get("body") if isinstance(record, dict) else None
    try:
        body_obj = json.loads(body) if isinstance(body, str) else None
    except (ValueError, RecursionError):
        body_obj = None

    # parse_detail tolerates a JSON-stringified detail, but a deeply
    # nested string can raise RecursionError inside it — same malformed-
    # record policy as the body parse above (skipped, never retried).
    try:
        detail = events.parse_detail(body_obj)
    except (ValueError, RecursionError):
        detail = None
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

    # Status filter FIRST (before the metadata lookup): only PROCESSED
    # events are indexed. An unknown status string is also a status
    # decision -> filtered, not skipped.
    if fields["status"] != status.PROCESSED:
        logger.info(
            "filtering non-PROCESSED event videoId=%s eventId=%s status=%s",
            video_id, event_id, fields["status"])
        return "filtered"

    # Poison validation doubles as the title source (FR-15/FR-17/AD-6):
    # NotFoundError is a successful negative lookup — drop, ack, never
    # retry. Any other error is transient and must raise for SQS retry.
    try:
        metadata = status.get_record(_metadata_table(), video_id)
    except NotFoundError:
        logger.warning(
            "dropping poison event: unknown videoId=%s eventId=%s",
            video_id, event_id)
        return "dropped"

    # A record without a usable title cannot yield a valid index entry —
    # deterministic poison under the same no-DLQ policy as above (review
    # loop 1): drop + ack, never retry.
    title = metadata.get("title")
    if not isinstance(title, str) or not title.strip():
        logger.warning(
            "dropping event with unusable metadata title "
            "videoId=%s eventId=%s", video_id, event_id)
        return "dropped"

    # Upsert is the dedupe (NFR-1): plain PutItem keyed by videoId —
    # redelivery overwrites the same PK, never duplicates. Deliberately
    # NO ConditionExpression (unlike the history consumer's append).
    _index_table().put_item(
        Item={
            "videoId": video_id,
            "title": title.strip(),
            "processedKey": fields["processedKey"],
            "indexedAt": status._now_iso(),
        },
    )

    logger.info(
        "search indexed videoId=%s eventId=%s processedKey=%s",
        video_id, event_id, fields["processedKey"])
    return "indexed"


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
        "indexed": outcomes.count("indexed"),
        "filtered": outcomes.count("filtered"),
        "dropped": outcomes.count("dropped"),
        "skipped": outcomes.count("skipped"),
    }
    logger.info("batch summary %s", summary)
    return summary
