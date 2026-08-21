"""Unit tests for the shared access layer (stdlib-only fake DynamoDB table).

Covers every I/O Matrix row that does not require the floci runtime:
transition table, idempotent create, idempotent re-assert, UUID5
determinism, envelope shape, error mapping.
"""

import uuid

import pytest

from shared import events, status  # noqa: E402
from shared.errors import (  # noqa: E402
    ConflictError,
    MalformedInputError,
    NotFoundError,
    map_error,
)


class ConditionalCheckFailedException(Exception):
    """Mimics boto3's exception type name (duck-typed by the layer)."""


class FakeTable:
    """In-memory stand-in for a boto3 resource Table honouring the two
    conditional expressions the layer uses."""

    def __init__(self):
        self.items = {}
        self.update_calls = 0

    def put_item(self, Item, ConditionExpression=None):
        key = Item["videoId"]
        if ConditionExpression == "attribute_not_exists(videoId)":
            if key in self.items:
                raise ConditionalCheckFailedException(
                    "attribute_not_exists(videoId) failed")
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
            raise ConditionalCheckFailedException("key not present")
        if ConditionExpression == "#s = :expected":
            if item.get("status") != ExpressionAttributeValues[":expected"]:
                raise ConditionalCheckFailedException(
                    "#s = :expected failed")
        # Apply SET clauses: "#s = :next", "updatedAt = :updatedAt",
        # "#extra_<name> = :extra_<name>"
        for clause in UpdateExpression[len("SET "):].split(", "):
            name_ref, value_ref = clause.split(" = ")
            attr = ExpressionAttributeNames.get(name_ref, name_ref)
            item[attr] = ExpressionAttributeValues[value_ref]
        self.update_calls += 1
        resp = {}
        if ReturnValues == "ALL_NEW":
            resp["Attributes"] = dict(item)
        return resp


@pytest.fixture
def table():
    return FakeTable()


@pytest.fixture
def uploaded(table):
    return status.create_record(
        table, "vid-1", "demo.mp4", "video-uploads", "uploads/vid-1/demo.mp4",
        content_type="video/mp4", size_bytes=1024)


# --- create (FR-12) ---

def test_create_returns_uploaded_record(table):
    rec = status.create_record(table, "vid-1", "t", "b", "k")
    assert rec["status"] == "UPLOADED"
    assert rec["createdAt"] and rec["updatedAt"]


def test_create_twice_returns_existing_unchanged(table, uploaded):
    again = status.create_record(table, "vid-1", "OTHER", "b2", "k2")
    assert again == uploaded  # existing record, unchanged


def test_create_requires_video_id_and_title(table):
    with pytest.raises(MalformedInputError):
        status.create_record(table, "", "t", "b", "k")
    with pytest.raises(MalformedInputError):
        status.create_record(table, "v", "", "b", "k")


def test_create_requires_bucket_and_key(table):
    with pytest.raises(MalformedInputError):
        status.create_record(table, "v", "t", "", "k")
    with pytest.raises(MalformedInputError):
        status.create_record(table, "v", "t", "b", "")


def test_create_rejects_non_integer_size(table):
    with pytest.raises(MalformedInputError):
        status.create_record(table, "v", "t", "b", "k", size_bytes="big")


# --- get (FR-13) ---

def test_get_unknown_raises_not_found(table):
    with pytest.raises(NotFoundError):
        status.get_record(table, "missing")


def test_transition_unknown_video_id_raises_not_found(table):
    with pytest.raises(NotFoundError):
        status.transition(table, "missing", status.PROCESSING)


# --- transition table (FR-11) ---

def test_legal_transitions_succeed(table, uploaded):
    rec = status.transition(table, "vid-1", status.PROCESSING)
    assert rec["status"] == "PROCESSING"
    rec = status.transition(table, "vid-1", status.PROCESSED)
    assert rec["status"] == "PROCESSED"


