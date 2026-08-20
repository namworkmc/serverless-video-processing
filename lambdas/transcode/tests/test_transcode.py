"""ATDD suite for Story 2.1: Transcode Worker Lambda (pure S3 in -> S3 out).

Assertions encode the spec's I/O & Edge-Case Matrix:

| Scenario            | Expected                                              |
|---------------------|-------------------------------------------------------|
| Happy transcode     | copy to video-processed under key containing videoId; |
|                     | returns {videoId, originalKey, processedKey, sizeBytes}|
| Missing videoId     | raises MalformedInputError                            |
| Missing originalKey | raises MalformedInputError                            |
| Unknown source      | S3 error propagates uncaught                          |
| Redelivery          | second run overwrites the same processed key          |

Plus the AD-4 purity guarantee: no shared.status / shared.events import,
no DynamoDB/EventBridge client ever constructed.

TDD Phase: GREEN
Story: 2-1-transcode-worker-lambda-pure-s3-in-s3-out
"""

import io
import logging

import pytest

from shared.errors import MalformedInputError
from transcode.handler import handler


# ---------------------------------------------------------------------------
# Fakes (same pattern as lambdas/upload_handler/tests/test_upload_handler.py)
# ---------------------------------------------------------------------------

class FakeStreamingBody:
    """Stand-in for botocore's StreamingBody (read() -> bytes)."""

    def __init__(self, data):
        self._stream = io.BytesIO(data)

    def read(self, *args):
        return self._stream.read(*args)


class FakeS3Client:
    """In-memory S3 stand-in recording get_object/put_object calls."""

    def __init__(self, objects=None):
        # (bucket, key) -> {"Body": bytes, "ContentType": str | None}
        self.objects = dict(objects or {})
        self.get_calls = []
        self.put_calls = []

    def get_object(self, Bucket, Key):
        self.get_calls.append({"Bucket": Bucket, "Key": Key})
        obj = self.objects.get((Bucket, Key))
        if obj is None:
            # Mimic botocore: NoSuchKey surfaces as ClientError.
            raise FakeClientError("NoSuchKey", Bucket, Key)
        return {
            "Body": FakeStreamingBody(obj["Body"]),
            "ContentType": obj.get("ContentType"),
        }

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = {
            "Body": Body,
            "ContentType": kwargs.get("ContentType"),
        }
        self.put_calls.append({
            "Bucket": Bucket,
            "Key": Key,
            "Body": Body,
            "ContentType": kwargs.get("ContentType"),
            # Raw kwargs so tests can distinguish "ContentType omitted"
            # from "ContentType=None".
            "raw_kwargs": kwargs,
        })
        return {}


class FakeClientError(Exception):
    """Duck-typed botocore ClientError for the unknown-object case."""

    def __init__(self, code, bucket, key):
        super().__init__(
            f"An error occurred ({code}) when calling the GetObject "
            f"operation: bucket={bucket} key={key}")
        self.response = {"Error": {"Code": code}}


