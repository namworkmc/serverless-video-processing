"""ATDD suite for Story 2.3: sfn-trigger-shim Lambda (queue ->
StartExecution, AD-5).

Assertions encode the spec's I/O & Edge-Case Matrix:

| Scenario                | Expected                                            |
|-------------------------|-----------------------------------------------------|
| Happy trigger           | start_execution(name="eb-{eventId}", input =        |
|                         | exactly {videoId, status, bucket, key}); summary    |
|                         | started=1; structured log                           |
| Redelivery / republish  | ExecutionAlreadyExists -> dedupe ack (no raise)     |
| detail is a string      | json.loads it, then identical behavior              |
| Malformed record        | skipped (acked), warning log, no execution started  |
| Non-SQS event           | raises MalformedInputError                          |
| Real StartExecution err | raises (ESM retries)                                |
| Multiple records        | each processed independently; outcomes tallied      |

Plus the purity guarantee: only a `states` client is ever constructed —
never dynamodb/s3/events.

TDD Phase: GREEN
Story: 2-3-trigger-leg-eventbridge-rule-queue-and-shim
"""

import json
import logging

import pytest

from shared import events
from shared.errors import MalformedInputError
from sfn_trigger_shim.handler import handler

# ---------------------------------------------------------------------------
# Fakes (same pattern as lambdas/event_publisher/tests/test_event_publisher.py)
# ---------------------------------------------------------------------------


class ExecutionAlreadyExists(Exception):
    """boto3 raises a dynamically generated ClientError subclass named
    after the error code — the class name is the stable signal the shim
    duck-types on."""


class FakeStatesClient:
    """In-memory Step Functions stand-in recording start_execution calls."""

    def __init__(self, already_exists=False, error=None):
        self.already_exists = already_exists
        self.error = error
        self.start_calls = []

    def start_execution(self, stateMachineArn, name, input):
        if self.error is not None:
            raise self.error
        if self.already_exists:
            raise ExecutionAlreadyExists(
                f"Execution already exists: '{name}'")
        self.start_calls.append({
            "stateMachineArn": stateMachineArn,
            "name": name,
            "input": input,
        })
        return {"executionArn": f"arn:aws:states:fake:execution:{name}"}


class ClientFactoryRecorder:
    """Wraps shared.clients to record every client construction — the
    purity probe (only the states client may ever be built)."""

    def __init__(self, states_client):
        self._states = states_client
        self.requested = []

    def states_client(self):
        self.requested.append("states")
        return self._states

    def events_client(self):
        self.requested.append("events")
        raise AssertionError("purity violation: events client constructed")

    def s3_client(self):
        self.requested.append("s3")
        raise AssertionError("purity violation: s3 client constructed")

    def dynamodb_resource(self):
        self.requested.append("dynamodb")
        raise AssertionError("purity violation: dynamodb client constructed")

    def dynamodb_table(self, name):
        self.requested.append("dynamodb")
        raise AssertionError("purity violation: dynamodb client constructed")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VIDEO_ID = "11111111-2222-3333-4444-555555555555"
UPLOAD_KEY = f"{VIDEO_ID}/demo.mp4"
STATE_MACHINE_ARN = (
    "arn:aws:states:us-east-1:000000000000:"
    "stateMachine:processing-state-machine")
EVENT_ID = events.event_id(VIDEO_ID, "UPLOADED")


def _detail(**extra):
    """The flat video.uploaded detail exactly as the upload handler
    publishes it (envelope + promoted detail fields)."""
    return {
        "eventId": EVENT_ID,
        "schemaVersion": events.SCHEMA_VERSION,
        "videoId": VIDEO_ID,
        "status": "UPLOADED",
        "bucket": "video-uploads",
        "key": UPLOAD_KEY,
        "detail": {
            "videoId": VIDEO_ID,
            "status": "UPLOADED",
            "bucket": "video-uploads",
            "key": UPLOAD_KEY,
        },
        **extra,
    }


