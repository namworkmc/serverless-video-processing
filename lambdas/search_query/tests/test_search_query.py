"""ATDD suite for Story 4.2: search-query Lambda (GET /videos/search?title=
through the gateway — FR-18, FR-21, NFR-3, NFR-7).

Assertions encode the spec's I/O & Edge-Case Matrix and the test
architect's checklist (T1-T8):

| Scenario               | Expected                                          |
|------------------------|---------------------------------------------------|
| Happy match            | 200 {"title": <stripped>, "results": [...]}        |
| Substring semantics    | contains() matches; non-containing titles excluded |
| No match               | 200 with "results": [] — NOT an error              |
| Multiple matches       | all returned, videoId ascending                    |
| Missing/empty title    | 400 via MalformedInputError; no scan (NFR-3)       |
| Transient scan error   | 500 via map_error                                  |

Plus: entry projection (exactly {videoId, title, processedKey,
indexedAt}; no internal fields in the body), the purity guarantee
(only a `dynamodb` handle is ever constructed), and config-not-code
(NFR-4).

TDD Phase: RED
Story: 4-2-title-search-through-the-gateway
"""

import json
import logging
import re

import pytest

from search_query.handler import handler

# ---------------------------------------------------------------------------
# Fakes (same pattern as lambdas/history_query/tests)
# ---------------------------------------------------------------------------


class TransientDynamoError(Exception):
    """Stand-in for any non-conditional DynamoDB failure (network,
    throttle, 5xx) — must surface as a mapped 500, never a crash."""


class FakeIndexTable:
    """In-memory search-index stand-in honoring the filtered Scan:
    evaluates a `contains(attr, :placeholder)` FilterExpression against
    ExpressionAttributeValues the way DynamoDB would — SUBSTRING
    containment, so a handler that swaps in an equality filter fails the
    semantics tests instead of silently passing. Any other expression
    shape is a loud test error."""

    _CONTAINS = re.compile(r"contains\(\s*(\w+)\s*,\s*(:\w+)\s*\)")

    def __init__(self, items=()):
        self.items = list(items)
        self.scan_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        expr = kwargs.get("FilterExpression")
        values = kwargs.get("ExpressionAttributeValues") or {}
        match = self._CONTAINS.fullmatch(expr) if isinstance(expr, str) else None
        if match is None:
            raise AssertionError(
                f"unsupported FilterExpression {expr!r} — this fake "
                "evaluates contains(attr, :ph) only")
        attr, placeholder = match.group(1), match.group(2)
        if placeholder not in values:
            raise AssertionError(f"unbound placeholder: {placeholder}")
        needle = values[placeholder]
        matched = [item for item in self.items
                   if isinstance(item.get(attr), str) and needle in item[attr]]
        return {"Items": [dict(item) for item in matched]}


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
THIRD_VIDEO_ID = "44444444-3333-2222-1111-555555555555"
OTHER_VIDEO_ID = "99999999-8888-7777-6666-555555555555"
INDEX_TABLE = "search-index"

INDEXED_AT = "2026-08-24T00:00:00Z"


def _indexed(video_id, title, indexed_at=INDEXED_AT, **extra):
    """A search-index item exactly as Story 4.1's consumer writes it:
    {videoId, title, processedKey, indexedAt}."""
    return {
        "videoId": video_id,
        "title": title,
        "processedKey": f"processed/{video_id}/fixture.mp4",
        "indexedAt": indexed_at,
        **extra,
    }


def _gw_event(query_params=None):
    """API Gateway v2 payload format 2.0 event for GET /videos/search.
    Route keys carry no query string — `title` arrives via
    queryStringParameters."""
    event = {
        "version": "2.0",
        "routeKey": "GET /videos/search",
        "rawPath": "/videos/search",
        "isBase64Encoded": False,
    }
    if query_params is not None:
        event["queryStringParameters"] = query_params
    return event


def _body(result):
    return json.loads(result["body"])


