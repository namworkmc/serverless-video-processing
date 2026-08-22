"""ATDD suite for Story 3.1: history-consumer Lambda (video.processed ->
status-history, AD-1/AD-6, FR-14/FR-15).

Assertions encode the spec's I/O & Edge-Case Matrix and the test
architect's checklist (T1-T11):

| Scenario              | Expected                                             |
|-----------------------|------------------------------------------------------|
| Happy record          | put_item {eventId, videoId, status, timestamp} with  |
|                       | attribute_not_exists(eventId); summary recorded=1    |
| Duplicate eventId     | ConditionalCheckFailed -> dedupe ack (no raise)      |
| Unknown videoId       | NotFoundError from metadata -> drop + ack, no write  |
| Transient error       | any other error raises (ESM retries)                 |
| detail is a string    | json.loads it, then identical behavior               |
| Malformed record      | skipped (acked), warning log, no write               |
| Non-SQS event         | raises MalformedInputError                           |
| Multiple records      | each processed independently; outcomes tallied       |

Plus the purity guarantee: only a `dynamodb` resource is ever
constructed — never s3/events/states/sqs.

TDD Phase: GREEN
Story: 3-1-history-consumer-recording-terminal-events
"""

import json
import logging
import re

import pytest

from shared import events
from shared.errors import MalformedInputError
from history_consumer.handler import handler

# ---------------------------------------------------------------------------
# Fakes (same pattern as lambdas/sfn_trigger_shim/tests)
# ---------------------------------------------------------------------------


class ConditionalCheckFailedException(Exception):
    """boto3 raises a dynamically generated ClientError subclass named
    after the error code — the class name is the stable signal the
    consumer duck-types on (shared.errors.is_conditional_check_failed)."""


class TransientDynamoError(Exception):
    """Stand-in for any non-conditional DynamoDB failure (network,
    throttle, 5xx) — must propagate so the ESM retries the message."""


class FakeMetadataTable:
    """In-memory video-metadata stand-in for get_record validation."""

    def __init__(self, known_video_ids=(), error=None):
        self.known = set(known_video_ids)
        self.error = error
        self.get_calls = []

    def get_item(self, Key):
        if self.error is not None:
            raise self.error
        self.get_calls.append(Key)
        video_id = Key.get("videoId")
        if video_id in self.known:
            return {"Item": {"videoId": video_id, "status": "PROCESSED"}}
        return {}


class FakeHistoryTable:
    """In-memory status-history stand-in honoring the dedupe condition:
    PutItem with ConditionExpression attribute_not_exists(eventId)."""

    def __init__(self):
        self.items = {}
        self.put_calls = []

    def put_item(self, Item, ConditionExpression=None):
        self.put_calls.append(
            {"Item": Item, "ConditionExpression": ConditionExpression})
        event_id = Item.get("eventId")
        if (ConditionExpression == "attribute_not_exists(eventId)"
                and event_id in self.items):
            raise ConditionalCheckFailedException(
                f"The conditional request failed: {event_id}")
        self.items[event_id] = dict(Item)


class ClientFactoryRecorder:
    """Wraps shared.clients to record every client construction — the
    purity probe (only dynamodb may ever be built)."""

    def __init__(self, tables):
        self._tables = tables
        self.requested = []

    def dynamodb_resource(self):
        self.requested.append("dynamodb")
        return self

    def dynamodb_table(self, name):
        self.requested.append("dynamodb")
        return self._tables[name]

    def s3_client(self):
        self.requested.append("s3")
        raise AssertionError("purity violation: s3 client constructed")

    def events_client(self):
        self.requested.append("events")
        raise AssertionError("purity violation: events client constructed")

    def states_client(self):
        self.requested.append("states")
        raise AssertionError("purity violation: states client constructed")

    def sqs_client(self):
        self.requested.append("sqs")
        raise AssertionError("purity violation: sqs client constructed")


# ---------------------------------------------------------------------------
# Fixtures — event payloads built from the shared layer's real wire shape
# (envelope + promoted detail fields), never hand-typed dicts.
# ---------------------------------------------------------------------------