def test_legal_transition_to_failed(table, uploaded):
    status.transition(table, "vid-1", status.PROCESSING)
    rec = status.transition(table, "vid-1", status.FAILED,
                            extra_attributes={"failureReason": "boom"})
    assert rec["status"] == "FAILED"
    assert rec["failureReason"] == "boom"


def test_illegal_transition_raises_conflict(table, uploaded):
    with pytest.raises(ConflictError):
        status.transition(table, "vid-1", status.PROCESSED)  # skips PROCESSING
    assert table.items["vid-1"]["status"] == "UPLOADED"  # untouched


def test_transition_out_of_terminal_raises_conflict(table, uploaded):
    status.transition(table, "vid-1", status.PROCESSING)
    status.transition(table, "vid-1", status.PROCESSED)
    with pytest.raises(ConflictError):
        status.transition(table, "vid-1", status.PROCESSING)
    assert table.items["vid-1"]["status"] == "PROCESSED"  # terminal is final


def test_transition_back_to_uploaded_raises_conflict(table, uploaded):
    # UPLOADED is only ever minted by create_record, never by transition.
    status.transition(table, "vid-1", status.PROCESSING)
    with pytest.raises(ConflictError):
        status.transition(table, "vid-1", status.UPLOADED)
    assert table.items["vid-1"]["status"] == "PROCESSING"


def test_transition_returns_tables_post_update_item(table, uploaded):
    rec = status.transition(table, "vid-1", status.PROCESSING)
    assert rec == table.items["vid-1"]  # actual stored item, not a rebuild


def test_transition_extra_none_value_is_skipped(table, uploaded):
    rec = status.transition(table, "vid-1", status.PROCESSING,
                            extra_attributes={"failureReason": None})
    assert "failureReason" not in rec


def test_transition_record_vanished_between_get_and_update(table, uploaded):
    """Record deleted after the initial read: the conflict re-read must
    surface NotFoundError, not a stale conflict message."""
    real_update = table.update_item

    def delete_on_update(*args, **kwargs):
        table.items.pop("vid-1", None)
        raise ConditionalCheckFailedException("#s = :expected failed")

    table.update_item = delete_on_update
    try:
        with pytest.raises(NotFoundError):
            status.transition(table, "vid-1", status.PROCESSING)
    finally:
        table.update_item = real_update


def test_unknown_target_status_is_malformed(table, uploaded):
    with pytest.raises(MalformedInputError):
        status.transition(table, "vid-1", "NOPE")


def test_disallowed_extra_attribute_is_malformed(table, uploaded):
    with pytest.raises(MalformedInputError):
        status.transition(table, "vid-1", status.PROCESSING,
                          extra_attributes={"title": "hack"})


# --- idempotent re-assertion ---

def test_reassert_current_status_is_noop(table, uploaded):
    calls_before = table.update_calls
    rec = status.transition(table, "vid-1", status.UPLOADED)
    assert rec["status"] == "UPLOADED"
    assert table.update_calls == calls_before  # no write side effect


# --- event envelopes (NFR-2) ---

def test_event_id_is_deterministic_uuid5():
    a = events.event_id("vid-1", "PROCESSED")
    b = events.event_id("vid-1", "PROCESSED")
    assert a == b
    assert uuid.UUID(a).version == 5
    assert events.event_id("vid-1", "UPLOADED") != a  # status changes id


def test_envelope_shape():
    detail = events.uploaded_detail("vid-1", "video-uploads", "k")
    env = events.build_envelope(events.EVENT_UPLOADED, detail)
    assert set(env) == {"eventId", "schemaVersion", "detail"}
    assert env["eventId"] == events.event_id("vid-1", "UPLOADED")
    assert env["schemaVersion"] == events.SCHEMA_VERSION
    assert env["detail"] == detail


