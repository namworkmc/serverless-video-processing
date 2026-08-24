"""ATDD suite for Story 4.3: search-rebuild Lambda (admin-only index
rebuild — FR-19, AD-3, NFR-1/NFR-4).

Assertions encode the spec's I/O & Edge-Case Matrix and the test
architect's checklist (T1-T8):

| Scenario              | Expected                                            |
|-----------------------|-----------------------------------------------------|
| Happy rebuild         | one upsert per PROCESSED record, exact 4.1 entry     |
| Selection semantics   | only PROCESSED — selection happens IN the Scan       |
| Empty source          | zero writes, summary {scanned:0, indexed:0, skipped:0}|
| Idempotent re-invoke  | same PK overwritten, no duplicates, no deletes       |
| Unusable record       | counted skipped + logged; batch NOT aborted          |
| Transient failure     | raw exception propagates — NO HTTP error mapping     |

Plus: purity (only dynamodb table handles ever constructed) and
config-not-code accessors (NFR-4). The event payload is ignored BY
CONTRACT — this function is direct-invoke admin tooling.

TDD Phase: GREEN
Story: 4-3-admin-only-index-rebuild
"""

import logging
import re

import pytest

from search_rebuild.handler import handler
from shared import status

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class TransientDynamoError(Exception):
    """Stand-in for any non-skip DynamoDB failure (network, throttle,
    5xx) — must propagate RAW out of the invocation (FunctionError for a
    direct invoke), never be swallowed or mapped."""


class FakeMetadataTable:
    """In-memory video-metadata stand-in that EVALUATES the sanctioned
    filtered-Scan shape: FilterExpression exactly `#s = :st`,
    ExpressionAttributeNames exactly {"#s": "status"}, and :st bound to
    shared.status.PROCESSED. Selection must happen IN THE QUERY (mirrors
    the sibling query lambdas' pattern) — an unfiltered scan plus an
    in-code post-filter fails loudly here. Any other expression shape is
    a loud test error."""

    _EQ = re.compile(r"#s\s*=\s*:st")
    _MISSING = object()

    def __init__(self, items=()):
        self.items = list(items)
        self.scan_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        expr = kwargs.get("FilterExpression")
        names = kwargs.get("ExpressionAttributeNames") or {}
        values = kwargs.get("ExpressionAttributeValues") or {}
        if not isinstance(expr, str) or not self._EQ.fullmatch(expr):
            raise AssertionError(
                f"unsupported FilterExpression {expr!r} — this fake "
                "evaluates the pinned `#s = :st` shape only (selection "
                "must happen IN the query)")
        if names != {"#s": "status"}:
            raise AssertionError(
                f"ExpressionAttributeNames must map #s -> status, "
                f"got {names!r}")
        bound = values.get(":st", self._MISSING)
        if bound is self._MISSING:
            raise AssertionError("unbound placeholder: :st")
        if bound != status.PROCESSED:
            raise AssertionError(
                f"filter must bind :st to shared.status.PROCESSED, "
                f"got {bound!r}")
        matched = [item for item in self.items
                   if item.get("status") == status.PROCESSED]
        return {"Items": [dict(item) for item in matched]}


class FakeIndexTable:
    """Records every put_item; delete_item is a LOUD failure — the
    rebuild repopulates, it never sweeps (Ask First: stale-entry
    sweeping is out of scope). Has no scan method at all: scanning the
    INDEX instead of METADATA_TABLE would AttributeError."""

    def __init__(self):
        self.items = {}      # videoId -> last item written
        self.put_calls = []  # every Item ever put, in order
        self.delete_calls = []

    def put_item(self, **kwargs):
        item = kwargs["Item"]
        self.put_calls.append(dict(item))
        self.items[item["videoId"]] = dict(item)

    def delete_item(self, **kwargs):
        self.delete_calls.append(kwargs)
        raise AssertionError(
            "purity violation: rebuild issued a DELETE against "
            "search-index")


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
# Fixtures + item builders
# ---------------------------------------------------------------------------

METADATA_TABLE = "video-metadata"
INDEX_TABLE = "search-index"

VIDEO_ID = "11111111-2222-3333-4444-555555555555"
OTHER_VIDEO_ID = "99999999-8888-7777-6666-555555555555"


def _meta(video_id=VIDEO_ID, title="My Video", st=status.UPLOADED,
          processed_key=None, **extra):
    """A video-metadata item shaped as the upload/processing legs write
    it. PROCESSED records carry processedKey (set by the state machine's
    transition extras); pre-terminal ones do not."""
    item = {
        "videoId": video_id,
        "title": title,
        "status": st,
        "bucket": "video-uploads",
        "originalKey": f"{video_id}/fixture.mp4",
        "createdAt": "2026-08-24T00:00:00Z",
        "updatedAt": "2026-08-24T00:00:00Z",
        **extra,
    }
    if processed_key is not None:
        item["processedKey"] = processed_key
    return item