VIDEO_ID = "11111111-2222-3333-4444-555555555555"
ORIGINAL_KEY = f"{VIDEO_ID}/demo.mp4"
PROCESSED_KEY = f"processed/{VIDEO_ID}/demo.mp4"
EVENT_ID = events.event_id(VIDEO_ID, "PROCESSED")
METADATA_TABLE = "video-metadata"
HISTORY_TABLE = "status-history"

_ISO8601_UTC = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z\Z")


def _flat_detail(**overrides):
    """The flat video.processed detail exactly as the event-publisher
    publishes it: {**envelope, **envelope['detail']}."""
    envelope = events.build_envelope(
        events.EVENT_PROCESSED,
        events.processed_detail(
            VIDEO_ID, "video-processed", ORIGINAL_KEY, PROCESSED_KEY))
    detail = {**envelope, **envelope["detail"]}
    detail.update(overrides)
    return detail


def _eb_event(detail=None):
    """The FULL EventBridge event the rule delivers as the SQS body."""
    return {
        "version": "0",
        "id": "random-bridge-id-not-used-for-dedupe",
        "detail-type": "video.processed",
        "source": "event-publisher",
        "account": "000000000000",
        "time": "2026-08-22T12:00:00Z",
        "region": "us-east-1",
        "resources": [],
        "detail": _flat_detail() if detail is None else detail,
    }


def _sqs_event(*bodies):
    """An SQS event as the event-source mapping delivers it."""
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": body}
            for i, body in enumerate(bodies)
        ],
    }


@pytest.fixture
def tables():
    return {
        METADATA_TABLE: FakeMetadataTable(known_video_ids={VIDEO_ID}),
        HISTORY_TABLE: FakeHistoryTable(),
    }


@pytest.fixture
def deps(tables, monkeypatch):
    """Wire the fake tables + env vars into the handler (NFR-4)."""
    monkeypatch.setenv("METADATA_TABLE", METADATA_TABLE)
    monkeypatch.setenv("HISTORY_TABLE", HISTORY_TABLE)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    import history_consumer.handler as h
    monkeypatch.setattr(
        h, "_metadata_table", lambda: tables[METADATA_TABLE])
    monkeypatch.setattr(h, "_history_table", lambda: tables[HISTORY_TABLE])
    return tables


# ---------------------------------------------------------------------------
# T1 — Happy record (matrix row 1)
# ---------------------------------------------------------------------------