def test_detail_shapes_fixed():
    up = events.uploaded_detail("v", "b", "k")
    assert set(up) == {"videoId", "status", "bucket", "key"}
    pr = events.processed_detail("v", "b", "ok", "pk")
    assert set(pr) == {"videoId", "status", "bucket", "originalKey",
                       "processedKey"}


def test_envelope_requires_video_id_and_status():
    with pytest.raises(MalformedInputError):
        events.build_envelope(events.EVENT_UPLOADED, {"videoId": "v"})


def test_envelope_rejects_unknown_event_name():
    with pytest.raises(MalformedInputError):
        events.build_envelope("video.exploded",
                              {"videoId": "v", "status": "UPLOADED"})


def test_envelope_rejects_status_mismatch():
    # A video.uploaded envelope cannot carry status PROCESSED.
    with pytest.raises(MalformedInputError):
        events.build_envelope(
            events.EVENT_UPLOADED,
            {"videoId": "v", "status": "PROCESSED", "bucket": "b",
             "key": "k"})


# --- error mapping (NFR-3) ---

def test_error_mapping():
    assert map_error(ConflictError("c")) == (409, {"error": "c"})
    assert map_error(NotFoundError("n")) == (404, {"error": "n"})
    assert map_error(MalformedInputError("m")) == (400, {"error": "m"})
    code, body = map_error(ConditionalCheckFailedException("ccf"))
    assert code == 409 and body == {"error": "ccf"}
    code, body = map_error(RuntimeError("x"))
    assert code == 500 and "RuntimeError" in body["error"]


# --- clients (NFR-4) ---

def test_clients_require_endpoint_env(monkeypatch):
    from shared import clients
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with pytest.raises(RuntimeError, match="AWS_ENDPOINT_URL"):
        clients._endpoint_url()


def test_clients_region_fallback(monkeypatch):
    from shared import clients
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    assert clients._region() == "us-east-1"
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    assert clients._region() == "eu-west-1"


def test_client_factories_use_correct_service_names(monkeypatch):
    """A service-name typo fails first at live invoke — pin every factory's
    boto3 service name (retro action item)."""
    from shared import clients

    constructed = []

    class FakeBoto3:
        @staticmethod
        def client(service, **kwargs):
            constructed.append(("client", service))
            return object()

        @staticmethod
        def resource(service, **kwargs):
            constructed.append(("resource", service))
            return object()

    monkeypatch.setattr(clients, "boto3", FakeBoto3)
    monkeypatch.setattr(clients, "BOTO3_AVAILABLE", True)
    monkeypatch.setattr(clients, "_client_cache", {})
    monkeypatch.setattr(clients, "_resource_cache", {})
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")

    clients.s3_client()
    clients.events_client()
    clients.states_client()
    clients.sqs_client()
    clients.lambda_client()
    clients.dynamodb_resource()

    assert constructed == [
        ("client", "s3"),
        ("client", "events"),
        ("client", "stepfunctions"),
        ("client", "sqs"),
        ("client", "lambda"),
        ("resource", "dynamodb"),
    ]


# --- require_field (shared payload validation) ---

def test_require_field_returns_stripped_value():
    from shared.errors import require_field
    assert require_field({"k": "  padded  "}, "k") == "padded"


def test_require_field_rejects_missing_empty_nonstring():
    from shared.errors import require_field
    for event in ({}, {"k": ""}, {"k": "   "}, {"k": 42}, {"k": None},
                  "not-a-dict", None):
        with pytest.raises(MalformedInputError):
            require_field(event, "k")


# --- ClientError-code duck typing ---

def test_is_client_error_code_matches_class_name():
    from shared.errors import is_client_error_code

    class ExecutionAlreadyExists(Exception):
        pass

    assert is_client_error_code(ExecutionAlreadyExists("x"),
                                "ExecutionAlreadyExists")
    assert not is_client_error_code(RuntimeError("x"),
                                    "ExecutionAlreadyExists")
    assert is_client_error_code(ConditionalCheckFailedException("x"),
                                "ConditionalCheckFailedException")
