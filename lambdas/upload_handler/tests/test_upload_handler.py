"""Red-phase ATDD scaffolds for Story 1.3: Upload Journey Through the Gateway.

These tests encode the acceptance criteria as executable assertions.
They are SKIPPED until the upload-handler implementation exists.
Remove the `pytest.importorskip` line and the per-test `@pytest.mark.skip`
decorators when starting the green-phase implementation.

TDD Phase: RED
Story: 1.3-upload-journey-through-the-gateway
"""

import json
import uuid

import pytest

# RED PHASE: this import will fail until lambdas/upload_handler/handler.py
# is created. pytest.importorskip skips the entire module when missing.
pytest.importorskip(
    "upload_handler.handler",
    reason="RED PHASE: upload-handler Lambda not yet implemented (Story 1.3)",
)

from upload_handler.handler import handler  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes (same pattern as lambdas/_shared/tests/test_shared.py)
# ---------------------------------------------------------------------------

class FakeS3Client:
    """In-memory S3 stand-in recording put_object calls."""

    def __init__(self):
        self.objects = {}  # (bucket, key) -> {"Body": bytes, "ContentType": str}
        self.put_calls = []

    def put_object(self, Bucket, Key, Body, ContentType=None, **kwargs):
        self.objects[(Bucket, Key)] = {
            "Body": Body,
            "ContentType": ContentType,
        }
        self.put_calls.append({
            "Bucket": Bucket,
            "Key": Key,
            "Body": Body,
            "ContentType": ContentType,
        })
        return {}


class FakeEventBridgeClient:
    """In-memory EventBridge stand-in recording put_events calls."""

    def __init__(self):
        self.events = []

    def put_events(self, Entries):
        self.events.extend(Entries)
        return {"Entries": [{"EventId": str(uuid.uuid4())} for _ in Entries]}


class FakeTable:
    """In-memory DynamoDB table (same as test_shared.py)."""

    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        key = Item["videoId"]
        if ConditionExpression == "attribute_not_exists(videoId)":
            if key in self.items:
                raise Exception("ConditionalCheckFailedException")
        self.items[key] = dict(Item)
        return {}

    def get_item(self, Key):
        item = self.items.get(Key["videoId"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ConditionExpression=None,
                    ReturnValues=None):
        key = Key["videoId"]
        item = self.items.get(key)
        if item is None:
            raise Exception("ConditionalCheckFailedException")
        if ConditionExpression == "#s = :expected":
            if item.get("status") != ExpressionAttributeValues[":expected"]:
                raise Exception("ConditionalCheckFailedException")
        for clause in UpdateExpression[len("SET "):].split(", "):
            name_ref, value_ref = clause.split(" = ")
            attr = ExpressionAttributeNames.get(name_ref, name_ref)
            item[attr] = ExpressionAttributeValues[value_ref]
        resp = {}
        if ReturnValues == "ALL_NEW":
            resp["Attributes"] = dict(item)
        return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOUNDARY = "----TestBoundary123"

def _multipart_body(fields: dict[str, str], file_field: str,
                    filename: str, file_bytes: bytes,
                    content_type: str = "video/mp4") -> str:
    """Build a raw multipart/form-data body string (as floci delivers it)."""
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        )
    parts.append(
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    )
    body_str = "".join(parts)
    # For test purposes we encode file bytes as latin-1 to keep it a string
    body_str += file_bytes.decode("latin-1")
    body_str += f"\r\n--{BOUNDARY}--\r\n"
    return body_str


def _make_event(body: str, content_type: str | None = None) -> dict:
    """Build a minimal API Gateway v2 event as floci delivers it."""
    ct = content_type or f"multipart/form-data; boundary={BOUNDARY}"
    return {
        "version": "2.0",
        "routeKey": "POST /videos/upload",
        "rawPath": "/videos/upload",
        "headers": {"content-type": ct},
        "body": body,
        "isBase64Encoded": False,
    }


@pytest.fixture
def s3():
    return FakeS3Client()


@pytest.fixture
def table():
    return FakeTable()


@pytest.fixture
def eventbridge():
    return FakeEventBridgeClient()


