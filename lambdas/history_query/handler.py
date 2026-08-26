"""history-query Lambda — the second client journey (Story 3.2, FR-16).

GET /videos/{videoId}/history (API Gateway v2, AWS_PROXY) -> this handler:

1. 404 gate BEFORE the history read: `status.get_record` on
   video-metadata — unknown videoId raises NotFoundError -> 404
   {"error": ...} (FR-13, NFR-3). A KNOWN videoId with zero entries
   returns 200 with empty entries — the consumer leg is async, so
   "no entries yet" is not "no video".
2. History read = Scan with FilterExpression videoId = :vid —
   status-history's PK is eventId only (AD-3 binds the key schema; no
   GSI, lab scale, NFR-7).
3. Entries sorted by timestamp ascending (ISO-8601 sorts
   lexicographically), projected to exactly {status, eventId,
   timestamp} — no metadata fields leak into the response.

Config-not-code (NFR-4): table names come strictly from the Terraform-set
METADATA_TABLE / HISTORY_TABLE env vars; the endpoint from
AWS_ENDPOINT_URL (via shared.clients). Builds ONLY DynamoDB table
handles.
"""

import json
import logging
import os

from shared import clients, status
from shared.errors import MalformedInputError, map_error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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


def _history_table():
    name = os.environ.get("HISTORY_TABLE")
    if not name:
        raise RuntimeError("HISTORY_TABLE env var is not set")
    return clients.dynamodb_table(name)


def _video_id(event):
    """Extract pathParameters.videoId from an API GW v2 payload or raise
    MalformedInputError (400) for missing/empty/non-string values."""
    params = event.get("pathParameters") if isinstance(event, dict) else None
    video_id = params.get("videoId") if isinstance(params, dict) else None
    if not isinstance(video_id, str) or not video_id.strip():
        raise MalformedInputError("missing or empty path parameter: videoId")
    return video_id.strip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handler(event, context):  # noqa: ARG001 - Lambda signature
    try:
        video_id = _video_id(event)

        # 404 gate first: unknown videoId fails fast, no scan performed.
        status.get_record(_metadata_table(), video_id)

        # ponytail: full-table Scan with a filter — status-history is keyed
        # by eventId only (AD-3) and lab-scale; a GSI is the upgrade path
        # if this table ever outgrows a scan (NFR-7 defers it).
        resp = _history_table().scan(
            FilterExpression="videoId = :vid",
            ExpressionAttributeValues={":vid": video_id},
        )
        # Pagination is deliberately out of scope (NFR-7 lab scale): a
        # truncated Scan silently drops later pages, so at least say so
        # loudly (mirrors search_rebuild/handler.py). Single-scan stays.
        if resp.get("LastEvaluatedKey"):
            logger.warning(
                "status-history scan truncated after %d items "
                "(LastEvaluatedKey present) — entries may be partial; "
                "pagination is out of scope per NFR-7 lab scale",
                len(resp.get("Items", [])))
        entries = sorted(
            (
                {"status": item["status"], "eventId": item["eventId"],
                 "timestamp": item["timestamp"]}
                for item in resp.get("Items", [])
            ),
            key=lambda entry: entry["timestamp"],
        )

        logger.info("history query videoId=%s entries=%d",
                    video_id, len(entries))
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"videoId": video_id, "entries": entries}),
        }
    except Exception as exc:  # noqa: BLE001 - map_error decides the status
        http_status, body = map_error(exc)
        logger.warning("history query rejected status=%d error=%s",
                       http_status, body.get("error"))
        return {
            "statusCode": http_status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body),
        }
