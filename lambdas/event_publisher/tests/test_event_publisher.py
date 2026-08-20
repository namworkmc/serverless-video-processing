"""ATDD suite for Story 2.2: Event-Publisher Lambda (sole constructor of
the video.processed envelope, AD-4/AD-6).

Assertions encode the spec's I/O & Edge-Case Matrix:

| Scenario            | Expected                                              |
|---------------------|-------------------------------------------------------|
| Happy publish       | one put_events entry on video-bus (flat Detail,       |
|                     | Source event-publisher); returns the envelope         |
| Missing/empty field | raises MalformedInputError, no event published        |
| Non-dict event      | raises MalformedInputError                            |
| Bus rejects entry   | raises RuntimeError                                   |
| Unset env           | raises RuntimeError                                   |
| Re-invoke           | identical eventId both times (UUID5 dedupe)           |

Plus the AD-4/AD-6 purity guarantee: no shared.status import, no
DynamoDB client ever constructed — only the events client.

TDD Phase: GREEN
Story: 2-2-processing-state-machine-event-publisher
"""

import json
import logging

import pytest

from shared import events
from shared.errors import MalformedInputError
from event_publisher.handler import handler

# ---------------------------------------------------------------------------
# Fakes (same pattern as lambdas/transcode/tests/test_transcode.py)
# ---------------------------------------------------------------------------


class FakeEventsClient:
    """In-memory EventBridge stand-in recording put_events calls."""

    def __init__(self, failed_entry_count=0):
        self.failed_entry_count = failed_entry_count
        self.put_calls = []

    def put_events(self, Entries):
        self.put_calls.append({"Entries": Entries})
        return {"FailedEntryCount": self.failed_entry_count}