@pytest.fixture
def deps(s3, table, eventbridge, monkeypatch):
    """Wire fakes into the handler's client factories via env/monkeypatch."""
    # The handler is expected to read resource names from env vars (NFR-4).
    monkeypatch.setenv("UPLOADS_BUCKET", "video-uploads")
    monkeypatch.setenv("METADATA_TABLE", "video-metadata")
    monkeypatch.setenv("EVENT_BUS_NAME", "video-bus")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    # Monkeypatch the handler's client factories to return fakes.
    # Adjust import paths once the handler module structure is known.
    import upload_handler.handler as h
    monkeypatch.setattr(h, "_s3_client", lambda: s3, raising=False)
    monkeypatch.setattr(h, "_dynamo_table", lambda: table, raising=False)
    monkeypatch.setattr(h, "_events_client", lambda: eventbridge, raising=False)
    return {"s3": s3, "table": table, "eventbridge": eventbridge}


# ---------------------------------------------------------------------------
# 1.3-UNIT-001 [P0] Multipart parsing — raw body, never base64
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestMultipartParsing:
    def test_parses_raw_multipart_extracts_file_bytes(self, deps):
        """AC2: handler parses the raw multipart body (isBase64Encoded: false)."""
        file_bytes = b"\x00\x01\x02fake-video-data"
        body = _multipart_body({}, "file", "demo.mp4", file_bytes)
        event = _make_event(body)

        response = handler(event, None)

        assert response["statusCode"] == 200
        # The file bytes must land in S3 intact
        s3 = deps["s3"]
        assert len(s3.put_calls) == 1
        assert s3.put_calls[0]["Body"] == file_bytes

    def test_extracts_filename_from_content_disposition(self, deps):
        """AC2: filename extracted from multipart Content-Disposition."""
        body = _multipart_body({}, "file", "my-video.mp4", b"data")
        event = _make_event(body)

        response = handler(event, None)
        json.loads(response["body"])  # body must be valid JSON

        # The S3 key should contain the original filename
        s3 = deps["s3"]
        assert "my-video.mp4" in s3.put_calls[0]["Key"]


# ---------------------------------------------------------------------------
# 1.3-UNIT-002 [P1] Title fallback
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestTitleFallback:
    def test_title_from_form_field(self, deps):
        """AC2: optional title form field is read and stored."""
        body = _multipart_body(
            {"title": "My Great Video"}, "file", "demo.mp4", b"data")
        event = _make_event(body)

        handler(event, None)

        table = deps["table"]
        record = list(table.items.values())[0]
        assert record["title"] == "My Great Video"

    def test_title_falls_back_to_filename(self, deps):
        """AC2: when title field is absent, filename is used."""
        body = _multipart_body({}, "file", "fallback-name.mp4", b"data")
        event = _make_event(body)

        handler(event, None)

        table = deps["table"]
        record = list(table.items.values())[0]
        assert record["title"] == "fallback-name.mp4"


# ---------------------------------------------------------------------------
# 1.3-UNIT-003 [P0] Response shape — 2xx + videoId
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestResponseShape:
    def test_returns_200_with_video_id(self, deps):
        """AC2: response is HTTP 2xx returning the minted videoId (UUID)."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")
        event = _make_event(body)

        response = handler(event, None)

        assert response["statusCode"] == 200
        body_json = json.loads(response["body"])
        assert "videoId" in body_json
        # Must be a valid UUID
        uuid.UUID(body_json["videoId"])

    def test_video_id_is_unique_per_request(self, deps):
        """AC2/FR-2: each upload mints a fresh UUID."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")

        r1 = handler(_make_event(body), None)
        r2 = handler(_make_event(body), None)

        id1 = json.loads(r1["body"])["videoId"]
        id2 = json.loads(r2["body"])["videoId"]
        assert id1 != id2