def _eb_event(detail=None):
    """The FULL EventBridge event the rule delivers as the SQS body."""
    return {
        "version": "0",
        "id": "random-bridge-id-not-used-for-dedupe",
        "detail-type": "video.uploaded",
        "source": "upload-handler",
        "account": "000000000000",
        "time": "2026-08-20T12:00:00Z",
        "region": "us-east-1",
        "resources": [],
        "detail": _detail() if detail is None else detail,
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
def sfn():
    return FakeStatesClient()


@pytest.fixture
def deps(sfn, monkeypatch):
    """Wire the fake states client + env vars into the handler (NFR-4)."""
    monkeypatch.setenv("STATE_MACHINE_ARN", STATE_MACHINE_ARN)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    import sfn_trigger_shim.handler as h
    monkeypatch.setattr(h, "_states_client", lambda: sfn)
    return {"sfn": sfn}


# ---------------------------------------------------------------------------
# Happy trigger (matrix row 1)
# ---------------------------------------------------------------------------

class TestHappyTrigger:
    def test_start_execution_called_with_deterministic_name(self, deps):
        handler(_sqs_event(json.dumps(_eb_event())), None)

        sfn = deps["sfn"]
        assert len(sfn.start_calls) == 1
        call = sfn.start_calls[0]
        assert call["name"] == f"eb-{EVENT_ID}"
        assert call["stateMachineArn"] == STATE_MACHINE_ARN

    def test_input_is_exactly_the_asl_domain_payload(self, deps):
        """Story 2.2's frozen ASL input contract: exactly {videoId,
        status, bucket, key} — envelope fields and extras dropped."""
        handler(_sqs_event(json.dumps(_eb_event())), None)

        sent = json.loads(deps["sfn"].start_calls[0]["input"])
        assert sent == {
            "videoId": VIDEO_ID,
            "status": "UPLOADED",
            "bucket": "video-uploads",
            "key": UPLOAD_KEY,
        }

    def test_execution_name_uses_detail_event_id_not_bridge_id(self, deps):
        """AD-5: the name derives from detail.eventId (deterministic
        UUID5), never the EventBridge top-level id (random on real AWS)."""
        handler(_sqs_event(json.dumps(_eb_event())), None)

        name = deps["sfn"].start_calls[0]["name"]
        assert "random-bridge-id-not-used-for-dedupe" not in name
        assert name == f"eb-{EVENT_ID}"

    def test_returns_started_summary(self, deps):
        result = handler(_sqs_event(json.dumps(_eb_event())), None)

        assert result == {
            "processed": 1, "started": 1, "deduped": 0, "skipped": 0,
        }

    def test_log_line_emitted(self, deps, caplog):
        """NFR-5: structured logging with videoId/eventId/executionName."""
        with caplog.at_level(logging.INFO, logger="sfn_trigger_shim.handler"):
            handler(_sqs_event(json.dumps(_eb_event())), None)

        log_text = " ".join(caplog.messages)
        assert VIDEO_ID in log_text
        assert EVENT_ID in log_text
        assert f"eb-{EVENT_ID}" in log_text

    def test_state_machine_arn_from_env(self, deps, monkeypatch):
        """Config-not-code (NFR-4): the ARN is the Terraform-set env var."""
        monkeypatch.setenv(
            "STATE_MACHINE_ARN", "arn:aws:states:other:sm")
        handler(_sqs_event(json.dumps(_eb_event())), None)

        assert deps["sfn"].start_calls[0]["stateMachineArn"] == (
            "arn:aws:states:other:sm")


# ---------------------------------------------------------------------------
# Redelivery / republish (matrix row 2)
# ---------------------------------------------------------------------------

class TestRedelivery:
    def test_execution_already_exists_is_acked(self, deps, sfn):
        """FR-9/NFR-1/2: ExecutionAlreadyExists is treated as success —
        no raise, so the ESM deletes the message."""
        sfn.already_exists = True

        result = handler(_sqs_event(json.dumps(_eb_event())), None)

        assert result == {
            "processed": 1, "started": 0, "deduped": 1, "skipped": 0,
        }

    def test_dedupe_logged(self, deps, sfn, caplog):
        sfn.already_exists = True

        with caplog.at_level(logging.INFO, logger="sfn_trigger_shim.handler"):
            handler(_sqs_event(json.dumps(_eb_event())), None)

        log_text = " ".join(caplog.messages)
        assert "dedupe" in log_text
        assert EVENT_ID in log_text


# ---------------------------------------------------------------------------
# detail is a string (matrix row 3)
# ---------------------------------------------------------------------------

class TestStringDetail:
    def test_json_stringified_detail_tolerated(self, deps):
        event = _eb_event(detail=json.dumps(_detail()))
        handler(_sqs_event(json.dumps(event)), None)

        sfn = deps["sfn"]
        assert len(sfn.start_calls) == 1
        assert sfn.start_calls[0]["name"] == f"eb-{EVENT_ID}"
        assert json.loads(sfn.start_calls[0]["input"]) == {
            "videoId": VIDEO_ID,
            "status": "UPLOADED",
            "bucket": "video-uploads",
            "key": UPLOAD_KEY,
        }


# ---------------------------------------------------------------------------
# Malformed records (matrix row 4) — logged + acked, never raised
# ---------------------------------------------------------------------------

class TestMalformedRecord:
    @pytest.mark.parametrize("body", [
        "not-json-at-all",
        json.dumps(["a", "list"]),
        json.dumps(None),
        json.dumps({"no": "detail"}),
        json.dumps({"detail": "not-json-either"}),
        json.dumps({"detail": ["detail", "is", "a", "list"]}),
    ])
    def test_unparseable_body_skipped(self, deps, body):
        result = handler(_sqs_event(body), None)

        assert result["skipped"] == 1
        assert result["started"] == 0
        assert deps["sfn"].start_calls == []

    def test_missing_event_id_skipped(self, deps):
        detail = _detail()
        del detail["eventId"]
        result = handler(_sqs_event(json.dumps(_eb_event(detail=detail))),
                         None)

        assert result["skipped"] == 1
        assert deps["sfn"].start_calls == []

    @pytest.mark.parametrize(
        "field", ["videoId", "status", "bucket", "key"])
    def test_missing_required_field_skipped(self, deps, field):
        detail = _detail()
        del detail[field]
        result = handler(_sqs_event(json.dumps(_eb_event(detail=detail))),
                         None)

        assert result["skipped"] == 1
        assert deps["sfn"].start_calls == []

    @pytest.mark.parametrize(
        "field", ["videoId", "status", "bucket", "key"])
    def test_empty_required_field_skipped(self, deps, field):
        detail = _detail(**{field: "   "})
        result = handler(_sqs_event(json.dumps(_eb_event(detail=detail))),
                         None)

        assert result["skipped"] == 1
        assert deps["sfn"].start_calls == []

    def test_malformed_record_warns(self, deps, caplog):
        with caplog.at_level(
                logging.WARNING, logger="sfn_trigger_shim.handler"):
            handler(_sqs_event("not-json"), None)

        assert any("malformed" in m for m in caplog.messages)

    @pytest.mark.parametrize("event_id", [
        "bad id with spaces",
        "bad/slash",
        "x" * 200,
    ])
    def test_unusable_event_id_skipped(self, deps, event_id):
        """An eventId that cannot become a valid SFN execution name is a
        deterministic poison message — skipped, never retried."""
        detail = _detail(eventId=event_id)
        result = handler(_sqs_event(json.dumps(_eb_event(detail=detail))),
                         None)

        assert result["skipped"] == 1
        assert deps["sfn"].start_calls == []

    def test_non_dict_record_skipped(self, deps):
        result = handler({"Records": ["not-a-dict"]}, None)

        assert result["skipped"] == 1
        assert deps["sfn"].start_calls == []


# ---------------------------------------------------------------------------
# Non-SQS event (matrix row 5)
# ---------------------------------------------------------------------------

class TestNonSqsEvent:
    @pytest.mark.parametrize("event", [
        None,
        "not-a-dict",
        ["list"],
        {},
        {"Records": "not-a-list"},
        {"Records": None},
    ])
    def test_raises_malformed_input(self, deps, event):
        with pytest.raises(MalformedInputError):
            handler(event, None)


# ---------------------------------------------------------------------------
# Real StartExecution error (matrix row 6)
# ---------------------------------------------------------------------------

class TestRealStartError:
    def test_other_errors_raise(self, deps, sfn):
        """Anything that is not ExecutionAlreadyExists propagates so the
        ESM retries the message."""
        sfn.error = RuntimeError("throttled")

        with pytest.raises(RuntimeError, match="throttled"):
            handler(_sqs_event(json.dumps(_eb_event())), None)


# ---------------------------------------------------------------------------
# Multiple records (matrix row 7)
# ---------------------------------------------------------------------------

class TestMultipleRecords:
    def test_each_record_processed_independently(self, deps, sfn):
        good = json.dumps(_eb_event())
        result = handler(_sqs_event(good, "garbage", good), None)

        assert result == {
            "processed": 3, "started": 2, "deduped": 0, "skipped": 1,
        }
        assert len(sfn.start_calls) == 2

    def test_empty_records_list_is_a_noop(self, deps):
        result = handler({"Records": []}, None)

        assert result == {
            "processed": 0, "started": 0, "deduped": 0, "skipped": 0,
        }
        assert deps["sfn"].start_calls == []


# ---------------------------------------------------------------------------
# Unset env
# ---------------------------------------------------------------------------

class TestUnsetEnv:
    def test_missing_state_machine_arn_raises(self, deps, monkeypatch):
        monkeypatch.delenv("STATE_MACHINE_ARN")

        with pytest.raises(RuntimeError, match="STATE_MACHINE_ARN"):
            handler(_sqs_event(json.dumps(_eb_event())), None)

    def test_missing_env_starts_nothing(self, deps, monkeypatch):
        monkeypatch.delenv("STATE_MACHINE_ARN")

        with pytest.raises(RuntimeError):
            handler(_sqs_event(json.dumps(_eb_event())), None)

        assert deps["sfn"].start_calls == []


# ---------------------------------------------------------------------------
# Purity guarantee
# ---------------------------------------------------------------------------

class TestPurity:
    def test_only_states_client_constructed(self, deps, sfn, monkeypatch):
        """Purity probe: route shared.clients through a recorder that
        fails on any events/s3/dynamodb construction; the handler may
        only ever ask for states."""
        recorder = ClientFactoryRecorder(sfn)
        import sfn_trigger_shim.handler as h
        monkeypatch.setattr(h, "clients", recorder)
        monkeypatch.setattr(h, "_states_client",
                            lambda: recorder.states_client())

        result = handler(_sqs_event(json.dumps(_eb_event())), None)

        assert result["started"] == 1
        assert recorder.requested == ["states"]

    def test_handler_module_imports_only_clients_from_shared(self):
        """The module must not import shared.status or shared.events —
        the shim neither writes status nor constructs envelopes. AST-based
        so docstrings mentioning them can't false-positive."""
        import ast
        import inspect

        import sfn_trigger_shim.handler as h
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
        assert "shared.clients" in imported
        assert any(m == "shared.errors" or m.startswith("shared.errors.")
                   for m in imported)
        assert not any(m == "shared.status" or m.startswith("shared.status.")
                       for m in imported)
        assert not any(m == "shared.events" or m.startswith("shared.events.")
                       for m in imported)