class TestHappyRecord:
    def test_put_item_called_with_exactly_the_history_entry(self, deps):
        handler(_sqs_event(json.dumps(_eb_event())), None)
        history = deps[HISTORY_TABLE]
        assert len(history.put_calls) == 1
        call = history.put_calls[0]
        item = call["Item"]
        assert set(item) == {"eventId", "videoId", "status", "timestamp"}
        assert item["eventId"] == EVENT_ID
        assert item["videoId"] == VIDEO_ID
        assert item["status"] == "PROCESSED"

    def test_condition_is_attribute_not_exists_event_id(self, deps):
        handler(_sqs_event(json.dumps(_eb_event())), None)
        call = deps[HISTORY_TABLE].put_calls[0]
        assert call["ConditionExpression"] == "attribute_not_exists(eventId)"

    def test_timestamp_is_iso8601_utc(self, deps):
        handler(_sqs_event(json.dumps(_eb_event())), None)
        item = deps[HISTORY_TABLE].put_calls[0]["Item"]
        assert _ISO8601_UTC.match(item["timestamp"])

    def test_returns_recorded_summary(self, deps):
        summary = handler(_sqs_event(json.dumps(_eb_event())), None)
        assert summary == {"processed": 1, "recorded": 1, "deduped": 0,
                           "dropped": 0, "skipped": 0}

    def test_metadata_validated_before_write(self, deps):
        handler(_sqs_event(json.dumps(_eb_event())), None)
        assert deps[METADATA_TABLE].get_calls == [{"videoId": VIDEO_ID}]

    def test_log_line_emitted(self, deps, caplog):
        with caplog.at_level(logging.INFO):
            handler(_sqs_event(json.dumps(_eb_event())), None)
        assert any(VIDEO_ID in r.message and EVENT_ID in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# T2 — Duplicate eventId (matrix row 2)
# ---------------------------------------------------------------------------

class TestDuplicateEventId:
    def test_conditional_check_failed_is_acked(self, deps):
        event = _sqs_event(json.dumps(_eb_event()))
        handler(event, None)  # first delivery records
        summary = handler(event, None)  # redelivery collides
        assert summary["deduped"] == 1
        assert summary["recorded"] == 0

    def test_still_exactly_one_entry(self, deps):
        event = _sqs_event(json.dumps(_eb_event()))
        handler(event, None)
        handler(event, None)
        assert list(deps[HISTORY_TABLE].items) == [EVENT_ID]

    def test_dedupe_logged(self, deps, caplog):
        event = _sqs_event(json.dumps(_eb_event()))
        handler(event, None)
        with caplog.at_level(logging.INFO):
            handler(event, None)
        assert any("dedupe" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# T3 — Poison: unknown videoId (matrix row 3, FR-15)
# ---------------------------------------------------------------------------

class TestPoisonUnknownVideoId:
    def test_no_write_and_acked(self, deps):
        deps[METADATA_TABLE].known.clear()
        summary = handler(_sqs_event(json.dumps(_eb_event())), None)
        assert summary["dropped"] == 1
        assert summary["recorded"] == 0
        assert deps[HISTORY_TABLE].put_calls == []

    def test_unknown_video_id_detected_via_metadata(self, deps):
        deps[METADATA_TABLE].known.clear()
        summary = handler(_sqs_event(json.dumps(_eb_event())), None)
        assert summary["dropped"] == 1
        assert deps[METADATA_TABLE].get_calls == [{"videoId": VIDEO_ID}]

    def test_drop_logged(self, deps, caplog):
        deps[METADATA_TABLE].known.clear()
        with caplog.at_level(logging.WARNING):
            handler(_sqs_event(json.dumps(_eb_event())), None)
        assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# T4/T5 — Transient errors raise (matrix row 4, FR-15)
# ---------------------------------------------------------------------------

class TestTransientErrors:
    def test_metadata_transient_error_raises(self, deps):
        deps[METADATA_TABLE].error = TransientDynamoError("throttled")
        with pytest.raises(TransientDynamoError):
            handler(_sqs_event(json.dumps(_eb_event())), None)
        assert deps[HISTORY_TABLE].put_calls == []

    def test_history_write_transient_error_raises(self, deps):
        history = deps[HISTORY_TABLE]
        real_put = history.put_item

        def flaky(**kwargs):
            history.put_calls.append(kwargs)
            raise TransientDynamoError("network")

        history.put_item = flaky
        with pytest.raises(TransientDynamoError):
            handler(_sqs_event(json.dumps(_eb_event())), None)
        history.put_item = real_put


# ---------------------------------------------------------------------------
# T6 — detail arrives JSON-stringified (matrix row 5)
# ---------------------------------------------------------------------------

class TestStringifiedDetail:
    def test_identical_behavior(self, deps):
        event = _eb_event()
        event["detail"] = json.dumps(event["detail"])
        summary = handler(_sqs_event(json.dumps(event)), None)
        assert summary["recorded"] == 1
        assert deps[HISTORY_TABLE].items[EVENT_ID]["videoId"] == VIDEO_ID


# ---------------------------------------------------------------------------
# T7 — Malformed records skipped (matrix row 6)
# ---------------------------------------------------------------------------

class TestMalformedRecords:
    @pytest.mark.parametrize("body", [
        "not-json-at-all",
        json.dumps({"no-detail": True}),
        json.dumps({"detail": "not-json-either"}),
        json.dumps({"detail": ["not", "a", "dict"]}),
        "[1, 2, 3]",                          # body parses to a non-dict
        json.dumps({"detail": "[1, 2, 3]"}),  # stringified detail -> non-dict
    ])
    def test_unparseable_body_skipped(self, deps, body):
        summary = handler(_sqs_event(body), None)
        assert summary["skipped"] == 1
        assert deps[HISTORY_TABLE].put_calls == []

    @pytest.mark.parametrize("field", ["eventId", "videoId", "status"])
    def test_missing_required_field_skipped(self, deps, field):
        detail = _flat_detail()
        del detail[field]
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["skipped"] == 1
        assert deps[HISTORY_TABLE].put_calls == []

    @pytest.mark.parametrize("field", ["eventId", "videoId", "status"])
    def test_empty_required_field_skipped(self, deps, field):
        detail = _flat_detail(**{field: "   "})
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["skipped"] == 1
        assert deps[HISTORY_TABLE].put_calls == []

    @pytest.mark.parametrize("field", ["eventId", "videoId", "status"])
    @pytest.mark.parametrize("bad_value", [123, {"nested": True}, ["x"]])
    def test_non_string_required_field_skipped(self, deps, field, bad_value):
        detail = _flat_detail(**{field: bad_value})
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["skipped"] == 1
        assert deps[HISTORY_TABLE].put_calls == []

    def test_unknown_status_skipped(self, deps):
        """A fabricated event with a KNOWN videoId but a status outside
        shared.status.STATUSES must not enter the audit trail."""
        detail = _flat_detail(status="NOT_A_STATUS")
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["skipped"] == 1
        assert deps[HISTORY_TABLE].put_calls == []

    def test_non_string_body_skipped(self, deps):
        """A body that is not a string (e.g. already a dict) cannot be an
        SQS delivery — skipped, not crashed."""
        summary = handler({"Records": [{"messageId": "m", "body": {
            "detail": _flat_detail()}}]}, None)
        assert summary["skipped"] == 1
        assert deps[HISTORY_TABLE].put_calls == []

    def test_malformed_record_warns(self, deps, caplog):
        with caplog.at_level(logging.WARNING):
            handler(_sqs_event("not-json"), None)
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_non_dict_record_skipped(self, deps):
        summary = handler({"Records": ["not-a-dict"]}, None)
        assert summary["skipped"] == 1


# ---------------------------------------------------------------------------
# T8 — Non-SQS event (matrix row 7)
# ---------------------------------------------------------------------------

class TestNonSqsEvent:
    @pytest.mark.parametrize("event", [
        None,
        "not-a-dict",
        {},
        {"Records": "not-a-list"},
    ])
    def test_raises_malformed_input(self, deps, event):
        with pytest.raises(MalformedInputError):
            handler(event, None)

    def test_empty_records_list_is_a_noop(self, deps):
        summary = handler({"Records": []}, None)
        assert summary == {"processed": 0, "recorded": 0, "deduped": 0,
                           "dropped": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# T9 — Multiple records (matrix row 8)
# ---------------------------------------------------------------------------

class TestMultipleRecords:
    def test_each_record_processed_independently(self, deps):
        deps[METADATA_TABLE].known.add("other-video")
        good = json.dumps(_eb_event())
        poison_detail = _flat_detail(videoId="unknown-video")
        poison = json.dumps(_eb_event(poison_detail))
        summary = handler(_sqs_event(good, poison, "garbage"), None)
        assert summary == {"processed": 3, "recorded": 1, "deduped": 0,
                           "dropped": 1, "skipped": 1}
        assert list(deps[HISTORY_TABLE].items) == [EVENT_ID]

    def test_duplicate_in_same_batch_deduped(self, deps):
        body = json.dumps(_eb_event())
        summary = handler(_sqs_event(body, body), None)
        assert summary == {"processed": 2, "recorded": 1, "deduped": 1,
                           "dropped": 0, "skipped": 0}
        assert list(deps[HISTORY_TABLE].items) == [EVENT_ID]

    def test_transient_failure_mid_batch_redelivery_dedupes(self, deps):
        """[good, raises] batch: the first record writes, the second
        raises transiently; SQS redelivers the whole batch — the first
        record dedupes, the second records. Still exactly one entry per
        eventId."""
        history = deps[HISTORY_TABLE]
        deps[METADATA_TABLE].known.add("other-video")
        good = json.dumps(_eb_event())
        other_id = events.event_id("other-video", "PROCESSED")
        other = json.dumps(_eb_event(_flat_detail(
            videoId="other-video", eventId=other_id)))

        real_put = history.put_item
        calls = {"n": 0}

        def fail_second(**kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise TransientDynamoError("network")
            return real_put(**kwargs)

        history.put_item = fail_second
        with pytest.raises(TransientDynamoError):
            handler(_sqs_event(good, other), None)
        assert list(history.items) == [EVENT_ID]

        history.put_item = real_put
        summary = handler(_sqs_event(good, other), None)
        assert summary == {"processed": 2, "recorded": 1, "deduped": 1,
                           "dropped": 0, "skipped": 0}
        assert set(history.items) == {EVENT_ID, other_id}


# ---------------------------------------------------------------------------
# T10 — Purity probe: only a dynamodb resource is ever constructed
# ---------------------------------------------------------------------------

class TestPurity:
    def test_only_dynamodb_constructed(self, tables, monkeypatch):
        """Purity probe: route shared.clients through a recorder that
        fails on any s3/events/states/sqs construction; the handler may
        only ever ask for dynamodb tables. Deliberately does NOT use the
        `deps` fixture (which patches the table accessors): only
        `h.clients` is patched, so the REAL accessors run and their
        client choices are what gets recorded."""
        monkeypatch.setenv("METADATA_TABLE", METADATA_TABLE)
        monkeypatch.setenv("HISTORY_TABLE", HISTORY_TABLE)
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        recorder = ClientFactoryRecorder(tables)
        import history_consumer.handler as h
        monkeypatch.setattr(h, "clients", recorder)

        summary = handler(_sqs_event(json.dumps(_eb_event())), None)

        assert summary["recorded"] == 1
        assert recorder.requested
        assert set(recorder.requested) == {"dynamodb"}


# ---------------------------------------------------------------------------
# T11 — eventId provenance: the dedupe key is the deterministic UUID5 of
# (videoId, PROCESSED), never the EventBridge top-level id.
# ---------------------------------------------------------------------------

class TestEventIdProvenance:
    def test_entry_keyed_by_deterministic_event_id(self, deps):
        handler(_sqs_event(json.dumps(_eb_event())), None)
        assert list(deps[HISTORY_TABLE].items) == [
            events.event_id(VIDEO_ID, "PROCESSED")]

    def test_bridge_top_level_id_ignored(self, deps):
        event = _eb_event()
        event["id"] = "bridge-id-must-not-appear-anywhere"
        handler(_sqs_event(json.dumps(event)), None)
        item = deps[HISTORY_TABLE].put_calls[0]["Item"]
        assert item["eventId"] == EVENT_ID
        assert "bridge-id-must-not-appear-anywhere" not in json.dumps(item)


# ---------------------------------------------------------------------------
# Wire-shape coupling: the producer's ACTUAL output must be consumable.
# _flat_detail() rebuilds the promotion recipe inline, so a producer-side
# field rename/nesting would silently empty the audit trail while both
# suites stay green — this test fails instead.
# ---------------------------------------------------------------------------

class TestProducerWireShapeCoupling:
    def test_publisher_output_is_consumed_as_recorded(self, deps,
                                                      monkeypatch):
        import event_publisher.handler as pub

        captured = {}

        class FakeEventsClient:
            def put_events(self, Entries):
                captured["entries"] = Entries
                return {"FailedEntryCount": 0}

        monkeypatch.setenv("PROCESSED_BUCKET", "processed-bucket")
        monkeypatch.setenv("EVENT_BUS_NAME", "video-bus")
        monkeypatch.setattr(
            pub, "_events_client", lambda: FakeEventsClient())

        pub.handler({
            "videoId": VIDEO_ID,
            "originalKey": ORIGINAL_KEY,
            "processedKey": PROCESSED_KEY,
        }, None)

        wire_detail = json.loads(captured["entries"][0]["Detail"])
        summary = handler(
            _sqs_event(json.dumps(_eb_event(wire_detail))), None)
        assert summary["recorded"] == 1
        assert deps[HISTORY_TABLE].items[EVENT_ID]["videoId"] == VIDEO_ID


# ---------------------------------------------------------------------------
# Config-not-code (NFR-4)
# ---------------------------------------------------------------------------

class TestConfigNotCode:
    """No `deps` fixture here — the REAL accessors run (unpatched)."""

    def test_missing_metadata_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("METADATA_TABLE", raising=False)
        import history_consumer.handler as h
        with pytest.raises(RuntimeError):
            h._metadata_table()

    def test_missing_history_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("HISTORY_TABLE", raising=False)
        import history_consumer.handler as h
        with pytest.raises(RuntimeError):
            h._history_table()