# ---------------------------------------------------------------------------
# 1.3-UNIT-004 [P0] S3 side effect
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestS3SideEffect:
    def test_object_stored_in_uploads_bucket(self, deps):
        """AC2/FR-1: object exists in video-uploads bucket."""
        body = _multipart_body({}, "file", "demo.mp4", b"video-bytes")
        event = _make_event(body)

        handler(event, None)

        s3 = deps["s3"]
        assert len(s3.put_calls) == 1
        assert s3.put_calls[0]["Bucket"] == "video-uploads"

    def test_s3_key_contains_video_id(self, deps):
        """AC2/FR-2: S3 key contains the same videoId returned in response."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")
        event = _make_event(body)

        response = handler(event, None)
        video_id = json.loads(response["body"])["videoId"]

        s3 = deps["s3"]
        assert video_id in s3.put_calls[0]["Key"]

    def test_content_type_preserved(self, deps):
        """FR-10: content type from multipart part is stored."""
        body = _multipart_body(
            {}, "file", "demo.mp4", b"data", content_type="video/webm")
        event = _make_event(body)

        handler(event, None)

        s3 = deps["s3"]
        assert s3.put_calls[0]["ContentType"] == "video/webm"


# ---------------------------------------------------------------------------
# 1.3-UNIT-005 [P0] DynamoDB record creation
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestDynamoRecord:
    def test_record_created_with_uploaded_status(self, deps):
        """AC2/FR-3: video-metadata record exists with status UPLOADED."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")
        event = _make_event(body)

        response = handler(event, None)
        video_id = json.loads(response["body"])["videoId"]

        table = deps["table"]
        assert video_id in table.items
        record = table.items[video_id]
        assert record["status"] == "UPLOADED"

    def test_record_has_timestamps(self, deps):
        """AC2/FR-3: created/updated timestamps populated."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")
        event = _make_event(body)

        response = handler(event, None)
        video_id = json.loads(response["body"])["videoId"]

        record = deps["table"].items[video_id]
        assert record.get("createdAt")
        assert record.get("updatedAt")

    def test_record_carries_full_shape(self, deps):
        """FR-10: record carries videoId, title, status, bucket, key,
        content_type, size, timestamps."""
        file_bytes = b"x" * 2048
        body = _multipart_body(
            {"title": "Test Vid"}, "file", "demo.mp4", file_bytes)
        event = _make_event(body)

        response = handler(event, None)
        video_id = json.loads(response["body"])["videoId"]

        record = deps["table"].items[video_id]
        assert record["videoId"] == video_id
        assert record["title"] == "Test Vid"
        assert record["status"] == "UPLOADED"
        assert record["bucket"] == "video-uploads"
        assert "key" in record or "originalKey" in record
        assert record.get("contentType") == "video/mp4"
        assert record.get("sizeBytes") == 2048


# ---------------------------------------------------------------------------
# 1.3-UNIT-006 [P0] EventBridge emission
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestEventEmission:
    def test_video_uploaded_event_emitted(self, deps):
        """AC2/FR-4: video.uploaded event is on the bus."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")
        event = _make_event(body)

        response = handler(event, None)
        json.loads(response["body"])["videoId"]  # response carries videoId

        eb = deps["eventbridge"]
        assert len(eb.events) == 1
        entry = eb.events[0]
        assert entry["DetailType"] == "video.uploaded"
        assert entry["EventBusName"] == "video-bus"

    def test_event_carries_deterministic_event_id(self, deps):
        """FR-4/NFR-2: eventId is deterministic UUID5 of (videoId, UPLOADED)."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")
        event = _make_event(body)

        response = handler(event, None)
        json.loads(response["body"])["videoId"]  # response carries videoId

        eb = deps["eventbridge"]
        detail = json.loads(eb.events[0]["Detail"])
        assert detail["eventId"] is not None
        # Verify it's a valid UUID
        uuid.UUID(detail["eventId"])

    def test_event_detail_shape(self, deps):
        """AD-6: video.uploaded detail = {videoId, status, bucket, key}."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")
        event = _make_event(body)

        response = handler(event, None)
        video_id = json.loads(response["body"])["videoId"]

        eb = deps["eventbridge"]
        detail = json.loads(eb.events[0]["Detail"])
        assert detail["videoId"] == video_id
        assert detail["status"] == "UPLOADED"
        assert detail["bucket"] == "video-uploads"
        assert "key" in detail


