"""Transcode worker Lambda — pure S3 in -> S3 out (Story 2.1, AD-4).

The processing state machine's first worker (invoked by Story 2.2's ASL):

1. Validate the domain payload: `videoId` + `originalKey` are required;
   anything else in the event is tolerated (the ASL passes the full
   `video.uploaded` detail unchanged). Missing/empty required fields are
   malformed input -> `MalformedInputError`.
2. Demo-mode transcode: stream the object body from `video-uploads` to
   `video-processed` (no ffmpeg — real transcoding is a documented future
   extension). The processed key is tied to the same videoId (FR-6):
   `processed/{videoId}/{basename}` where basename is the last path
   segment of `originalKey` (upload key shape is `{videoId}/{filename}`).
3. Return the domain payload for the ASL result:
   `{videoId, originalKey, processedKey, sizeBytes}` — Story 2.2 feeds
   this to the event-publisher task.

PURE WORKER (AD-4): no DynamoDB writes, no event publishing — this module
does not even import `shared.status` or `shared.events`. Error semantics:
malformed payload raises `MalformedInputError`; S3 failures propagate raw.
Either way the invocation fails, which is exactly what the ASL task needs
(Story 2.2 fails the execution).

Config-not-code (NFR-4): bucket names come strictly from the Terraform-set
env vars UPLOADS_BUCKET / PROCESSED_BUCKET; the endpoint from
AWS_ENDPOINT_URL (via shared.clients).
"""

import logging
import os

from shared import clients
from shared.errors import MalformedInputError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Client accessors — thin wrappers over shared.clients with the env-var
# names for THIS function (config-not-code, NFR-4). Module-level functions
# so tests can monkeypatch them (mirrors upload_handler/handler.py).
# ---------------------------------------------------------------------------

def _s3_client():
    return clients.s3_client()


def _uploads_bucket():
    bucket = os.environ.get("UPLOADS_BUCKET")
    if not bucket:
        raise RuntimeError("UPLOADS_BUCKET env var is not set")
    return bucket


def _processed_bucket():
    bucket = os.environ.get("PROCESSED_BUCKET")
    if not bucket:
        raise RuntimeError("PROCESSED_BUCKET env var is not set")
    return bucket


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

def _require_field(event, name):
    """Return a non-empty string field or raise MalformedInputError.

    Returns the STRIPPED value so whitespace-padded fields cannot leak
    into S3 keys or the ASL result payload.
    """
    value = event.get(name) if isinstance(event, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise MalformedInputError(f"missing or empty required field: {name}")
    return value.strip()


def _processed_key(video_id, original_key):
    """`processed/{videoId}/{basename}` — basename is the last path
    segment of originalKey. Deterministic, tied to the videoId (FR-6),
    human-inspectable."""
    basename = original_key.replace("\\", "/").rsplit("/", 1)[-1]
    if not basename:
        raise MalformedInputError(
            "originalKey has no filename component")
    return f"processed/{video_id}/{basename}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handler(event, context):  # noqa: ARG001 - Lambda signature
    video_id = _require_field(event, "videoId")
    original_key = _require_field(event, "originalKey")
    processed_key = _processed_key(video_id, original_key)

    # Demo-mode transcode: stream the source object body straight into the
    # processed bucket (no temp file; lab objects are small). S3 failures
    # propagate raw — the ASL task fails the execution (Story 2.2).
    s3 = _s3_client()
    source = s3.get_object(Bucket=_uploads_bucket(), Key=original_key)
    body = source["Body"].read()

    put_kwargs = {
        "Bucket": _processed_bucket(),
        "Key": processed_key,
        "Body": body,
    }
    content_type = source.get("ContentType")
    if content_type:
        put_kwargs["ContentType"] = content_type
    s3.put_object(**put_kwargs)

    size_bytes = len(body)
    logger.info(
        "transcode complete videoId=%s originalKey=%s processedKey=%s "
        "sizeBytes=%d",
        video_id, original_key, processed_key, size_bytes)

    # Domain result for the ASL — the event-publisher task (Story 2.2)
    # builds the video.processed detail from exactly these fields.
    return {
        "videoId": video_id,
        "originalKey": original_key,
        "processedKey": processed_key,
        "sizeBytes": size_bytes,
    }
