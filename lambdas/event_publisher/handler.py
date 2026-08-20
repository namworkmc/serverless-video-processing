"""Event-publisher Lambda — sole constructor of the video.processed
envelope (Story 2.2, AD-4/AD-6).

The processing state machine's terminal task. The ASL passes it only the
domain payload — the transcode result `{videoId, originalKey,
processedKey, sizeBytes}` — and this handler:

1. Validates the domain payload: `videoId` + `originalKey` +
   `processedKey` are required; anything else is tolerated. Missing/empty
   required fields are malformed input -> `MalformedInputError`.
2. Builds the `video.processed` envelope via the shared layer (AD-6):
   `events.build_envelope(EVENT_PROCESSED, processed_detail(...))`. The
   eventId is the deterministic UUID5 of (videoId, PROCESSED) — a
   republish is a dedupe, never a new id (NFR-2). The detail's `bucket`
   comes from the Terraform-set PROCESSED_BUCKET env var (config-not-code,
   NFR-4), NOT from the ASL — the ASL carries domain payload only (AD-4).
3. Publishes exactly one entry on the custom bus. Wire Detail mirrors the
   upload handler's flat shape: `{**envelope, **envelope["detail"]}` so
   consumers can read {eventId, schemaVersion, videoId, status, bucket,
   originalKey, processedKey} flat while the nested envelope detail stays
   intact. `FailedEntryCount > 0` raises — a dropped terminal event must
   not masquerade as success.
4. Returns the envelope for the ASL result.

SOLE CONSTRUCTOR (AD-4/AD-6): this is the only code that builds the
video.processed envelope. It performs NO DynamoDB access of any kind —
this module does not import `shared.status` and never constructs a
DynamoDB client. Error semantics: malformed payload raises
`MalformedInputError`; a rejected entry raises `RuntimeError`. Either way
the invocation fails, which is exactly what the ASL task needs (the
execution fails).

Config-not-code (NFR-4): bucket and bus names come strictly from the
Terraform-set env vars PROCESSED_BUCKET / EVENT_BUS_NAME; the endpoint
from AWS_ENDPOINT_URL (via shared.clients).
"""

import json
import logging
import os

from shared import clients, events
from shared.errors import MalformedInputError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# EventBridge source for events this handler publishes.
EVENT_SOURCE = "event-publisher"


# ---------------------------------------------------------------------------
# Client accessors — thin wrappers over shared.clients with the env-var
# names for THIS function (config-not-code, NFR-4). Module-level functions
# so tests can monkeypatch them (mirrors transcode/handler.py).
# ---------------------------------------------------------------------------

def _events_client():
    return clients.events_client()


def _processed_bucket():
    bucket = os.environ.get("PROCESSED_BUCKET")
    if not bucket:
        raise RuntimeError("PROCESSED_BUCKET env var is not set")
    return bucket


def _event_bus_name():
    bus = os.environ.get("EVENT_BUS_NAME")
    if not bus:
        raise RuntimeError("EVENT_BUS_NAME env var is not set")
    return bus


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

def _require_field(event, name):
    """Return a non-empty string field or raise MalformedInputError.

    Returns the STRIPPED value so whitespace-padded fields cannot leak
    into the event detail.
    """
    value = event.get(name) if isinstance(event, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise MalformedInputError(f"missing or empty required field: {name}")
    return value.strip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handler(event, context):  # noqa: ARG001 - Lambda signature
    video_id = _require_field(event, "videoId")
    original_key = _require_field(event, "originalKey")
    processed_key = _require_field(event, "processedKey")

    # Sole constructor of the video.processed envelope (AD-6): detail shape
    # fixed by the shared layer; bucket from env (config-not-code, NFR-4).
    envelope = events.build_envelope(
        events.EVENT_PROCESSED,
        events.processed_detail(
            video_id, _processed_bucket(), original_key, processed_key),
    )

    # Wire Detail: the envelope (eventId + schemaVersion) with the detail
    # fields promoted to the top level — mirrors upload_handler's flat
    # shape so consumers can read both views.
    detail_payload = {**envelope, **envelope["detail"]}
    put_resp = _events_client().put_events(Entries=[{
        "Source": EVENT_SOURCE,
        "DetailType": events.EVENT_PROCESSED,
        "Detail": json.dumps(detail_payload),
        "EventBusName": _event_bus_name(),
    }])
    # EventBridge returns HTTP 200 even when individual entries fail; a
    # dropped video.processed must not masquerade as success.
    if put_resp.get("FailedEntryCount"):
        raise RuntimeError(
            f"event publish failed: {put_resp.get('FailedEntryCount')} "
            "entries rejected")

    logger.info(
        "video.processed published videoId=%s eventId=%s processedKey=%s",
        video_id, envelope["eventId"], processed_key)

    # Domain result for the ASL — the terminal task's output.
    return envelope