# ---------------------------------------------------------------------------
# 1.3-UNIT-007 [P0] Missing file → 400
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestMissingFile:
    def test_missing_file_part_returns_400(self, deps):
        """AC4/NFR-3: missing file → 400 {"error": ...}."""
        # Multipart with only a title field, no file
        _multipart_body({"title": "no file here"}, "file", "", b"")
        # Remove the file part entirely by building without it
        body_no_file = (
            f"--{BOUNDARY}\r\n"
            f'Content-Disposition: form-data; name="title"\r\n\r\n'
            f"just a title\r\n"
            f"--{BOUNDARY}--\r\n"
        )
        event = _make_event(body_no_file)

        response = handler(event, None)

        assert response["statusCode"] == 400
        body_json = json.loads(response["body"])
        assert "error" in body_json

    def test_empty_body_returns_400(self, deps):
        """AC4: empty body is malformed → 400."""
        event = _make_event("")

        response = handler(event, None)

        assert response["statusCode"] == 400
        body_json = json.loads(response["body"])
        assert "error" in body_json


# ---------------------------------------------------------------------------
# 1.3-UNIT-008 [P1] Unparseable multipart → 400
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestUnparseableMultipart:
    def test_garbage_body_returns_400(self, deps):
        """AC4: unparseable multipart body → 400 {"error": ...}."""
        event = _make_event("this is not multipart at all")

        response = handler(event, None)

        assert response["statusCode"] == 400
        body_json = json.loads(response["body"])
        assert "error" in body_json

    def test_missing_content_type_returns_400(self, deps):
        """AC4: no content-type header → 400."""
        body = _multipart_body({}, "file", "demo.mp4", b"data")
        event = _make_event(body, content_type=None)
        del event["headers"]["content-type"]

        response = handler(event, None)

        assert response["statusCode"] == 400
        body_json = json.loads(response["body"])
        assert "error" in body_json


# ---------------------------------------------------------------------------
# 1.3-INT-001 [P0] Full handler integration (fake services)
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestHandlerIntegration:
    def test_full_upload_journey_within_handler(self, deps):
        """AC2 end-to-end within handler boundary: multipart in →
        S3 object + DDB record + EventBridge event, all consistent."""
        file_bytes = b"\x89PNG\r\n\x1a\nfake-video-content"
        body = _multipart_body(
            {"title": "Integration Test Video"},
            "file", "integration.mp4", file_bytes,
            content_type="video/mp4",
        )
        event = _make_event(body)

        response = handler(event, None)

        # 1. Response
        assert response["statusCode"] == 200
        video_id = json.loads(response["body"])["videoId"]
        uuid.UUID(video_id)  # valid UUID

        # 2. S3
        s3 = deps["s3"]
        assert len(s3.put_calls) == 1
        assert s3.put_calls[0]["Bucket"] == "video-uploads"
        assert video_id in s3.put_calls[0]["Key"]
        assert s3.put_calls[0]["Body"] == file_bytes

        # 3. DynamoDB
        table = deps["table"]
        assert video_id in table.items
        record = table.items[video_id]
        assert record["status"] == "UPLOADED"
        assert record["title"] == "Integration Test Video"
        assert record["createdAt"]
        assert record["updatedAt"]

        # 4. EventBridge
        eb = deps["eventbridge"]
        assert len(eb.events) == 1
        entry = eb.events[0]
        assert entry["DetailType"] == "video.uploaded"
        detail = json.loads(entry["Detail"])
        assert detail["videoId"] == video_id
        assert detail["status"] == "UPLOADED"
        assert detail["bucket"] == "video-uploads"


# ---------------------------------------------------------------------------
# 1.3-INT-002 [P2] Duplicate filename → distinct keys
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="RED PHASE: upload-handler not implemented")
class TestDuplicateFilename:
    def test_same_filename_produces_distinct_keys(self, deps):
        """Edge: uploading the same filename twice yields different S3 keys
        (because videoId differs)."""
        body = _multipart_body({}, "file", "same.mp4", b"data")

        handler(_make_event(body), None)
        handler(_make_event(body), None)

        s3 = deps["s3"]
        assert len(s3.put_calls) == 2
        key1 = s3.put_calls[0]["Key"]
        key2 = s3.put_calls[1]["Key"]
        assert key1 != key2
