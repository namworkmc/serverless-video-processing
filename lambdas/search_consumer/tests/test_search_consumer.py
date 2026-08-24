"""ATDD suite for Story 4.1: search-consumer Lambda (video.processed ->
search-index, FR-17, AD-1/AD-3/AD-6, NFR-1).

Assertions encode the spec's I/O & Edge-Case Matrix and the test
architect's checklist (T1-T14):

| Scenario              | Expected                                             |
|-----------------------|------------------------------------------------------|
| Happy index           | plain put_item {videoId, title, processedKey,        |
|                       | indexedAt}, NO ConditionExpression; indexed=1        |
| FAILED filter         | no put_item, no metadata get_item; filtered=1        |
| Non-terminal filter   | UPLOADED/PROCESSING -> filtered (status ==           |
|                       | PROCESSED, not TERMINAL_STATUSES)                    |
| Redelivery            | overwrite by PK videoId; still one item; both        |
|                       | calls indexed=1; domain fields stable, indexedAt     |
|                       | refreshes (review loop 1)                            |
| Poison                | NotFoundError from metadata -> drop + ack            |
| Transient errors      | any other error raises (ESM retries)                 |
| Stringified detail    | parsed, identical to happy                           |
| Malformed record      | skipped (acked), logged, no write                    |
| Unknown status        | ARCHIVED -> filtered (a status decision, made        |
|                       | before any legality concern)                         |
| Non-SQS event         | raises MalformedInputError                           |
| Mixed batch           | per-record outcomes tallied, in order                |

Plus the purity guarantee (only a dynamodb resource is ever constructed)
and the producer->consumer wire-shape coupling.

Fixture discipline: PROCESSED events built from the shared layer's real
wire shape (`build_envelope` + flat promotion); sole exception is
non-PROCESSED statuses, which `build_envelope` rejects by design (AD-6
guard) — those are hand-crafted flat details.

TDD Phase: authored RED against a stub handler; GREEN since 2026-08-24
Story: 4-1-search-consumer-indexing-processed-videos
"""

import json
import logging
import re

import pytest

from shared import events
from shared.errors import MalformedInputError

# ---------------------------------------------------------------------------
# Fakes (same pattern as lambdas/history_consumer/tests)
# ---------------------------------------------------------------------------


class TransientDynamoError(Exception):
    """Stand-in for any non-poison DynamoDB failure (network, throttle,
    5xx) — must propagate so the ESM retries the message."""


class FakeMetadataTable:
    """In-memory video-metadata stand-in: get_item configurable to return
    the record (with its title), poison (missing), or raise."""

    def __init__(self, records=None, error=None):
        self.records = dict(records or {})
        self.error = error
        self.get_calls = []

    def get_item(self, Key):
        if self.error is not None:
            raise self.error
        self.get_calls.append(Key)
        video_id = Key.get("videoId")
        if video_id in self.records:
            return {"Item": dict(self.records[video_id])}
        return {}


class FakeIndexTable:
    """In-memory search-index stand-in honoring upsert-by-PK: a plain
    PutItem keyed by videoId overwrites; there is no condition path.
    Records the FULL call kwargs so tests can assert that no
    ConditionExpression (or anything else) is ever passed."""

    def __init__(self):
        self.items = {}
        self.put_calls = []

    def put_item(self, Item, **kwargs):
        self.put_calls.append({"Item": Item, **kwargs})
        video_id = Item.get("videoId")
        self.items[video_id] = dict(Item)


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
# (envelope + promoted detail fields), never hand-typed dicts. Sole
# exception: non-PROCESSED statuses (build_envelope rejects them), which
# are produced by post-build mutation of an otherwise-real flat detail.
# ---------------------------------------------------------------------------

VIDEO_ID = "11111111-2222-3333-4444-555555555555"
ORIGINAL_KEY = f"{VIDEO_ID}/demo.mp4"
PROCESSED_KEY = f"processed/{VIDEO_ID}/demo.mp4"
EVENT_ID = events.event_id(VIDEO_ID, "PROCESSED")
TITLE = "Demo Video Title"
METADATA_TABLE = "video-metadata"
INDEX_TABLE = "search-index"

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
        "time": "2026-08-23T12:00:00Z",
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
        METADATA_TABLE: FakeMetadataTable(records={
            VIDEO_ID: {
                "videoId": VIDEO_ID,
                "title": TITLE,
                "status": "PROCESSED",
                "bucket": "video-uploads",
                "originalKey": ORIGINAL_KEY,
            },
        }),
        INDEX_TABLE: FakeIndexTable(),
    }