class ClientFactoryRecorder:
    """Wraps shared.clients to record every client construction — the
    AD-4 purity probe (no dynamodb/events clients may ever be built)."""

    def __init__(self, s3):
        self.s3 = s3
        self.requested = []

    def s3_client(self):
        self.requested.append("s3")
        return self.s3

    def dynamodb_resource(self):
        self.requested.append("dynamodb")
        raise AssertionError("AD-4 violation: dynamodb client constructed")

    def dynamodb_table(self, name):
        self.requested.append("dynamodb")
        raise AssertionError("AD-4 violation: dynamodb client constructed")

    def events_client(self):
        self.requested.append("events")
        raise AssertionError("AD-4 violation: events client constructed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VIDEO_ID = "11111111-2222-3333-4444-555555555555"
ORIGINAL_KEY = f"{VIDEO_ID}/demo.mp4"
VIDEO_BYTES = b"\x00\x01\x02fake-video-data"


@pytest.fixture
def s3():
    return FakeS3Client(objects={
        ("video-uploads", ORIGINAL_KEY): {
            "Body": VIDEO_BYTES,
            "ContentType": "video/mp4",
        },
    })


@pytest.fixture
def deps(s3, monkeypatch):
    """Wire the fake S3 client + env vars into the handler (NFR-4)."""
    monkeypatch.setenv("UPLOADS_BUCKET", "video-uploads")
    monkeypatch.setenv("PROCESSED_BUCKET", "video-processed")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    import transcode.handler as h
    monkeypatch.setattr(h, "_s3_client", lambda: s3)
    return {"s3": s3}


def _payload(**extra):
    return {"videoId": VIDEO_ID, "originalKey": ORIGINAL_KEY, **extra}


# ---------------------------------------------------------------------------
# Happy transcode (matrix row 1)
# ---------------------------------------------------------------------------

class TestHappyTranscode:
    def test_object_copied_to_processed_bucket(self, deps):
        s3 = deps["s3"]

        handler(_payload(), None)

        assert len(s3.get_calls) == 1
        assert s3.get_calls[0] == {
            "Bucket": "video-uploads", "Key": ORIGINAL_KEY}
        assert len(s3.put_calls) == 1
        put = s3.put_calls[0]
        assert put["Bucket"] == "video-processed"
        assert put["Body"] == VIDEO_BYTES

    def test_processed_key_contains_video_id(self, deps):
        result = handler(_payload(), None)

        assert VIDEO_ID in result["processedKey"]
        assert VIDEO_ID in deps["s3"].put_calls[0]["Key"]

    def test_processed_key_shape(self, deps):
        """Design Notes: processed/{videoId}/{basename}."""
        result = handler(_payload(), None)

        assert result["processedKey"] == f"processed/{VIDEO_ID}/demo.mp4"
        assert deps["s3"].put_calls[0]["Key"] == result["processedKey"]

    def test_returns_domain_payload_for_asl(self, deps):
        """The ASL result: {videoId, originalKey, processedKey, sizeBytes}."""
        result = handler(_payload(), None)

        assert result == {
            "videoId": VIDEO_ID,
            "originalKey": ORIGINAL_KEY,
            "processedKey": f"processed/{VIDEO_ID}/demo.mp4",
            "sizeBytes": len(VIDEO_BYTES),
        }

    def test_content_type_preserved(self, deps):
        """Design Notes: preserve ContentType from the source object."""
        handler(_payload(), None)

        assert deps["s3"].put_calls[0]["ContentType"] == "video/mp4"

    def test_no_content_type_when_source_has_none(self, deps):
        deps["s3"].objects[("video-uploads", ORIGINAL_KEY)] = {
            "Body": VIDEO_BYTES, "ContentType": None}

        handler(_payload(), None)

        # ContentType must be OMITTED from the put kwargs entirely,
        # not passed as None.
        assert "ContentType" not in deps["s3"].put_calls[0]["raw_kwargs"]

    def test_log_line_emitted(self, deps, caplog):
        """NFR-5: structured logging with videoId/keys/size."""
        with caplog.at_level(logging.INFO, logger="transcode.handler"):
            handler(_payload(), None)

        log_text = " ".join(caplog.messages)
        assert VIDEO_ID in log_text
        assert ORIGINAL_KEY in log_text
        assert f"processed/{VIDEO_ID}/demo.mp4" in log_text
        assert str(len(VIDEO_BYTES)) in log_text

    def test_extra_fields_tolerated(self, deps):
        """Input contract: the ASL may pass the full detail unchanged."""
        payload = _payload(
            status="UPLOADED", bucket="video-uploads",
            eventId="abc-123", schemaVersion=1)

        result = handler(payload, None)

        assert result["videoId"] == VIDEO_ID
        assert result["processedKey"] == f"processed/{VIDEO_ID}/demo.mp4"


# ---------------------------------------------------------------------------
# Malformed input (matrix rows 2-3)
# ---------------------------------------------------------------------------

class TestMalformedInput:
    @pytest.mark.parametrize("payload", [
        {"originalKey": ORIGINAL_KEY},                    # missing videoId
        {"videoId": "", "originalKey": ORIGINAL_KEY},     # empty videoId
        {"videoId": "   ", "originalKey": ORIGINAL_KEY},  # blank videoId
        {},                                               # empty payload
    ])
    def test_missing_or_empty_video_id_raises(self, deps, payload):
        with pytest.raises(MalformedInputError):
            handler(payload, None)

    @pytest.mark.parametrize("payload", [
        {"videoId": VIDEO_ID},                            # missing key
        {"videoId": VIDEO_ID, "originalKey": ""},         # empty key
        {"videoId": VIDEO_ID, "originalKey": "   "},      # blank key
    ])
    def test_missing_or_empty_original_key_raises(self, deps, payload):
        with pytest.raises(MalformedInputError):
            handler(payload, None)

    def test_malformed_input_performs_no_s3_access(self, deps):
        with pytest.raises(MalformedInputError):
            handler({"videoId": VIDEO_ID}, None)

        s3 = deps["s3"]
        assert s3.get_calls == []
        assert s3.put_calls == []


# ---------------------------------------------------------------------------
# Unknown source object (matrix row 4)
# ---------------------------------------------------------------------------

class TestUnknownSourceObject:
    def test_s3_error_propagates_uncaught(self, deps):
        """Task failure — the ASL fails the execution in Story 2.2."""
        payload = _payload(originalKey=f"{VIDEO_ID}/missing.mp4")

        with pytest.raises(FakeClientError):
            handler(payload, None)

        # No partial side effects: nothing was written.
        assert deps["s3"].put_calls == []


# ---------------------------------------------------------------------------
# Redelivery / re-invoke (matrix row 5)
# ---------------------------------------------------------------------------

class TestRedelivery:
    def test_second_run_overwrites_same_processed_key(self, deps):
        r1 = handler(_payload(), None)
        r2 = handler(_payload(), None)

        assert r1 == r2
        s3 = deps["s3"]
        assert len(s3.put_calls) == 2
        assert s3.put_calls[0]["Key"] == s3.put_calls[1]["Key"]
        assert s3.put_calls[1]["Body"] == VIDEO_BYTES
        # Exactly one object exists under the deterministic key.
        processed = [k for (b, k) in s3.objects if b == "video-processed"]
        assert processed == [f"processed/{VIDEO_ID}/demo.mp4"]


# ---------------------------------------------------------------------------
# Remaining handler branches (review patches)
# ---------------------------------------------------------------------------

class TestRemainingBranches:
    def test_original_key_without_filename_component_raises(self, deps):
        """`_processed_key` guard: trailing-slash key has no basename."""
        with pytest.raises(MalformedInputError):
            handler(_payload(originalKey=f"{VIDEO_ID}/"), None)

        assert deps["s3"].put_calls == []

    @pytest.mark.parametrize("payload", [
        {"videoId": 123, "originalKey": ORIGINAL_KEY},      # non-string id
        {"videoId": VIDEO_ID, "originalKey": 456},          # non-string key
        {"videoId": None, "originalKey": ORIGINAL_KEY},     # None id
    ])
    def test_non_string_fields_raise(self, deps, payload):
        with pytest.raises(MalformedInputError):
            handler(payload, None)

    @pytest.mark.parametrize("event", ["not-a-dict", ["list"], None, 42])
    def test_non_dict_event_raises(self, deps, event):
        with pytest.raises(MalformedInputError):
            handler(event, None)

    def test_whitespace_padded_fields_are_stripped(self, deps):
        """Review patch: _require_field returns the stripped value."""
        result = handler(
            {"videoId": f"  {VIDEO_ID}  ",
             "originalKey": f"  {ORIGINAL_KEY}  "}, None)

        assert result["videoId"] == VIDEO_ID
        assert result["originalKey"] == ORIGINAL_KEY
        assert result["processedKey"] == f"processed/{VIDEO_ID}/demo.mp4"

    def test_zero_byte_source_object_succeeds(self, deps):
        deps["s3"].objects[("video-uploads", ORIGINAL_KEY)] = {
            "Body": b"", "ContentType": "video/mp4"}

        result = handler(_payload(), None)

        assert result["sizeBytes"] == 0
        assert deps["s3"].put_calls[0]["Body"] == b""

    def test_missing_uploads_bucket_env_raises(self, deps, monkeypatch):
        monkeypatch.delenv("UPLOADS_BUCKET")

        with pytest.raises(RuntimeError, match="UPLOADS_BUCKET"):
            handler(_payload(), None)

    def test_missing_processed_bucket_env_raises(self, deps, monkeypatch):
        monkeypatch.delenv("PROCESSED_BUCKET")

        with pytest.raises(RuntimeError, match="PROCESSED_BUCKET"):
            handler(_payload(), None)


# ---------------------------------------------------------------------------
# Purity guarantee (AD-4)
# ---------------------------------------------------------------------------

class TestPurity:
    def test_handler_module_does_not_import_status_or_events(self):
        """The module must not even import shared.status / shared.events.
        AST-based so docstrings mentioning them can't false-positive."""
        import ast
        import inspect

        import transcode.handler as h
        tree = ast.parse(inspect.getsource(h))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                imported.extend(
                    f"{base}.{a.name}" if base else a.name
                    for a in node.names)
            elif isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        assert not any(m == "shared.status" or m.startswith("shared.status.")
                       for m in imported)
        assert not any(m == "shared.events" or m.startswith("shared.events.")
                       for m in imported)
        assert "shared.clients" in imported

    def test_no_dynamodb_or_events_client_constructed(self, deps,
                                                       monkeypatch):
        """AD-4 probe: route shared.clients through a recorder that fails
        on any dynamodb/events construction; the handler may only ever
        ask for s3."""
        recorder = ClientFactoryRecorder(deps["s3"])
        import transcode.handler as h
        monkeypatch.setattr(h, "clients", recorder)
        monkeypatch.setattr(h, "_s3_client", lambda: recorder.s3_client())

        result = handler(_payload(), None)

        assert result["sizeBytes"] == len(VIDEO_BYTES)
        assert recorder.requested == ["s3"]
