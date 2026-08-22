"""Upload-handler Lambda — the ingest leg (Story 1.3).

POST /videos/upload (API Gateway v2, AWS_PROXY) -> this handler:

1. Parse the multipart body. The gateway delivers non-text bodies
   base64-encoded with `isBase64Encoded: true` (matches real AWS) —
   decode first, then parse raw bytes. Plain-text bodies
   (`isBase64Encoded: false`) are parsed as-is.
2. Mint `videoId` (UUID4) exactly once; the same id appears in the
   response, the S3 key, the metadata record, and the event (FR-2).
3. Side effects, strictly in order:
     S3 put_object  ->  shared.status.create_record  ->  events.put_events
   All service access goes through the shared layer (NFR-4): no
   hand-written status writes, envelope via events.build_envelope,
   errors via errors.map_error.
4. Respond 200 {"videoId": ...}; parse failures -> 400 {"error": ...},
   downstream failures -> 500 {"error": ...} (NFR-3).

Config-not-code: bucket/table/bus names come strictly from the
Terraform-set env vars UPLOADS_BUCKET / METADATA_TABLE / EVENT_BUS_NAME;
the endpoint from AWS_ENDPOINT_URL (via shared.clients).
"""

import base64
import json
import logging
import os
import uuid
from email.parser import BytesParser
from email.policy import HTTP

from shared import clients, events, status
from shared.errors import MalformedInputError, map_error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# EventBridge source for events this handler publishes.
EVENT_SOURCE = "upload-handler"


# ---------------------------------------------------------------------------
# Client accessors — thin wrappers over shared.clients with the env-var
# names for THIS function (config-not-code, NFR-4). Module-level functions
# so tests can monkeypatch them (see tests/test_upload_handler.py `deps`).
# ---------------------------------------------------------------------------

def _s3_client():
    return clients.s3_client()


def _dynamo_table():
    table_name = os.environ.get("METADATA_TABLE")
    if not table_name:
        raise RuntimeError("METADATA_TABLE env var is not set")
    return clients.dynamodb_table(table_name)


def _events_client():
    return clients.events_client()


def _uploads_bucket():
    bucket = os.environ.get("UPLOADS_BUCKET")
    if not bucket:
        raise RuntimeError("UPLOADS_BUCKET env var is not set")
    return bucket


def _event_bus_name():
    bus = os.environ.get("EVENT_BUS_NAME")
    if not bus:
        raise RuntimeError("EVENT_BUS_NAME env var is not set")
    return bus


# ---------------------------------------------------------------------------
# Multipart parsing — stdlib only, cgi-free (spec Design Notes).
# ---------------------------------------------------------------------------

def _content_type(event):
    """Case-insensitive content-type header lookup (API GW v2 lowercases
    headers, but be robust). Missing -> MalformedInputError."""
    headers = event.get("headers") or {}
    for name, value in headers.items():
        if name.lower() == "content-type":
            return value
    raise MalformedInputError("missing content-type header")


