"""search-query Lambda — the third client journey (Story 4.2, FR-18).

GET /videos/search?title= (API Gateway v2, AWS_PROXY) -> this handler:

1. Validates the `title` query parameter: missing, empty, whitespace-only,
   or non-string raises MalformedInputError -> 400 {"error": ...} BEFORE
   any table access (NFR-3). The value is stripped once, so whitespace-
   padded queries cannot leak into the expression or the response body.
   Route keys carry no query string — API GW v2 delivers it via
   queryStringParameters.
2. Search read = plain table Scan with FilterExpression
   contains(title, :t) — case-sensitive substring, sanctioned at lab
   scale (AD-3/NFR-7; no GSI, no pagination plumbing).
3. No match is SUCCESS: zero hits -> 200 with an empty results list,
   never an error. Results are projected to exactly {videoId, title,
   processedKey, indexedAt} (the Story 4.1 entry shape — no internal
   fields) and sorted by videoId ascending so live/Bruno assertions are
   order-stable.

Config-not-code (NFR-4): the table name comes strictly from the
Terraform-set SEARCH_INDEX_TABLE env var; the endpoint from
AWS_ENDPOINT_URL (via shared.clients). Builds ONLY DynamoDB table
handles. Request-invoked behind the gateway — no queue, EventBridge
rule, or event-source mapping exists for this function by design.
"""

import json
import logging
import os

from shared import clients, errors

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Client accessor — thin wrapper over shared.clients with the env-var name
# for THIS function (config-not-code, NFR-4). Module-level function so tests
# can monkeypatch it (mirrors history_query/handler.py).
# ---------------------------------------------------------------------------

def _index_table():
    name = os.environ.get("SEARCH_INDEX_TABLE")
    if not name:
        raise RuntimeError("SEARCH_INDEX_TABLE env var is not set")
    return clients.dynamodb_table(name)


def _title(event):
    """Extract queryStringParameters.title from an API GW v2 payload or
    raise MalformedInputError (400) for missing/empty/non-string values."""
    params = event.get("queryStringParameters") if isinstance(event, dict) else None
    title = params.get("title") if isinstance(params, dict) else None
    if not isinstance(title, str) or not title.strip():
        raise errors.MalformedInputError("missing or empty query parameter: title")
    return title.strip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handler(event, context):  # noqa: ARG001 - Lambda signature
    try:
        title = _title(event)

        # ponytail: full-table Scan with a contains filter — title is not a
        # key attribute and the index is lab-scale; a GSI is the upgrade
        # path if this table ever outgrows a scan (NFR-7 defers it).
        resp = _index_table().scan(
            FilterExpression="contains(title, :t)",
            ExpressionAttributeValues={":t": title},
        )
        results = sorted(
            (
                {"videoId": item["videoId"], "title": item["title"],
                 "processedKey": item["processedKey"],
                 "indexedAt": item["indexedAt"]}
                for item in resp.get("Items", [])
            ),
            key=lambda entry: entry["videoId"],
        )

        logger.info("search query title=%r results=%d", title, len(results))
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"title": title, "results": results}),
        }
    except Exception as exc:  # noqa: BLE001 - map_error decides the status
        http_status, body = errors.map_error(exc)
        logger.warning("search query rejected status=%d error=%s",
                       http_status, body.get("error"))
        return {
            "statusCode": http_status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body),
        }