class ClientFactoryRecorder:
    """Wraps shared.clients to record every client construction — the
    AD-4 purity probe (no dynamodb client may ever be built; only the
    events client is allowed)."""

    def __init__(self, events_client):
        self._events = events_client
        self.requested = []

    def events_client(self):
        self.requested.append("events")
        return self._events

    def s3_client(self):
        self.requested.append("s3")
        raise AssertionError("AD-4 violation: s3 client constructed")

    def dynamodb_resource(self):
        self.requested.append("dynamodb")
        raise AssertionError("AD-4 violation: dynamodb client constructed")

    def dynamodb_table(self, name):
        self.requested.append("dynamodb")
        raise AssertionError("AD-4 violation: dynamodb client constructed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VIDEO_ID = "11111111-2222-3333-4444-555555555555"
ORIGINAL_KEY = f"{VIDEO_ID}/demo.mp4"
PROCESSED_KEY = f"processed/{VIDEO_ID}/demo.mp4"


@pytest.fixture
def eb():
    return FakeEventsClient()


@pytest.fixture
def deps(eb, monkeypatch):
    """Wire the fake events client + env vars into the handler (NFR-4)."""
    monkeypatch.setenv("PROCESSED_BUCKET", "video-processed")
    monkeypatch.setenv("EVENT_BUS_NAME", "video-bus")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    import event_publisher.handler as h
    monkeypatch.setattr(h, "_events_client", lambda: eb)
    return {"eb": eb}


def _payload(**extra):
    return {
        "videoId": VIDEO_ID,
        "originalKey": ORIGINAL_KEY,
        "processedKey": PROCESSED_KEY,
        "sizeBytes": 19,
        **extra,
    }


def _published_detail(deps):
    """The flat Detail JSON of the single published entry."""
    entries = deps["eb"].put_calls[0]["Entries"]
    assert len(entries) == 1
    return json.loads(entries[0]["Detail"])


# ---------------------------------------------------------------------------
# Happy publish (matrix row 1)
# ---------------------------------------------------------------------------

class TestHappyPublish:
    def test_one_entry_on_the_bus(self, deps):
        handler(_payload(), None)

        eb = deps["eb"]
        assert len(eb.put_calls) == 1
        entry = eb.put_calls[0]["Entries"][0]
        assert entry["Source"] == "event-publisher"
        assert entry["DetailType"] == "video.processed"
        assert entry["EventBusName"] == "video-bus"

    def test_envelope_built_via_shared_layer(self, deps):
        """AD-6: eventId = UUID5(videoId, PROCESSED), schemaVersion, and
        the fixed processed detail shape — exactly what the shared layer
        produces."""
        result = handler(_payload(), None)

        expected = events.build_envelope(
            events.EVENT_PROCESSED,
            events.processed_detail(
                VIDEO_ID, "video-processed", ORIGINAL_KEY, PROCESSED_KEY),
        )
        assert result == expected
        assert result["eventId"] == events.event_id(VIDEO_ID, "PROCESSED")
        assert result["schemaVersion"] == events.SCHEMA_VERSION

    def test_wire_detail_is_flat_envelope_plus_detail(self, deps):
        """Mirrors upload_handler's flat shape: consumers can read
        {eventId, schemaVersion, videoId, status, bucket, originalKey,
        processedKey} flat, and the nested detail stays intact."""
        handler(_payload(), None)

        detail = _published_detail(deps)
        assert detail["eventId"] == events.event_id(VIDEO_ID, "PROCESSED")
        assert detail["schemaVersion"] == events.SCHEMA_VERSION
        assert detail["videoId"] == VIDEO_ID
        assert detail["status"] == "PROCESSED"
        assert detail["bucket"] == "video-processed"
        assert detail["originalKey"] == ORIGINAL_KEY
        assert detail["processedKey"] == PROCESSED_KEY
        # Nested envelope detail intact for envelope-shaped readers.
        assert detail["detail"] == {
            "videoId": VIDEO_ID,
            "status": "PROCESSED",
            "bucket": "video-processed",
            "originalKey": ORIGINAL_KEY,
            "processedKey": PROCESSED_KEY,
        }

    def test_bucket_comes_from_env_not_payload(self, deps):
        """AD-4: the ASL carries domain payload only; the detail's bucket
        is the Terraform-set PROCESSED_BUCKET, even if the payload tries
        to supply one."""
        handler(_payload(bucket="attacker-bucket"), None)

        detail = _published_detail(deps)
        assert detail["bucket"] == "video-processed"
        assert detail["detail"]["bucket"] == "video-processed"

    def test_returns_the_envelope(self, deps):
        result = handler(_payload(), None)

        assert set(result) == {"eventId", "schemaVersion", "detail"}

    def test_log_line_emitted(self, deps, caplog):
        """NFR-5: structured logging with videoId/eventId/processedKey."""
        with caplog.at_level(logging.INFO, logger="event_publisher.handler"):
            handler(_payload(), None)

        log_text = " ".join(caplog.messages)
        assert VIDEO_ID in log_text
        assert events.event_id(VIDEO_ID, "PROCESSED") in log_text
        assert PROCESSED_KEY in log_text

    def test_extra_fields_tolerated(self, deps):
        """Input contract: the ASL passes the transcode result; extra
        fields are tolerated."""
        result = handler(_payload(unexpected="field"), None)

        assert result["detail"]["videoId"] == VIDEO_ID


# ---------------------------------------------------------------------------
# Malformed input (matrix rows 2-3)
# ---------------------------------------------------------------------------

class TestMalformedInput:
    @pytest.mark.parametrize("payload", [
        {"originalKey": ORIGINAL_KEY, "processedKey": PROCESSED_KEY},
        {"videoId": "", "originalKey": ORIGINAL_KEY,
         "processedKey": PROCESSED_KEY},
        {"videoId": "   ", "originalKey": ORIGINAL_KEY,
         "processedKey": PROCESSED_KEY},
        {},
    ])
    def test_missing_or_empty_video_id_raises(self, deps, payload):
        with pytest.raises(MalformedInputError):
            handler(payload, None)

    @pytest.mark.parametrize("payload", [
        {"videoId": VIDEO_ID, "processedKey": PROCESSED_KEY},
        {"videoId": VIDEO_ID, "originalKey": "",
         "processedKey": PROCESSED_KEY},
    ])
    def test_missing_or_empty_original_key_raises(self, deps, payload):
        with pytest.raises(MalformedInputError):
            handler(payload, None)

    @pytest.mark.parametrize("payload", [
        {"videoId": VIDEO_ID, "originalKey": ORIGINAL_KEY},
        {"videoId": VIDEO_ID, "originalKey": ORIGINAL_KEY,
         "processedKey": ""},
    ])
    def test_missing_or_empty_processed_key_raises(self, deps, payload):
        with pytest.raises(MalformedInputError):
            handler(payload, None)

    @pytest.mark.parametrize("event", ["not-a-dict", ["list"], None, 42])
    def test_non_dict_event_raises(self, deps, event):
        with pytest.raises(MalformedInputError):
            handler(event, None)

    @pytest.mark.parametrize("payload", [
        {"videoId": 123, "originalKey": ORIGINAL_KEY,
         "processedKey": PROCESSED_KEY},
        {"videoId": VIDEO_ID, "originalKey": 456,
         "processedKey": PROCESSED_KEY},
        {"videoId": None, "originalKey": ORIGINAL_KEY,
         "processedKey": PROCESSED_KEY},
    ])
    def test_non_string_fields_raise(self, deps, payload):
        with pytest.raises(MalformedInputError):
            handler(payload, None)

    def test_malformed_input_publishes_nothing(self, deps):
        with pytest.raises(MalformedInputError):
            handler({"videoId": VIDEO_ID}, None)

        assert deps["eb"].put_calls == []

    def test_whitespace_padded_fields_are_stripped(self, deps):
        """_require_field returns the stripped value."""
        result = handler(
            {"videoId": f"  {VIDEO_ID}  ",
             "originalKey": f"  {ORIGINAL_KEY}  ",
             "processedKey": f"  {PROCESSED_KEY}  "}, None)

        assert result["detail"]["videoId"] == VIDEO_ID
        assert result["detail"]["originalKey"] == ORIGINAL_KEY
        assert result["detail"]["processedKey"] == PROCESSED_KEY


# ---------------------------------------------------------------------------
# Bus rejects entry (matrix row 4)
# ---------------------------------------------------------------------------

class TestBusRejection:
    def test_failed_entry_count_raises(self, deps, eb):
        """A dropped terminal event must not masquerade as success — the
        ASL task fails the execution."""
        eb.failed_entry_count = 1

        with pytest.raises(RuntimeError, match="event publish failed"):
            handler(_payload(), None)


# ---------------------------------------------------------------------------
# Unset env (matrix row 5)
# ---------------------------------------------------------------------------

class TestUnsetEnv:
    def test_missing_processed_bucket_env_raises(self, deps, monkeypatch):
        monkeypatch.delenv("PROCESSED_BUCKET")

        with pytest.raises(RuntimeError, match="PROCESSED_BUCKET"):
            handler(_payload(), None)

    def test_missing_event_bus_name_env_raises(self, deps, monkeypatch):
        monkeypatch.delenv("EVENT_BUS_NAME")

        with pytest.raises(RuntimeError, match="EVENT_BUS_NAME"):
            handler(_payload(), None)

    def test_missing_env_publishes_nothing(self, deps, monkeypatch):
        monkeypatch.delenv("EVENT_BUS_NAME")

        with pytest.raises(RuntimeError):
            handler(_payload(), None)

        assert deps["eb"].put_calls == []


# ---------------------------------------------------------------------------
# Re-invoke / dedupe (matrix row 6)
# ---------------------------------------------------------------------------

class TestReinvoke:
    def test_identical_event_id_both_times(self, deps):
        """NFR-2: eventId is the deterministic UUID5 of (videoId,
        PROCESSED) — a republish is a dedupe, never a new id."""
        r1 = handler(_payload(), None)
        r2 = handler(_payload(), None)

        assert r1["eventId"] == r2["eventId"]
        assert r1 == r2
        assert len(deps["eb"].put_calls) == 2


# ---------------------------------------------------------------------------
# Purity guarantee (AD-4/AD-6)
# ---------------------------------------------------------------------------

class TestPurity:
    def test_handler_module_does_not_import_status(self):
        """The module must not import shared.status (no status writes).
        AST-based so docstrings mentioning it can't false-positive."""
        import ast
        import inspect

        import event_publisher.handler as h
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
        assert "shared.clients" in imported
        assert "shared.events" in imported

    def test_no_dynamodb_client_constructed(self, deps, eb, monkeypatch):
        """AD-4 probe: route shared.clients through a recorder that fails
        on any dynamodb/s3 construction; the handler may only ever ask
        for events."""
        recorder = ClientFactoryRecorder(eb)
        import event_publisher.handler as h
        monkeypatch.setattr(h, "clients", recorder)
        monkeypatch.setattr(h, "_events_client",
                            lambda: recorder.events_client())

        result = handler(_payload(), None)

        assert result["eventId"] == events.event_id(VIDEO_ID, "PROCESSED")
        assert recorder.requested == ["events"]