def _parse_multipart(event):
    """Parse the raw multipart body.

    Returns (file_info, fields) where file_info is a dict with filename /
    content_type / data and fields maps plain form-field names to values.
    Raises MalformedInputError for every parse failure (NFR-3 -> 400).
    """
    content_type = _content_type(event)
    if "multipart/form-data" not in content_type.lower():
        raise MalformedInputError(
            "content-type must be multipart/form-data")

    body = event.get("body")
    if not body:
        raise MalformedInputError("empty request body")
    if event.get("isBase64Encoded"):
        # The gateway delivers non-text bodies (incl. multipart) base64
        # with isBase64Encoded: true (matches real AWS). Decode to raw
        # bytes before parsing.
        try:
            raw = base64.b64decode(body, validate=True)
        except ValueError:
            raise MalformedInputError("body is not valid base64")
    else:
        # Plain-text delivery: latin-1 round-trips every byte value 1:1 —
        # binary-safe for the string body. Code points above U+00FF cannot
        # be a raw byte stream — reject as malformed (400, not 500).
        try:
            raw = body.encode("latin-1") if isinstance(body, str) else bytes(body)
        except UnicodeEncodeError:
            raise MalformedInputError("request body contains undecodable characters")
    try:
        ct_bytes = content_type.encode("latin-1")
    except UnicodeEncodeError:
        raise MalformedInputError("content-type contains undecodable characters")
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + ct_bytes + b"\r\n\r\n" + raw)
    if not message.is_multipart():
        raise MalformedInputError("body is not multipart")

    fields = {}
    file_info = None
    for part in message.iter_parts():
        disposition = part.get_content_disposition()
        if disposition != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if filename:
            if file_info is not None:
                raise MalformedInputError("multiple file parts")
            payload = part.get_payload(decode=True)
            if payload is None:
                payload = part.get_content().encode("latin-1")
            elif not isinstance(payload, (bytes, bytearray)):
                payload = payload.encode("latin-1")
            if not payload:
                raise MalformedInputError("empty file part")
            file_info = {
                "filename": filename,
                "content_type": part.get_content_type(),
                "data": bytes(payload),
            }
        elif name:
            value = part.get_content()
            if not isinstance(value, str):
                raise MalformedInputError(
                    f"form field {name!r} is not plain text")
            fields[name] = value

    if file_info is None:
        raise MalformedInputError("missing file part")
    return file_info, fields


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _sanitize_filename(filename):
    """Strip path components and reject empty/control-only names.

    The client-supplied filename flows into the S3 key; path separators,
    dot-dot, and control characters must not reshape the key.
    """
    # Take the last path component regardless of separator style.
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    # Drop control characters.
    name = "".join(ch for ch in name if ch.isprintable())
    if not name or name in (".", ".."):
        raise MalformedInputError("invalid filename in upload")
    return name


def handler(event, context):  # noqa: ARG001 - Lambda signature
    try:
        file_info, fields = _parse_multipart(event)

        # Mint the videoId exactly once — reused in response, S3 key,
        # record, and event (FR-2).
        video_id = str(uuid.uuid4())
        filename = _sanitize_filename(file_info["filename"])
        bucket = _uploads_bucket()
        key = f"{video_id}/{filename}"

        # 1. S3 object.
        _s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=file_info["data"],
            ContentType=file_info["content_type"],
        )

        # 2. Metadata record (idempotent create, mints UPLOADED +
        #    timestamps). Title: optional form field, fallback to the
        #    uploaded filename (FR-10).
        title = (fields.get("title") or "").strip() or filename
        status.create_record(
            _dynamo_table(),
            video_id,
            title,
            bucket,
            key,
            content_type=file_info["content_type"],
            size_bytes=len(file_info["data"]),
        )

        # 3. Deterministic video.uploaded event on the custom bus.
        envelope = events.build_envelope(
            events.EVENT_UPLOADED,
            events.uploaded_detail(video_id, bucket, key),
        )
        # Wire Detail: the envelope (eventId + schemaVersion) with the
        # detail fields promoted to the top level — consumers can read
        # {eventId, schemaVersion, videoId, status, bucket, key} flat,
        # and the nested envelope detail stays intact for envelope-shaped
        # readers (the AC tests assert both views).
        detail_payload = {**envelope, **envelope["detail"]}
        put_resp = _events_client().put_events(Entries=[{
            "Source": EVENT_SOURCE,
            "DetailType": events.EVENT_UPLOADED,
            "Detail": json.dumps(detail_payload),
            "EventBusName": _event_bus_name(),
        }])
        # EventBridge returns HTTP 200 even when individual entries fail;
        # a dropped video.uploaded must not masquerade as success.
        if put_resp.get("FailedEntryCount"):
            raise RuntimeError(
                f"event publish failed: {put_resp.get('FailedEntryCount')} "
                "entries rejected")

        logger.info(
            "upload accepted videoId=%s key=%s size=%d",
            video_id, key, len(file_info["data"]))
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"videoId": video_id}),
        }
    except Exception as exc:  # noqa: BLE001 - map_error decides the status
        http_status, body = map_error(exc)
        logger.warning("upload rejected status=%d error=%s",
                       http_status, body.get("error"))
        return {
            "statusCode": http_status,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body),
        }