@pytest.fixture
def tables():
    return {INDEX_TABLE: FakeIndexTable()}


@pytest.fixture
def deps(tables, monkeypatch):
    """Wire the fake table + env vars into the handler (NFR-4)."""
    monkeypatch.setenv("SEARCH_INDEX_TABLE", INDEX_TABLE)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    import search_query.handler as h
    monkeypatch.setattr(h, "_index_table", lambda: tables[INDEX_TABLE])
    return tables


# ---------------------------------------------------------------------------
# T1 — Happy match (matrix row 1, FR-18)
# ---------------------------------------------------------------------------

class TestHappyMatch:
    def test_200_with_stripped_title_and_results(self, deps):
        deps[INDEX_TABLE].items.append(_indexed(VIDEO_ID, "My Video"))
        result = handler(_gw_event({"title": "  My Vid  "}), None)
        assert result["statusCode"] == 200
        body = _body(result)
        assert body["title"] == "My Vid"
        assert len(body["results"]) == 1

    def test_body_is_exactly_title_and_results(self, deps):
        """Never: no index-internal fields leak into the response top level."""
        deps[INDEX_TABLE].items.append(_indexed(VIDEO_ID, "My Video"))
        body = _body(handler(_gw_event({"title": "My Vid"}), None))
        assert set(body) == {"title", "results"}

    def test_entry_shape_exactly_the_4_1_entry(self, deps):
        deps[INDEX_TABLE].items.extend([
            _indexed(VIDEO_ID, "My Video", shim="internal-only"),
        ])
        body = _body(handler(_gw_event({"title": "My Vid"}), None))
        entry = body["results"][0]
        assert set(entry) == {"videoId", "title", "processedKey", "indexedAt"}
        assert entry["videoId"] == VIDEO_ID
        assert entry["title"] == "My Video"
        assert entry["processedKey"] == f"processed/{VIDEO_ID}/fixture.mp4"
        assert entry["indexedAt"] == INDEXED_AT

    def test_content_type_json(self, deps):
        result = handler(_gw_event({"title": "My Vid"}), None)
        assert result["headers"]["Content-Type"] == "application/json"

    def test_log_line_carries_result_count(self, deps, caplog):
        deps[INDEX_TABLE].items.append(_indexed(VIDEO_ID, "My Video"))
        with caplog.at_level(logging.INFO):
            handler(_gw_event({"title": "My Vid"}), None)
        assert any("results=1" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# T2 — Substring semantics (matrix row 2, AD-3/NFR-7 scan design).
# The spec's literal example query ("anch") matches neither "Anchor A"
# nor "Enchanted" case-sensitively; "nch" preserves the row's observable
# semantics (both containing titles match, Other excluded) under the
# frozen case-sensitive constraint.
# ---------------------------------------------------------------------------

class TestSubstringSemantics:
    def test_contains_matches_both_containing_titles_excludes_other(
            self, deps):
        deps[INDEX_TABLE].items.extend([
            _indexed(VIDEO_ID, "Anchor A"),
            _indexed(OTHER_VIDEO_ID, "Enchanted"),
            _indexed(THIRD_VIDEO_ID, "Other"),
        ])
        body = _body(handler(_gw_event({"title": "nch"}), None))
        matched_ids = {r["videoId"] for r in body["results"]}
        assert matched_ids == {VIDEO_ID, OTHER_VIDEO_ID}

    def test_matching_is_case_sensitive(self, deps):
        deps[INDEX_TABLE].items.append(_indexed(VIDEO_ID, "Anchor A"))
        body = _body(handler(_gw_event({"title": "anchor"}), None))
        assert body["results"] == []

    def test_filter_bound_to_the_stripped_query(self, deps):
        handler(_gw_event({"title": "  nch  "}), None)
        call = deps[INDEX_TABLE].scan_calls[0]
        values = call.get("ExpressionAttributeValues") or {}
        assert list(values.values()) == ["nch"]
        assert call["FilterExpression"].startswith("contains(title,")


# ---------------------------------------------------------------------------
# T3 — No match is success (matrix row 3)
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_unknown_substring_200_with_empty_results_not_error(self, deps):
        deps[INDEX_TABLE].items.append(_indexed(VIDEO_ID, "Other"))
        result = handler(_gw_event({"title": "zzz-not-there"}), None)
        assert result["statusCode"] == 200
        body = _body(result)
        assert body["title"] == "zzz-not-there"
        assert body["results"] == []

    def test_empty_index_is_also_200_empty(self, deps):
        result = handler(_gw_event({"title": "anything"}), None)
        assert result["statusCode"] == 200
        assert _body(result)["results"] == []


# ---------------------------------------------------------------------------
# T4 — Multiple matches: deterministic order (matrix row 4)
# ---------------------------------------------------------------------------

class TestMultipleMatches:
    def test_all_returned_sorted_by_video_id_ascending(self, deps):
        # Seeded deliberately out of order.
        deps[INDEX_TABLE].items.extend([
            _indexed(OTHER_VIDEO_ID, "Anchor A"),
            _indexed(VIDEO_ID, "Anchored B"),
            _indexed(THIRD_VIDEO_ID, "Lunch Clip"),
        ])
        body = _body(handler(_gw_event({"title": "nch"}), None))
        assert [r["videoId"] for r in body["results"]] == [
            VIDEO_ID, THIRD_VIDEO_ID, OTHER_VIDEO_ID]


# ---------------------------------------------------------------------------
# T5 — Missing/empty/non-string title (matrix row 5, NFR-3)
# ---------------------------------------------------------------------------

class TestMissingOrEmptyTitle:
    @pytest.mark.parametrize("event", [
        None,
        "not-a-dict",
        {},
        {"queryStringParameters": None},
        {"queryStringParameters": {}},
        {"queryStringParameters": {"other": "x"}},
        {"queryStringParameters": {"title": ""}},
        {"queryStringParameters": {"title": "   "}},
        {"queryStringParameters": {"title": 123}},
    ])
    def test_400_via_malformed_input(self, deps, event):
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert _body(result).get("error")

    def test_no_scan_performed(self, deps):
        handler({"queryStringParameters": {}}, None)
        assert deps[INDEX_TABLE].scan_calls == []

    def test_400_body_is_exactly_error_with_json_content_type(self, deps):
        """Representative bad input pinned to the full error contract:
        body keys exactly {"error"}, Content-Type json (FR-21 passthrough
        shape the gateway forwards unchanged)."""
        result = handler({"queryStringParameters": {"title": ""}}, None)
        assert result["statusCode"] == 400
        assert set(_body(result)) == {"error"}
        assert _body(result)["error"]
        assert result["headers"]["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# T6 — Transient scan errors map to 500 (matrix row 6, NFR-3)
# ---------------------------------------------------------------------------

class TestTransientErrors:
    def test_scan_transient_error_maps_to_500(self, deps):
        def flaky(**kwargs):
            raise TransientDynamoError("network")

        deps[INDEX_TABLE].scan = flaky
        result = handler(_gw_event({"title": "nch"}), None)
        assert result["statusCode"] == 500
        assert set(_body(result)) == {"error"}
        assert _body(result)["error"]
        assert result["headers"]["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# T7 — Purity probe: only a dynamodb handle is ever constructed
# ---------------------------------------------------------------------------

class TestPurity:
    def test_only_dynamodb_constructed(self, tables, monkeypatch):
        """Route shared.clients through a recorder that fails on any
        s3/events/states/sqs construction. Deliberately does NOT use the
        `deps` fixture (which patches the table accessor): only
        `h.clients` is patched, so the REAL accessor runs and its client
        choices are what gets recorded."""
        monkeypatch.setenv("SEARCH_INDEX_TABLE", INDEX_TABLE)
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        recorder = ClientFactoryRecorder(tables)
        import search_query.handler as h
        monkeypatch.setattr(h, "clients", recorder)

        result = handler(_gw_event({"title": "nch"}), None)

        assert result["statusCode"] == 200
        assert recorder.requested
        assert set(recorder.requested) == {"dynamodb"}


# ---------------------------------------------------------------------------
# T8 — Config-not-code (NFR-4)
# ---------------------------------------------------------------------------

class TestConfigNotCode:
    """No `deps` fixture here — the REAL accessor runs (unpatched)."""

    def test_missing_index_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("SEARCH_INDEX_TABLE", raising=False)
        import search_query.handler as h
        with pytest.raises(RuntimeError):
            h._index_table()

    def test_missing_endpoint_url_maps_to_500(self, monkeypatch):
        """SEARCH_INDEX_TABLE set but AWS_ENDPOINT_URL gone: the REAL
        accessors run (no deps fixture) and shared.clients._endpoint_url()
        raises RuntimeError inside the handler's try block -> map_error ->
        500 {"error"} (config gap must surface as a mapped client-facing
        error, never a crash)."""
        monkeypatch.setenv("SEARCH_INDEX_TABLE", INDEX_TABLE)
        monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
        import shared.clients as clients_mod
        # Belt-and-braces: a warm resource cache would short-circuit the
        # endpoint check; force the real construction path.
        monkeypatch.setattr(clients_mod, "_resource_cache", {})
        result = handler(_gw_event({"title": "x"}), None)
        assert result["statusCode"] == 500
        assert set(_body(result)) == {"error"}
        assert _body(result)["error"]


# ---------------------------------------------------------------------------
# T9 — Scan-truncation observability (epic-4 retro AI-17/F2): parity with
# search_rebuild/handler.py:87-91 — warn loudly past one Scan page while
# keeping NFR-7 single-scan semantics and the 200-partial-results contract.
# ---------------------------------------------------------------------------

class TestScanTruncationObservability:
    def test_truncated_scan_warns_loudly_still_single_scan(
            self, deps, caplog):
        """If DynamoDB reports LastEvaluatedKey (>1 Scan page) the handler
        must WARN that results may be partial — but NOT paginate (NFR-7
        lab scale pins single-scan semantics). The fake's real `scan` is
        WRAPPED, not replaced, so expression-shape evaluation still runs."""
        deps[INDEX_TABLE].items.extend([
            _indexed(VIDEO_ID, "Anchor A"),
            _indexed(OTHER_VIDEO_ID, "Enchanted"),
        ])
        original_scan = deps[INDEX_TABLE].scan

        def truncating(**kwargs):
            result = original_scan(**kwargs)
            result["LastEvaluatedKey"] = {"videoId": OTHER_VIDEO_ID}
            return result

        deps[INDEX_TABLE].scan = truncating
        with caplog.at_level(logging.WARNING):
            result = handler(_gw_event({"title": "nch"}), None)
        assert result["statusCode"] == 200
        body = _body(result)
        assert [r["videoId"] for r in body["results"]] == [
            VIDEO_ID, OTHER_VIDEO_ID]
        # Records must come from THIS handler module's own logger at
        # WARNING level, with the lazy-%d count rendered correctly
        # (both seeded items match -> "after 2 items").
        warn_records = [r for r in caplog.records
                        if r.name == "search_query.handler"
                        and r.levelno == logging.WARNING]
        assert any(
            "truncated" in r.getMessage()
            and "LastEvaluatedKey" in r.getMessage()
            and "after 2 items" in r.getMessage()
            for r in warn_records)
        assert len(deps[INDEX_TABLE].scan_calls) == 1

    def test_single_page_scan_logs_no_truncation_warning(
            self, deps, caplog):
        """No-warn control: an always-warn implementation fails here."""
        deps[INDEX_TABLE].items.append(_indexed(VIDEO_ID, "Anchor A"))
        with caplog.at_level(logging.WARNING):
            result = handler(_gw_event({"title": "nch"}), None)
        assert result["statusCode"] == 200
        assert not any("truncated" in r.getMessage()
                       for r in caplog.records)