def _processed(video_id, title="My Video"):
    return _meta(
        video_id, title=title, st=status.PROCESSED,
        processed_key=f"processed/{video_id}/fixture.mp4")


@pytest.fixture
def tables():
    return {METADATA_TABLE: FakeMetadataTable(),
            INDEX_TABLE: FakeIndexTable()}


@pytest.fixture
def deps(tables, monkeypatch):
    """Wire the fake tables + env vars into the handler (NFR-4)."""
    monkeypatch.setenv("METADATA_TABLE", METADATA_TABLE)
    monkeypatch.setenv("SEARCH_INDEX_TABLE", INDEX_TABLE)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    import search_rebuild.handler as h
    monkeypatch.setattr(h, "_metadata_table",
                        lambda: tables[METADATA_TABLE])
    monkeypatch.setattr(h, "_index_table", lambda: tables[INDEX_TABLE])
    return tables


# ---------------------------------------------------------------------------
# T1 — Happy rebuild (matrix rows 1+2 combined seeding, FR-19)
# ---------------------------------------------------------------------------

class TestHappyRebuild:
    def test_only_processed_records_indexed_from_mixed_statuses(self, deps):
        deps[METADATA_TABLE].items.extend([
            _processed(VIDEO_ID),
            _meta("22222222-2222-3333-4444-555555555555",
                  st=status.UPLOADED),
            _meta("33333333-3333-3333-4444-555555555555",
                  st=status.PROCESSING),
            _meta("44444444-4444-3333-4444-555555555555",
                  st=status.FAILED),
        ])
        handler({}, None)
        assert set(deps[INDEX_TABLE].items) == {VIDEO_ID}

    def test_entry_shape_exactly_the_4_1_entry_with_stripped_title(
            self, deps):
        deps[METADATA_TABLE].items.append(
            _processed(VIDEO_ID, title="  Padded Title  "))
        handler({}, None)
        entry = deps[INDEX_TABLE].items[VIDEO_ID]
        assert set(entry) == {"videoId", "title", "processedKey",
                              "indexedAt"}
        assert entry["videoId"] == VIDEO_ID
        assert entry["title"] == "Padded Title"
        assert entry["processedKey"] == f"processed/{VIDEO_ID}/fixture.mp4"
        assert isinstance(entry["indexedAt"], str) and entry["indexedAt"]

    def test_summary_keys_exactly_scanned_indexed_skipped(self, deps):
        deps[METADATA_TABLE].items.extend([
            _processed(VIDEO_ID),
            _processed(OTHER_VIDEO_ID),
            _meta(st=status.FAILED),
        ])
        summary = handler({}, None)
        assert set(summary) == {"scanned", "indexed", "skipped"}
        assert summary == {"scanned": 2, "indexed": 2, "skipped": 0}

    def test_event_payload_ignored_by_contract(self, deps):
        deps[METADATA_TABLE].items.append(_processed(VIDEO_ID))
        for event in ({}, None, {"Records": [{"body": "garbage"}]},
                      "not-even-a-dict"):
            summary = handler(event, None)
            assert summary["indexed"] == 1

    def test_log_line_carries_counts(self, deps, caplog):
        deps[METADATA_TABLE].items.append(_processed(VIDEO_ID))
        with caplog.at_level(logging.INFO):
            handler({}, None)
        assert any(
            "scanned=1" in r.getMessage() and "indexed=1" in r.getMessage()
            and "skipped=0" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# T2 — Filter binding: selection IN the query (spec Design Notes)
# ---------------------------------------------------------------------------

class TestFilterBinding:
    def test_scan_expression_is_the_pinned_shape(self, deps):
        deps[METADATA_TABLE].items.append(_processed(VIDEO_ID))
        handler({}, None)
        assert len(deps[METADATA_TABLE].scan_calls) == 1
        call = deps[METADATA_TABLE].scan_calls[0]
        assert re.fullmatch(r"#s\s*=\s*:st",
                            call["FilterExpression"])
        assert call["ExpressionAttributeNames"] == {"#s": "status"}
        assert call["ExpressionAttributeValues"] == {
            ":st": status.PROCESSED}

    def test_filter_value_bound_to_shared_constant_not_literal(
            self, deps, monkeypatch):
        """The bound value must equal shared.status.PROCESSED — the fake
        rejects anything else, so a drifted string literal ("PROCESSED"
        typo'd, or a different constant like FAILED) fails here."""
        deps[METADATA_TABLE].items.append(_processed(VIDEO_ID))
        handler({}, None)
        bound = deps[METADATA_TABLE].scan_calls[0][
            "ExpressionAttributeValues"][":st"]
        assert bound == status.PROCESSED

    def test_single_scan_no_pagination(self, deps):
        deps[METADATA_TABLE].items.extend(
            [_processed(f"{i:08x}-0000-0000-0000-000000000000")
             for i in range(5)])
        handler({}, None)
        assert len(deps[METADATA_TABLE].scan_calls) == 1

    def test_truncated_scan_warns_loudly_still_single_scan(
            self, deps, caplog):
        """P2 observability: if DynamoDB reports LastEvaluatedKey the
        handler must WARN that the rebuild may be partial — but NOT
        paginate (NFR-7 lab scale pins single-scan semantics)."""
        deps[METADATA_TABLE].items.append(_processed(VIDEO_ID))
        original_scan = deps[METADATA_TABLE].scan

        def truncating(**kwargs):
            result = original_scan(**kwargs)
            result["LastEvaluatedKey"] = {"videoId": VIDEO_ID}
            return result

        deps[METADATA_TABLE].scan = truncating
        with caplog.at_level(logging.WARNING):
            summary = handler({}, None)
        assert summary == {"scanned": 1, "indexed": 1, "skipped": 0}
        assert any("truncated" in r.getMessage() for r in caplog.records)
        assert len(deps[METADATA_TABLE].scan_calls) == 1


# ---------------------------------------------------------------------------
# T3 — Empty source (matrix row 3)
# ---------------------------------------------------------------------------

class TestEmptySource:
    def test_empty_table_zero_writes_zero_summary(self, deps):
        summary = handler({}, None)
        assert summary == {"scanned": 0, "indexed": 0, "skipped": 0}
        assert deps[INDEX_TABLE].put_calls == []

    def test_all_non_terminal_records_zero_writes(self, deps):
        deps[METADATA_TABLE].items.extend([
            _meta(st=status.UPLOADED),
            _meta(st=status.PROCESSING),
            _meta(st=status.FAILED),
        ])
        summary = handler({}, None)
        assert summary == {"scanned": 0, "indexed": 0, "skipped": 0}
        assert deps[INDEX_TABLE].put_calls == []


# ---------------------------------------------------------------------------
# T4 — Unusable PROCESSED records: skip, never abort (matrix row 5)
# ---------------------------------------------------------------------------

class TestUnusableRecord:
    @pytest.mark.parametrize("bad_field,bad_value", [
        ("videoId", None),        # missing
        ("videoId", ""),          # empty string
        ("videoId", "   "),       # whitespace-only
        ("videoId", 123),         # non-string
        ("title", None),
        ("title", ""),
        ("title", "   "),
        ("title", 123),           # non-string
        ("processedKey", None),   # missing
        ("processedKey", ""),
        ("processedKey", "   "),
        ("processedKey", 4.5),    # non-string
    ])
    def test_bad_record_skipped_good_record_still_indexed(
            self, deps, caplog, bad_field, bad_value):
        broken = _meta("aaaa0000-0000-0000-0000-000000000000",
                       st=status.PROCESSED,
                       processed_key="processed/x/fixture.mp4")
        del broken[bad_field]
        if bad_value is not None:
            broken[bad_field] = bad_value
        good = _processed(VIDEO_ID)
        deps[METADATA_TABLE].items.extend([broken, good])
        with caplog.at_level(logging.WARNING):
            summary = handler({}, None)
        assert summary == {"scanned": 2, "indexed": 1, "skipped": 1}
        assert set(deps[INDEX_TABLE].items) == {VIDEO_ID}
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_padded_video_id_and_processed_key_stripped(self, deps):
        """Parity with the consumer (review P1): videoId and
        processedKey are stored whitespace-stripped — a padded videoId
        must land on the SAME PK the consumer would write, never on a
        phantom padded key."""
        padded_id = f"  {VIDEO_ID}  "
        deps[METADATA_TABLE].items.append(
            _meta(padded_id, st=status.PROCESSED,
                  processed_key=f"  processed/{VIDEO_ID}/fixture.mp4  "))
        handler({}, None)
        assert set(deps[INDEX_TABLE].items) == {VIDEO_ID}
        entry = deps[INDEX_TABLE].items[VIDEO_ID]
        assert entry["videoId"] == VIDEO_ID
        assert entry["processedKey"] == \
            f"processed/{VIDEO_ID}/fixture.mp4"

    def test_all_records_unusable_still_a_clean_zero_index_run(
            self, deps):
        broken = _meta(VIDEO_ID, st=status.PROCESSED)
        del broken["title"]
        deps[METADATA_TABLE].items.append(broken)
        summary = handler({}, None)
        assert summary == {"scanned": 1, "indexed": 0, "skipped": 1}
        assert deps[INDEX_TABLE].put_calls == []


# ---------------------------------------------------------------------------
# T5 — Idempotent re-invoke (matrix row 4): PK IS the dedupe
# ---------------------------------------------------------------------------

class TestIdempotentReinvoke:
    def test_second_invoke_overwrites_same_pk_never_duplicates(
            self, deps):
        deps[METADATA_TABLE].items.append(_processed(VIDEO_ID))
        first = handler({}, None)
        second = handler({}, None)
        assert first == second == {"scanned": 1, "indexed": 1,
                                   "skipped": 0}
        assert len(deps[INDEX_TABLE].put_calls) == 2
        assert set(deps[INDEX_TABLE].items) == {VIDEO_ID}
        assert deps[INDEX_TABLE].items[VIDEO_ID] == \
            deps[INDEX_TABLE].put_calls[-1]

    def test_no_delete_calls_ever(self, deps):
        deps[METADATA_TABLE].items.append(_processed(VIDEO_ID))
        handler({}, None)
        handler({}, None)
        assert deps[INDEX_TABLE].delete_calls == []


# ---------------------------------------------------------------------------
# T6 — Transient failures fail LOUDLY (matrix row 6, transcode precedent)
# ---------------------------------------------------------------------------

class TestTransientErrors:
    def test_scan_error_propagates_raw_not_mapped(self, deps):
        def flaky(**kwargs):
            raise TransientDynamoError("network")

        deps[METADATA_TABLE].scan = flaky
        try:
            result = handler({}, None)
        except TransientDynamoError:
            return  # raw propagation — loud failure, exactly right
        raise AssertionError(
            f"transient error was mapped/absorbed into {result!r} — "
            "must propagate raw, never become an HTTP-ish response")

    def test_scan_error_raises(self, deps):
        def flaky(**kwargs):
            raise TransientDynamoError("network")

        deps[METADATA_TABLE].scan = flaky
        with pytest.raises(TransientDynamoError):
            handler({}, None)

    def test_put_error_propagates_raw_not_swallowed(self, deps):
        deps[METADATA_TABLE].items.append(_processed(VIDEO_ID))

        def flaky(**kwargs):
            raise TransientDynamoError("throttled")

        deps[INDEX_TABLE].put_item = flaky
        with pytest.raises(TransientDynamoError):
            handler({}, None)


# ---------------------------------------------------------------------------
# T7 — Purity probe: only dynamodb handles are ever constructed
# ---------------------------------------------------------------------------

class TestPurity:
    def test_only_dynamodb_constructed(self, tables, monkeypatch):
        """Route shared.clients through a recorder that fails on any
        s3/events/states/sqs construction. Deliberately does NOT use the
        `deps` fixture (which patches the accessors): only `h.clients`
        is patched, so the REAL accessors run and their client choices
        are what gets recorded."""
        monkeypatch.setenv("METADATA_TABLE", METADATA_TABLE)
        monkeypatch.setenv("SEARCH_INDEX_TABLE", INDEX_TABLE)
        monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
        recorder = ClientFactoryRecorder(tables)
        import search_rebuild.handler as h
        monkeypatch.setattr(h, "clients", recorder)

        tables[METADATA_TABLE].items.append(_processed(VIDEO_ID))
        summary = handler({}, None)

        assert summary["indexed"] == 1
        assert recorder.requested
        assert set(recorder.requested) == {"dynamodb"}


# ---------------------------------------------------------------------------
# T8 — Config-not-code (NFR-4): real accessors, unset env -> RuntimeError
# ---------------------------------------------------------------------------

class TestConfigNotCode:
    """No `deps` fixture here — the REAL accessors run (unpatched)."""

    def test_missing_metadata_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("METADATA_TABLE", raising=False)
        import search_rebuild.handler as h
        with pytest.raises(RuntimeError):
            h._metadata_table()

    def test_missing_search_index_table_env_raises(self, monkeypatch):
        monkeypatch.delenv("SEARCH_INDEX_TABLE", raising=False)
        import search_rebuild.handler as h
        with pytest.raises(RuntimeError):
            h._index_table()

    def test_missing_endpoint_url_fails_loudly_raw(self, monkeypatch):
        """Tables set but AWS_ENDPOINT_URL gone: the REAL accessors run
        and shared.clients raises RuntimeError straight through the
        handler — a config gap in an admin tool fails the invocation,
        it does NOT become an HTTP-shaped envelope (no map_error)."""
        monkeypatch.setenv("METADATA_TABLE", METADATA_TABLE)
        monkeypatch.setenv("SEARCH_INDEX_TABLE", INDEX_TABLE)
        monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
        import shared.clients as clients_mod
        # Belt-and-braces: a warm resource cache would short-circuit the
        # endpoint check; force the real construction path.
        monkeypatch.setattr(clients_mod, "_resource_cache", {})
        with pytest.raises(RuntimeError):
            handler({}, None)
