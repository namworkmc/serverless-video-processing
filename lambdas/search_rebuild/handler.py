"""search-rebuild Lambda — repopulates search-index from video-metadata
(Story 4.3, FR-19, AD-3, NFR-1/NFR-4).

The index is derived and disposable (AD-3): Story 4.1's consumer only
indexes events flowing after the fact, so a lost/cleared search-index
needs a way back. This function IS that way — an ADMIN tool reachable
ONLY by direct invoke (local boto3 / floci's Lambda REST). It has no
gateway route, no queue, no rule, no event-source mapping: the
admin-only constraint is structural (terraform/search-rebuild.tf), not
convention. The event payload of the invocation is ignored entirely.

One pass:

1. Scans METADATA_TABLE with FilterExpression `#s = :st` bound to
   shared.status.PROCESSED — selection happens IN THE QUERY, so
   UPLOADED / PROCESSING / FAILED records are never even returned
   (FR-19's PROCESSED-only consequence, mirroring FR-17).
2. Upserts each hit into SEARCH_INDEX_TABLE as exactly
   `{videoId, title(stripped), processedKey(stripped), indexedAt}` —
   the entry shape Story 4.1 writes (all three string fields
   whitespace-stripped, same field-validation parity as the consumer),
   so search-query cannot tell a rebuilt entry from a consumed one.
   Plain PutItem keyed by videoId: the PK IS the dedupe — re-invocation
   overwrites, never duplicates, never deletes. `indexedAt` is stamped
   at rebuild time: the entry describes the projection state.
3. A PROCESSED record without a usable string title or processedKey is
   counted `skipped`, logged, and does NOT abort the rebuild — one
   corrupt record must not take down an admin batch job.

Returns the summary `{"scanned", "indexed", "skipped"}` for the direct-
invoke caller; nothing else is promised.

Deliberately NO shared.errors import and NO map_error tail: unlike the
query lambdas this function has no HTTP contract. Transient scan/put
errors propagate raw — the invoke caller sees FunctionError, which is
the correct loud failure for an admin tool.

Config-not-code (NFR-4): table names come strictly from the Terraform-
set METADATA_TABLE / SEARCH_INDEX_TABLE env vars; the endpoint from
AWS_ENDPOINT_URL (via shared.clients). The module builds ONLY DynamoDB
table handles — no S3, no events, no states, no SQS (purity-probed).
"""

import logging
import os

from shared import clients, status

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Client accessors — thin wrappers over shared.clients with the env-var
# names for THIS function (config-not-code, NFR-4). Module-level functions
# so tests can monkeypatch them (mirrors search_consumer/handler.py).
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


def _rebuild(metadata, index):
    scanned = indexed = skipped = 0

    # Selection IN the query: #s = :st bound to the shared constant.
    resp = metadata.scan(
        FilterExpression="#s = :st",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":st": status.PROCESSED},
    )
    items = resp.get("Items", [])
    scanned = len(items)
    # Pagination is deliberately out of scope (NFR-7 lab scale): a
    # truncated Scan would silently rebuild a PARTIAL index, so at least
    # say so loudly. Single-scan semantics stay.
    if resp.get("LastEvaluatedKey"):
        logger.warning(
            "metadata scan truncated after %d items (LastEvaluatedKey "
            "present) — rebuilt index may be partial; pagination is out "
            "of scope per NFR-7 lab scale", scanned)

    for item in items:
        video_id = item.get("videoId")
        title = item.get("title")
        processed_key = item.get("processedKey")

        # One corrupt record must not take down an admin batch job:
        # skip it, keep rebuilding the rest. Field validation mirrors
        # search_consumer/handler.py: non-string or blank-after-strip
        # is unusable.
        if not isinstance(video_id, str) or not video_id.strip():
            logger.warning(
                "skipping unusable PROCESSED record with bad videoId")
            skipped += 1
            continue
        video_id = video_id.strip()
        if not isinstance(title, str) or not title.strip():
            logger.warning(
                "skipping unusable PROCESSED record "
                "videoId=%s: bad title", video_id)
            skipped += 1
            continue
        if not isinstance(processed_key, str) or not processed_key.strip():
            logger.warning(
                "skipping unusable PROCESSED record "
                "videoId=%s: bad processedKey", video_id)
            skipped += 1
            continue
        processed_key = processed_key.strip()

        # Plain PutItem keyed by videoId — the PK IS the dedupe:
        # re-invocation overwrites the same PK, never duplicates,
        # never deletes. Deliberately NO ConditionExpression.
        index.put_item(
            Item={
                "videoId": video_id,
                "title": title.strip(),
                "processedKey": processed_key,
                "indexedAt": status._now_iso(),
            },
        )
        indexed += 1

    return {"scanned": scanned, "indexed": indexed, "skipped": skipped}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handler(event, context):  # noqa: ARG001 - Lambda signature; the
    # invocation payload is ignored entirely — this is a fire-and-report
    # admin batch job, not an event consumer.
    summary = _rebuild(_metadata_table(), _index_table())
    logger.info(
        "rebuild summary scanned=%d indexed=%d skipped=%d",
        summary["scanned"], summary["indexed"], summary["skipped"])
    return summary
