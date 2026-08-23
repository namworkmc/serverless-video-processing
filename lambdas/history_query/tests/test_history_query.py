"""ATDD suite for Story 3.2: history-query Lambda (GET /videos/{videoId}/history
through the gateway — FR-16, FR-13, FR-21, NFR-3).

Assertions encode the spec's I/O & Edge-Case Matrix and the test
architect's checklist (T1-T10):

| Scenario                    | Expected                                        |
|-----------------------------|-------------------------------------------------|
| Happy path                  | 200 {"videoId", "entries"} sorted timestamp asc |
| Known videoId, no entries   | 200 with "entries": [] — NOT 404 (async leg)    |
| Unknown videoId             | 404 {"error"} via the metadata gate; no scan    |
| Missing/empty videoId       | 400 via MalformedInputError                     |
| Transient DynamoDB error    | 500 via map_error                               |

Plus: filter binding (only this video's entries), entry projection
(exactly {status, eventId, timestamp}; no metadata fields in the body),
the purity guarantee (only a `dynamodb` resource is ever constructed),
and config-not-code (NFR-4).

TDD Phase: RED
Story: 3-2-history-query-through-the-gateway
"""

import json
import logging

import pytest

from shared import events
from history_query.handler import handler

# ---------------------------------------------------------------------------
# Fakes (same pattern as lambdas/history_consumer/tests)
# ---------------------------------------------------------------------------


class TransientDynamoError(Exception):
    """Stand-in for any non-conditional DynamoDB failure (network,
    throttle, 5xx) — must surface as a mapped 500, never a crash."""


class FakeMetadataTable:
    """In-memory video-metadata stand-in for the 404 gate."""

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
    """In-memory status-history stand-in honoring the filtered Scan:
    evaluates a simple `attr = :placeholder` FilterExpression against
    ExpressionAttributeValues the way DynamoDB would. A scan with NO
    filter returns every item — so a handler that forgets the filter
    fails the filtering tests instead of silently passing."""

    def __init__(self, items=()):
        self.items = list(items)
        self.scan_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        expr = kwargs.get("FilterExpression")
        values = kwargs.get("ExpressionAttributeValues") or {}
        matched = self.items
        if isinstance(expr, str) and "=" in expr:
            attr, _, placeholder = (p.strip() for p in expr.partition("="))
            if placeholder in values:
                matched = [i for i in self.items
                           if i.get(attr) == values[placeholder]]
        return {"Items": [dict(i) for i in matched]}


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
# Fixtures
# ---------------------------------------------------------------------------

VIDEO_ID = "11111111-2222-3333-4444-555555555555"
OTHER_VIDEO_ID = "99999999-8888-7777-6666-555555555555"
METADATA_TABLE = "video-metadata"
HISTORY_TABLE = "status-history"

TS_UPLOADED = "2026-08-23T01:00:00Z"
TS_PROCESSED = "2026-08-23T01:05:00Z"


def _entry(video_id, status, timestamp):
    """A status-history item exactly as Story 3.1's consumer writes it:
    {eventId, videoId, status, timestamp} with the deterministic eventId."""
    return {
        "eventId": events.event_id(video_id, status),
        "videoId": video_id,
        "status": status,
        "timestamp": timestamp,
    }


def _gw_event(video_id):
    """API Gateway v2 payload format 2.0 event for
    GET /videos/{videoId}/history."""
    return {
        "version": "2.0",
        "routeKey": "GET /videos/{videoId}/history",
        "rawPath": f"/videos/{video_id}/history",
        "pathParameters": {"videoId": video_id},
        "isBase64Encoded": False,
    }


def _body(result):
    return json.loads(result["body"])


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
    import history_query.handler as h
    monkeypatch.setattr(
        h, "_metadata_table", lambda: tables[METADATA_TABLE])
    monkeypatch.setattr(h, "_history_table", lambda: tables[HISTORY_TABLE])
    return tables


