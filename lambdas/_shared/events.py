"""Deterministic event envelopes (NFR-2).

eventId is a name-based UUID5 of (videoId, status) under a fixed namespace:
identical across calls, processes, and restarts. A republish is therefore a
dedupe, never a new id.

Envelope contract (consumed unchanged downstream — shim -> ASL -> transcode
-> publisher -> consumers):
    {"eventId": <uuid5>, "schemaVersion": "1", "detail": {...}}
Event names are verb-in-past and carried by the transport (EventBridge
detail-type), not inside the envelope.

The detail payload shapes are FIXED here (AD-6):
    video.uploaded  -> {videoId, status, bucket, key}
    video.processed -> {videoId, status, bucket, originalKey, processedKey}
"""

import json
import uuid

SCHEMA_VERSION = "1"

# Fixed namespace for eventId derivation. Changing this changes every
# eventId in the system — never change it.
EVENT_ID_NAMESPACE = uuid.UUID("99881bbf-05eb-5ec6-8f3a-490d7496e518")

EVENT_UPLOADED = "video.uploaded"
EVENT_PROCESSED = "video.processed"

# Known event names and the detail status each one must carry. The layer
# fixes these shapes (AD-6); a mismatch is malformed input, not a silent
# envelope.
_EVENT_STATUS = {
    EVENT_UPLOADED: "UPLOADED",
    EVENT_PROCESSED: "PROCESSED",
}


def event_id(video_id, status):
    """Deterministic eventId for (videoId, status) — restart-proof."""
    return str(uuid.uuid5(EVENT_ID_NAMESPACE, f"{video_id}:{status}"))


def build_envelope(name, detail):
    """Build an envelope: eventId + schemaVersion + detail.

    The eventId is derived from the detail's (videoId, status), so the
    envelope is fully determined by its domain payload. `name` must be a
    known event name and must agree with the detail's status.
    """
    from shared.errors import MalformedInputError
    expected_status = _EVENT_STATUS.get(name)
    if expected_status is None:
        raise MalformedInputError(f"unknown event name: {name}")
    video_id = detail.get("videoId")
    status = detail.get("status")
    if not video_id or not status:
        raise MalformedInputError(
            "detail must carry videoId and status to derive eventId")
    if status != expected_status:
        raise MalformedInputError(
            f"event {name} requires status {expected_status}, got {status}")
    return {
        "eventId": event_id(video_id, status),
        "schemaVersion": SCHEMA_VERSION,
        "detail": dict(detail),
    }


def uploaded_detail(video_id, bucket, key):
    """Fixed detail shape for video.uploaded."""
    return {
        "videoId": video_id,
        "status": "UPLOADED",
        "bucket": bucket,
        "key": key,
    }


def processed_detail(video_id, bucket, original_key, processed_key):
    """Fixed detail shape for video.processed."""
    return {
        "videoId": video_id,
        "status": "PROCESSED",
        "bucket": bucket,
        "originalKey": original_key,
        "processedKey": processed_key,
    }


def parse_detail(body_obj):
    """Consumer-side counterpart to build_envelope: return the EventBridge
    event's detail as a dict, or None if the body is not a parseable
    EventBridge event. Tolerates a detail that arrives JSON-stringified
    (the exact floci encoding is confirmed by the live AC run).

    Every SQS consumer unwraps Records[].body -> envelope -> detail
    through this one helper — one definition, no per-consumer copies.
    """
    if not isinstance(body_obj, dict):
        return None
    detail = body_obj.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return None
    return detail if isinstance(detail, dict) else None