@pytest.fixture
def deps(tables, monkeypatch):
    """Wire the fake tables + env vars into the handler (NFR-4)."""
    monkeypatch.setenv("METADATA_TABLE", METADATA_TABLE)
    monkeypatch.setenv("SEARCH_INDEX_TABLE", INDEX_TABLE)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    import search_consumer.handler as h
    monkeypatch.setattr(
        h, "_metadata_table", lambda: tables[METADATA_TABLE])
    monkeypatch.setattr(h, "_index_table", lambda: tables[INDEX_TABLE])
    return tables


# ---------------------------------------------------------------------------
# T1 — Happy index (matrix row 1)
# ---------------------------------------------------------------------------

class TestHappyIndex:
    def test_put_item_called_with_exactly_the_index_entry(self, deps):
        from search_consumer.handler import handler
        handler(_sqs_event(json.dumps(_eb_event())), None)
        index = deps[INDEX_TABLE]
        assert len(index.put_calls) == 1
        item = index.put_calls[0]["Item"]
        assert set(item) == {
            "videoId", "title", "processedKey", "indexedAt"}
        assert item["videoId"] == VIDEO_ID
        assert item["processedKey"] == PROCESSED_KEY

    def test_plain_putitem_no_condition_expression(self, deps):
        from search_consumer.handler import handler
        handler(_sqs_event(json.dumps(_eb_event())), None)
        call = deps[INDEX_TABLE].put_calls[0]
        # The PK IS the dedupe (NFR-1): a plain PutItem, nothing else —
        # in particular NOT the history consumer's attribute_not_exists.
        assert set(call) == {"Item"}
        assert not any(
            k.lower().startswith("condition") for k in call)

    def test_title_from_metadata_record_not_event(self, deps):
        from search_consumer.handler import handler
        flat = _flat_detail()
        assert "title" not in flat  # AD-6: the detail carries no title
        handler(_sqs_event(json.dumps(_eb_event(flat))), None)
        assert deps[INDEX_TABLE].items[VIDEO_ID]["title"] == TITLE

    def test_indexed_at_is_iso8601_utc(self, deps):
        from search_consumer.handler import handler
        handler(_sqs_event(json.dumps(_eb_event())), None)
        item = deps[INDEX_TABLE].put_calls[0]["Item"]
        assert _ISO8601_UTC.match(item["indexedAt"])

    def test_returns_indexed_summary(self, deps):
        from search_consumer.handler import handler
        summary = handler(_sqs_event(json.dumps(_eb_event())), None)
        assert summary == {"processed": 1, "indexed": 1, "filtered": 0,
                           "dropped": 0, "skipped": 0}

    def test_metadata_validated_before_write(self, deps):
        from search_consumer.handler import handler
        handler(_sqs_event(json.dumps(_eb_event())), None)
        assert deps[METADATA_TABLE].get_calls == [{"videoId": VIDEO_ID}]

    def test_log_line_emitted(self, deps, caplog):
        from search_consumer.handler import handler
        with caplog.at_level(logging.INFO):
            handler(_sqs_event(json.dumps(_eb_event())), None)
        assert any(VIDEO_ID in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# T2 — Status filter: FAILED never indexed (matrix row 2, AC3, FR-17 core)
# ---------------------------------------------------------------------------

class TestStatusFilterFailed:
    def test_no_write_no_lookup_filtered(self, deps):
        from search_consumer.handler import handler
        # Hand-crafted flat detail (build_envelope rejects FAILED).
        detail = _flat_detail(status="FAILED")
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary == {"processed": 1, "indexed": 0, "filtered": 1,
                           "dropped": 0, "skipped": 0}
        assert deps[INDEX_TABLE].put_calls == []
        # Filter runs BEFORE the metadata lookup — zero table access.
        assert deps[METADATA_TABLE].get_calls == []

    def test_failed_filter_does_not_raise(self, deps):
        from search_consumer.handler import handler
        detail = _flat_detail(status="FAILED")
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["filtered"] == 1


# ---------------------------------------------------------------------------
# T3 — Status filter: non-terminal statuses (matrix row 2 continued)
# ---------------------------------------------------------------------------

class TestStatusFilterNonTerminal:
    @pytest.mark.parametrize("status_value", ["UPLOADED", "PROCESSING"])
    def test_non_terminal_filtered_without_table_access(
            self, deps, status_value):
        from search_consumer.handler import handler
        detail = _flat_detail(status=status_value)
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["filtered"] == 1
        assert summary["indexed"] == 0
        assert deps[INDEX_TABLE].put_calls == []
        assert deps[METADATA_TABLE].get_calls == []


# ---------------------------------------------------------------------------
# T4 — Redelivery overwrite (matrix row 3, AC4, NFR-1)
# ---------------------------------------------------------------------------

class TestRedeliveryOverwrite:
    def test_same_event_twice_still_one_item(self, deps):
        from search_consumer.handler import handler
        event = _sqs_event(json.dumps(_eb_event()))
        first = handler(event, None)
        second = handler(event, None)
        assert first["indexed"] == 1
        assert second["indexed"] == 1
        assert list(deps[INDEX_TABLE].items) == [VIDEO_ID]

    def test_overwritten_fields_identical(self, deps, monkeypatch):
        from search_consumer.handler import handler
        import search_consumer.handler as h
        stamps = iter(["2026-08-24T00:00:01Z", "2026-08-24T00:00:02Z"])
        monkeypatch.setattr(h.status, "_now_iso", lambda: next(stamps))
        event = _sqs_event(json.dumps(_eb_event()))
        handler(event, None)
        snapshot = dict(deps[INDEX_TABLE].items[VIDEO_ID])
        handler(event, None)
        rewritten = deps[INDEX_TABLE].items[VIDEO_ID]
        # NFR-1 dedupe: the DOMAIN fields are identical across redelivery.
        # indexedAt intentionally REFRESHES — plain-PutItem upsert
        # semantics pinned by review loop 1 (spec change log).
        for field in ("videoId", "title", "processedKey"):
            assert rewritten[field] == snapshot[field]
        assert rewritten["indexedAt"] == "2026-08-24T00:00:02Z"


# ---------------------------------------------------------------------------
# T5 — Poison: unknown videoId (matrix row 4, FR-15)
# ---------------------------------------------------------------------------

class TestPoisonUnknownVideoId:
    def test_no_write_and_acked(self, deps):
        from search_consumer.handler import handler
        deps[METADATA_TABLE].records.clear()
        summary = handler(_sqs_event(json.dumps(_eb_event())), None)
        assert summary["dropped"] == 1
        assert summary["indexed"] == 0
        assert deps[INDEX_TABLE].put_calls == []

    def test_unknown_video_id_detected_via_metadata(self, deps):
        from search_consumer.handler import handler
        deps[METADATA_TABLE].records.clear()
        summary = handler(_sqs_event(json.dumps(_eb_event())), None)
        assert summary["dropped"] == 1
        assert deps[METADATA_TABLE].get_calls == [{"videoId": VIDEO_ID}]

    def test_drop_logged(self, deps, caplog):
        from search_consumer.handler import handler
        deps[METADATA_TABLE].records.clear()
        with caplog.at_level(logging.WARNING):
            handler(_sqs_event(json.dumps(_eb_event())), None)
        assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# Unusable metadata title — deterministic poison like an unknown videoId
# (review loop 1): dropped + acked, never retried.
# ---------------------------------------------------------------------------

class TestUnusableMetadataTitle:
    @pytest.mark.parametrize("bad_title", [None, "", "   ", 123])
    def test_dropped_without_write(self, deps, bad_title):
        from search_consumer.handler import handler
        if bad_title is None:
            deps[METADATA_TABLE].records[VIDEO_ID].pop("title")
        else:
            deps[METADATA_TABLE].records[VIDEO_ID]["title"] = bad_title
        summary = handler(_sqs_event(json.dumps(_eb_event())), None)
        assert summary == {"processed": 1, "indexed": 0, "filtered": 0,
                           "dropped": 1, "skipped": 0}
        assert deps[INDEX_TABLE].put_calls == []


# ---------------------------------------------------------------------------
# T6/T7 — Transient errors raise (matrix rows 5–6, FR-15)
# ---------------------------------------------------------------------------

class TestTransientErrors:
    def test_metadata_transient_error_raises(self, deps):
        from search_consumer.handler import handler
        deps[METADATA_TABLE].error = TransientDynamoError("throttled")
        with pytest.raises(TransientDynamoError):
            handler(_sqs_event(json.dumps(_eb_event())), None)
        assert deps[INDEX_TABLE].put_calls == []

    def test_index_write_transient_error_raises(self, deps):
        from search_consumer.handler import handler
        index = deps[INDEX_TABLE]
        real_put = index.put_item

        def flaky(Item, **kwargs):
            index.put_calls.append({"Item": Item, **kwargs})
            raise TransientDynamoError("network")

        index.put_item = flaky
        with pytest.raises(TransientDynamoError):
            handler(_sqs_event(json.dumps(_eb_event())), None)
        index.put_item = real_put


# ---------------------------------------------------------------------------
# T8 — detail arrives JSON-stringified (matrix row 7)
# ---------------------------------------------------------------------------

class TestStringifiedDetail:
    def test_identical_behavior(self, deps):
        from search_consumer.handler import handler
        event = _eb_event()
        event["detail"] = json.dumps(event["detail"])
        summary = handler(_sqs_event(json.dumps(event)), None)
        assert summary["indexed"] == 1
        assert deps[INDEX_TABLE].items[VIDEO_ID]["videoId"] == VIDEO_ID


# ---------------------------------------------------------------------------
# T9 — Malformed records skipped (matrix row 8)
# ---------------------------------------------------------------------------

_REQUIRED = ("eventId", "videoId", "status", "processedKey")


class TestMalformedRecords:
    @pytest.mark.parametrize("body", [
        "not-json-at-all",
        json.dumps({"no-detail": True}),
        json.dumps({"detail": "not-json-either"}),
        json.dumps({"detail": ["not", "a", "dict"]}),
        "[1, 2, 3]",                          # body parses to a non-dict
        json.dumps({"detail": "[1, 2, 3]"}),  # stringified -> non-dict
    ])
    def test_unparseable_body_skipped(self, deps, body):
        from search_consumer.handler import handler
        summary = handler(_sqs_event(body), None)
        assert summary["skipped"] == 1
        assert deps[INDEX_TABLE].put_calls == []
        assert deps[METADATA_TABLE].get_calls == []

    @pytest.mark.parametrize("field", _REQUIRED)
    def test_missing_required_field_skipped(self, deps, field):
        from search_consumer.handler import handler
        detail = _flat_detail()
        del detail[field]
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["skipped"] == 1
        assert deps[INDEX_TABLE].put_calls == []
        assert deps[METADATA_TABLE].get_calls == []

    @pytest.mark.parametrize("field", _REQUIRED)
    def test_empty_required_field_skipped(self, deps, field):
        from search_consumer.handler import handler
        detail = _flat_detail(**{field: "   "})
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["skipped"] == 1
        assert deps[INDEX_TABLE].put_calls == []
        assert deps[METADATA_TABLE].get_calls == []

    @pytest.mark.parametrize("field", _REQUIRED)
    @pytest.mark.parametrize("bad_value", [123, {"nested": True}, ["x"]])
    def test_non_string_required_field_skipped(self, deps, field, bad_value):
        from search_consumer.handler import handler
        detail = _flat_detail(**{field: bad_value})
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary["skipped"] == 1
        assert deps[INDEX_TABLE].put_calls == []
        assert deps[METADATA_TABLE].get_calls == []

    def test_malformed_record_warns(self, deps, caplog):
        from search_consumer.handler import handler
        with caplog.at_level(logging.WARNING):
            handler(_sqs_event("not-json"), None)
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_non_dict_record_skipped(self, deps):
        from search_consumer.handler import handler
        summary = handler({"Records": ["not-a-dict"]}, None)
        assert summary["skipped"] == 1

    def test_non_string_body_skipped(self, deps):
        from search_consumer.handler import handler
        summary = handler({"Records": [{"messageId": "m", "body": {
            "detail": _flat_detail()}}]}, None)
        assert summary["skipped"] == 1
        assert deps[INDEX_TABLE].put_calls == []

    def test_deeply_nested_body_skipped(self, deps):
        """json.loads raises RecursionError (not ValueError) past the
        interpreter nesting limit — still a malformed record: skipped."""
        from search_consumer.handler import handler
        body = "1"
        for _ in range(20000):
            body = f"[{body}]"
        summary = handler(_sqs_event(body), None)
        assert summary["skipped"] == 1
        assert deps[INDEX_TABLE].put_calls == []


# ---------------------------------------------------------------------------
# T10 — Unknown status string counts as FILTERED (pins the spec bullet)
# ---------------------------------------------------------------------------

class TestUnknownStatusFiltered:
    def test_archived_is_filtered_before_metadata(self, deps):
        from search_consumer.handler import handler
        detail = _flat_detail(status="ARCHIVED")
        summary = handler(_sqs_event(json.dumps(_eb_event(detail))), None)
        assert summary == {"processed": 1, "indexed": 0, "filtered": 1,
                           "dropped": 0, "skipped": 0}
        assert deps[INDEX_TABLE].put_calls == []
        assert deps[METADATA_TABLE].get_calls == []


# ---------------------------------------------------------------------------
# T11 — Non-SQS event (matrix row 10)
# ---------------------------------------------------------------------------

class TestNonSqsEvent:
    @pytest.mark.parametrize("event", [
        None,
        "not-a-dict",
        {},
        {"Records": "not-a-list"},
        {"no-records-key": True},
    ])
    def test_raises_malformed_input(self, deps, event):
        from search_consumer.handler import handler
        with pytest.raises(MalformedInputError):
            handler(event, None)

    def test_empty_records_list_is_a_noop(self, deps):
        from search_consumer.handler import handler
        summary = handler({"Records": []}, None)
        assert summary == {"processed": 0, "indexed": 0, "filtered": 0,
                           "dropped": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# T12 — Mixed batch tallied per record, in order (matrix row 11)
# ---------------------------------------------------------------------------

class TestMixedBatch:
    def test_each_record_processed_independently(self, deps):
        from search_consumer.handler import handler
        failed = _flat_detail(status="FAILED")
        poison = _flat_detail(videoId="unknown-video")
        summary = handler(_sqs_event(
            json.dumps(_eb_event()),                     # -> indexed
            json.dumps(_eb_event(failed)),               # -> filtered
            json.dumps(_eb_event(poison)),               # -> dropped
            "garbage",                                   # -> skipped
        ), None)
        assert summary == {"processed": 4, "indexed": 1, "filtered": 1,
                           "dropped": 1, "skipped": 1}
        assert list(deps[INDEX_TABLE].items) == [VIDEO_ID]


# ---------------------------------------------------------------------------
# T13 — Purity probe: only a dynamodb resource is ever constructed
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
        monkeypatch.setenv("SEARCH_INDEX_TABLE", INDEX_TABLE)
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        import search_consumer.handler as h
        recorder = ClientFactoryRecorder(tables)
        monkeypatch.setattr(h, "clients", recorder)

        summary = h.handler(_sqs_event(json.dumps(_eb_event())), None)

        assert summary["indexed"] == 1
        assert recorder.requested
        assert set(recorder.requested) == {"dynamodb"}


# ---------------------------------------------------------------------------
# T14 — Wire-shape coupling: the producer's ACTUAL output must be
# consumable (producer->consumer contract, 3.1 review-loop pattern).
# ---------------------------------------------------------------------------

class TestProducerWireShapeCoupling:
    def test_publisher_output_is_consumed_as_indexed(self, deps,
                                                     monkeypatch):
        import event_publisher.handler as pub
        from search_consumer.handler import handler

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
        assert summary["indexed"] == 1
        item = deps[INDEX_TABLE].items[VIDEO_ID]
        assert item["title"] == TITLE
        assert item["processedKey"] == PROCESSED_KEY


# ---------------------------------------------------------------------------
# Config-not-code (NFR-4)
# ---------------------------------------------------------------------------

class TestConfigNotCode:
    """No `deps` fixture here — the REAL accessors run (unpatched)."""

    def test_missing_metadata_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("METADATA_TABLE", raising=False)
        import search_consumer.handler as h
        with pytest.raises(RuntimeError):
            h._metadata_table()

    def test_missing_index_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("SEARCH_INDEX_TABLE", raising=False)
        import search_consumer.handler as h
        with pytest.raises(RuntimeError):
            h._index_table()