# ---------------------------------------------------------------------------
# T1 — Happy path (matrix row 1, FR-16)
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_200_with_video_id_and_entries(self, deps):
        deps[HISTORY_TABLE].items.extend([
            _entry(VIDEO_ID, "UPLOADED", TS_UPLOADED),
            _entry(VIDEO_ID, "PROCESSED", TS_PROCESSED),
        ])
        result = handler(_gw_event(VIDEO_ID), None)
        assert result["statusCode"] == 200
        body = _body(result)
        assert body["videoId"] == VIDEO_ID
        assert len(body["entries"]) == 2

    def test_body_is_exactly_video_id_and_entries(self, deps):
        """Never: no video-metadata fields leak into the response."""
        deps[HISTORY_TABLE].items.append(
            _entry(VIDEO_ID, "PROCESSED", TS_PROCESSED))
        body = _body(handler(_gw_event(VIDEO_ID), None))
        assert set(body) == {"videoId", "entries"}

    def test_content_type_json(self, deps):
        result = handler(_gw_event(VIDEO_ID), None)
        assert result["headers"]["Content-Type"] == "application/json"

    def test_log_line_carries_video_id_and_count(self, deps, caplog):
        deps[HISTORY_TABLE].items.extend([
            _entry(VIDEO_ID, "UPLOADED", TS_UPLOADED),
            _entry(VIDEO_ID, "PROCESSED", TS_PROCESSED),
        ])
        with caplog.at_level(logging.INFO):
            handler(_gw_event(VIDEO_ID), None)
        assert any(VIDEO_ID in r.message and "2" in r.message
                   for r in caplog.records)


# ---------------------------------------------------------------------------
# T2 — Known videoId, no entries yet (matrix row 2) — the async-leg
# distinction the Bruno poll-with-timeout design depends on.
# ---------------------------------------------------------------------------

class TestKnownVideoNoEntries:
    def test_200_with_empty_entries_not_404(self, deps):
        result = handler(_gw_event(VIDEO_ID), None)
        assert result["statusCode"] == 200
        assert _body(result)["entries"] == []


# ---------------------------------------------------------------------------
# T3 — Unknown videoId: the 404 gate (matrix row 3, FR-13, NFR-3)
# ---------------------------------------------------------------------------

class TestUnknownVideoId:
    def test_404_with_error_body(self, deps):
        deps[METADATA_TABLE].known.clear()
        result = handler(_gw_event(VIDEO_ID), None)
        assert result["statusCode"] == 404
        body = _body(result)
        assert body.get("error")
        assert VIDEO_ID in body["error"]

    def test_gate_runs_before_the_scan(self, deps):
        deps[METADATA_TABLE].known.clear()
        handler(_gw_event(VIDEO_ID), None)
        assert deps[METADATA_TABLE].get_calls == [{"videoId": VIDEO_ID}]
        assert deps[HISTORY_TABLE].scan_calls == []


# ---------------------------------------------------------------------------
# T4 — Missing/empty videoId (matrix row 4)
# ---------------------------------------------------------------------------

class TestMissingOrEmptyVideoId:
    @pytest.mark.parametrize("event", [
        None,
        "not-a-dict",
        {},
        {"pathParameters": None},
        {"pathParameters": {}},
        {"pathParameters": {"videoId": ""}},
        {"pathParameters": {"videoId": "   "}},
        {"pathParameters": {"videoId": 123}},
    ])
    def test_400_via_malformed_input(self, deps, event):
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert _body(result).get("error")

    def test_no_scan_performed(self, deps):
        handler({"pathParameters": {}}, None)
        assert deps[HISTORY_TABLE].scan_calls == []


# ---------------------------------------------------------------------------
# T5 — Transient DynamoDB errors map to 500 (matrix row 5, NFR-3)
# ---------------------------------------------------------------------------

class TestTransientErrors:
    def test_metadata_transient_error_maps_to_500(self, deps):
        deps[METADATA_TABLE].error = TransientDynamoError("throttled")
        result = handler(_gw_event(VIDEO_ID), None)
        assert result["statusCode"] == 500
        assert _body(result).get("error")

    def test_scan_transient_error_maps_to_500(self, deps):
        def flaky(**kwargs):
            raise TransientDynamoError("network")

        deps[HISTORY_TABLE].scan = flaky
        result = handler(_gw_event(VIDEO_ID), None)
        assert result["statusCode"] == 500
        assert _body(result).get("error")


# ---------------------------------------------------------------------------
# T6 — Filtered scan: only this video's entries (AD-3 scan design)
# ---------------------------------------------------------------------------

class TestFiltering:
    def test_other_videos_entries_excluded(self, deps):
        deps[METADATA_TABLE].known.add(OTHER_VIDEO_ID)
        deps[HISTORY_TABLE].items.extend([
            _entry(OTHER_VIDEO_ID, "PROCESSED", TS_UPLOADED),
            _entry(VIDEO_ID, "PROCESSED", TS_PROCESSED),
        ])
        body = _body(handler(_gw_event(VIDEO_ID), None))
        assert [e["eventId"] for e in body["entries"]] == [
            events.event_id(VIDEO_ID, "PROCESSED")]

    def test_scan_bound_to_the_requested_video_id(self, deps):
        handler(_gw_event(VIDEO_ID), None)
        call = deps[HISTORY_TABLE].scan_calls[0]
        assert VIDEO_ID in (call.get("ExpressionAttributeValues") or {}).values()


# ---------------------------------------------------------------------------
# T7 — Sort order: timestamp ascending (ISO-8601 sorts lexicographically)
# ---------------------------------------------------------------------------

class TestSortOrder:
    def test_entries_sorted_by_timestamp_ascending(self, deps):
        # Seeded deliberately out of order.
        deps[HISTORY_TABLE].items.extend([
            _entry(VIDEO_ID, "PROCESSED", TS_PROCESSED),
            _entry(VIDEO_ID, "UPLOADED", TS_UPLOADED),
        ])
        body = _body(handler(_gw_event(VIDEO_ID), None))
        assert [e["timestamp"] for e in body["entries"]] == [
            TS_UPLOADED, TS_PROCESSED]


# ---------------------------------------------------------------------------
# T8 — Entry projection: exactly {status, eventId, timestamp} (FR-16)
# ---------------------------------------------------------------------------

class TestEntryProjection:
    def test_entry_shape_exactly(self, deps):
        deps[HISTORY_TABLE].items.append(
            _entry(VIDEO_ID, "PROCESSED", TS_PROCESSED))
        body = _body(handler(_gw_event(VIDEO_ID), None))
        entry = body["entries"][0]
        assert set(entry) == {"status", "eventId", "timestamp"}
        assert entry["status"] == "PROCESSED"
        assert entry["eventId"] == events.event_id(VIDEO_ID, "PROCESSED")
        assert entry["timestamp"] == TS_PROCESSED


# ---------------------------------------------------------------------------
# T9 — Purity probe: only a dynamodb resource is ever constructed
# ---------------------------------------------------------------------------

class TestPurity:
    def test_only_dynamodb_constructed(self, tables, monkeypatch):
        """Route shared.clients through a recorder that fails on any
        s3/events/states/sqs construction. Deliberately does NOT use the
        `deps` fixture (which patches the table accessors): only
        `h.clients` is patched, so the REAL accessors run and their
        client choices are what gets recorded."""
        monkeypatch.setenv("METADATA_TABLE", METADATA_TABLE)
        monkeypatch.setenv("HISTORY_TABLE", HISTORY_TABLE)
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        recorder = ClientFactoryRecorder(tables)
        import history_query.handler as h
        monkeypatch.setattr(h, "clients", recorder)

        result = handler(_gw_event(VIDEO_ID), None)

        assert result["statusCode"] == 200
        assert recorder.requested
        assert set(recorder.requested) == {"dynamodb"}


# ---------------------------------------------------------------------------
# T10 — Config-not-code (NFR-4)
# ---------------------------------------------------------------------------

class TestConfigNotCode:
    """No `deps` fixture here — the REAL accessors run (unpatched)."""

    def test_missing_metadata_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("METADATA_TABLE", raising=False)
        import history_query.handler as h
        with pytest.raises(RuntimeError):
            h._metadata_table()

    def test_missing_history_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("HISTORY_TABLE", raising=False)
        import history_query.handler as h
        with pytest.raises(RuntimeError):
            h._history_table()
