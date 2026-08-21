"""Smoke Lambda — proves the shared layer and the deployed epic-2 wiring
inside floci's real Docker runtime. Invoked ad-hoc (never by a gateway
route) and by ci-local.sh stage 5. Runs scenario-driven checks:

    {"scenario": "all"}                -- every scenario, in order
    {"scenario": "create"}             -- create_record via PutItem condition
    {"scenario": "create-idempotent"}  -- second create returns existing
    {"scenario": "transition-legal"}   -- UPLOADED -> PROCESSING
    {"scenario": "transition-illegal"} -- UPLOADED -> PROCESSED => conflict
    {"scenario": "reassert"}           -- same-status re-assertion, no write
    {"scenario": "envelope"}           -- deterministic eventId + shape
    {"scenario": "transcode"}          -- invoke deployed transcode zip
    {"scenario": "state-machine"}      -- StartExecution -> PROCESSED -> re-run fails
    {"scenario": "trigger-leg"}        -- publish -> rule -> queue -> shim -> SFN

The runtime scenarios (transcode, state-machine, trigger-leg) backstop the
DEPLOYED wiring — zip layout, handler strings, env vars, IAM, ESM — which
unit tests and terraform validate cannot see. They use a fresh uuid4
videoId per run so reruns never collide, and delete their records/objects
afterwards. Executions cannot be deleted in floci; their names are unique
per run, so leftovers are harmless.

Also reports boto3 availability in the runtime image (confirmed present by
the Story 1.2 run). The handler deletes its fixed test record at the end of
every run, so reruns and Story 1.3 start from an empty table.
"""

import json
import os
import time
import traceback
import uuid

from shared import clients, events, status
from shared.errors import ConflictError, NotFoundError

TEST_VIDEO_ID = "smoke-00000000-0000-0000-0000-000000000001"
TEST_TITLE = "smoke-test.mp4"
TEST_BUCKET = "video-uploads"
TEST_KEY = f"uploads/{TEST_VIDEO_ID}/{TEST_TITLE}"

# Fixture object body for the runtime scenarios (ASCII text — the lab's
# documented text-safe limitation; binary transport through the gateway is
# a separate tracked gap).
FIXTURE_BODY = b"smoke-fixture-video-bytes"
FIXTURE_NAME = "fixture.mp4"


def _check(condition, message):
    """Explicit check — unlike bare assert, survives python -O and carries
    context in the failure report."""
    if not condition:
        raise AssertionError(message)


def _table():
    table_name = os.environ.get("TABLE_NAME")
    if not table_name:
        raise RuntimeError("TABLE_NAME env var is not set")
    return clients.dynamodb_table(table_name)


def _cleanup(table):
    try:
        table.delete_item(Key={"videoId": TEST_VIDEO_ID})
    except Exception:  # noqa: BLE001 - cleanup must never fail the run
        pass


def _ensure_fresh(table):
    _cleanup(table)


# ---------------------------------------------------------------------------
# Shared-layer scenarios (Story 1.2)
# ---------------------------------------------------------------------------

def scenario_create(table):
    _ensure_fresh(table)
    rec = status.create_record(
        table, TEST_VIDEO_ID, TEST_TITLE, TEST_BUCKET, TEST_KEY,
        content_type="video/mp4", size_bytes=1)
    _check(rec["status"] == "UPLOADED", f"expected UPLOADED, got {rec}")
    _check(rec["createdAt"] and rec["updatedAt"],
           f"missing timestamps in {rec}")
    return {"created": rec["videoId"], "status": rec["status"]}


def scenario_create_idempotent(table):
    _ensure_fresh(table)
    first = status.create_record(table, TEST_VIDEO_ID, TEST_TITLE,
                                 TEST_BUCKET, TEST_KEY)
    second = status.create_record(table, TEST_VIDEO_ID, "OTHER.mp4",
                                  "other-bucket", "other/key")
    _check(second == first, f"retry did not return existing record: "
                            f"{first} vs {second}")
    return {"idempotent": True, "title": second["title"]}


def scenario_transition_legal(table):
    _ensure_fresh(table)
    status.create_record(table, TEST_VIDEO_ID, TEST_TITLE, TEST_BUCKET,
                         TEST_KEY)
    rec = status.transition(table, TEST_VIDEO_ID, status.PROCESSING)
    _check(rec["status"] == "PROCESSING", f"expected PROCESSING, got {rec}")
    stored = status.get_record(table, TEST_VIDEO_ID)
    _check(stored["status"] == "PROCESSING",
           f"table not updated: {stored}")
    return {"from": "UPLOADED", "to": stored["status"]}


def scenario_transition_illegal(table):
    _ensure_fresh(table)
    status.create_record(table, TEST_VIDEO_ID, TEST_TITLE, TEST_BUCKET,
                         TEST_KEY)
    try:
        status.transition(table, TEST_VIDEO_ID, status.PROCESSED)
    except ConflictError as exc:
        stored = status.get_record(table, TEST_VIDEO_ID)
        _check(stored["status"] == "UPLOADED",
               f"record mutated by rejected transition: {stored}")
        return {"rejected": True, "conflict": str(exc),
                "status_untouched": stored["status"]}
    raise AssertionError("illegal transition was accepted")


def scenario_reassert(table):
    _ensure_fresh(table)
    status.create_record(table, TEST_VIDEO_ID, TEST_TITLE, TEST_BUCKET,
                         TEST_KEY)
    rec = status.transition(table, TEST_VIDEO_ID, status.UPLOADED)
    _check(rec["status"] == "UPLOADED", f"re-assertion failed: {rec}")
    return {"reasserted": "UPLOADED", "idempotent": True}


def scenario_envelope(table):
    detail = events.uploaded_detail(TEST_VIDEO_ID, TEST_BUCKET, TEST_KEY)
    env1 = events.build_envelope(events.EVENT_UPLOADED, detail)
    env2 = events.build_envelope(events.EVENT_UPLOADED, detail)
    _check(env1["eventId"] == env2["eventId"],
           f"eventId not deterministic: {env1} vs {env2}")
    _check(set(env1) == {"eventId", "schemaVersion", "detail"},
           f"envelope shape wrong: {env1}")
    processed = events.build_envelope(
        events.EVENT_PROCESSED,
        events.processed_detail(TEST_VIDEO_ID, TEST_BUCKET, TEST_KEY,
                                f"processed/{TEST_VIDEO_ID}.mp4"))
    _check(processed["eventId"] != env1["eventId"],
           "different (videoId, status) produced the same eventId")
    return {"eventId": env1["eventId"], "schemaVersion": env1["schemaVersion"],
            "deterministic": True}


# ---------------------------------------------------------------------------
# Runtime scenarios (retro action item: backstop deployed epic-2 wiring)
# ---------------------------------------------------------------------------

def _env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} env var is not set")
    return value


def _seed_video(table, video_id, key):
    """Fixture object in video-uploads + UPLOADED metadata record."""
    clients.s3_client().put_object(
        Bucket=_env("UPLOADS_BUCKET"), Key=key, Body=FIXTURE_BODY,
        ContentType="video/mp4")
    status.create_record(table, video_id, FIXTURE_NAME,
                         _env("UPLOADS_BUCKET"), key,
                         content_type="video/mp4",
                         size_bytes=len(FIXTURE_BODY))


def _cleanup_video(table, video_id, key, processed_key=None):
    try:
        table.delete_item(Key={"videoId": video_id})
    except Exception:  # noqa: BLE001 - cleanup must never fail the run
        pass
    s3 = clients.s3_client()
    for bucket, k in [(_env("UPLOADS_BUCKET"), key)] + (
            [(_env("PROCESSED_BUCKET"), processed_key)]
            if processed_key else []):
        try:
            s3.delete_object(Bucket=bucket, Key=k)
        except Exception:  # noqa: BLE001
            pass


def _invoke_lambda(function_name, payload):
    """Invoke a deployed function; return the parsed JSON result. Raises
    with the function error when the invocation failed."""
    resp = clients.lambda_client().invoke(
        FunctionName=function_name,
        Payload=json.dumps(payload).encode("utf-8"))
    body = json.loads(resp["Payload"].read())
    if resp.get("FunctionError") or (
            isinstance(body, dict) and body.get("errorType")):
        raise RuntimeError(
            f"{function_name} invocation failed: {body}")
    return body


def scenario_transcode(table):
    """Invoke the DEPLOYED transcode zip: fixture object -> processed
    object, payload contract intact."""
    video_id = f"smoke-tc-{uuid.uuid4()}"
    key = f"{video_id}/{FIXTURE_NAME}"
    processed_key = f"processed/{video_id}/{FIXTURE_NAME}"
    _seed_video(table, video_id, key)
    try:
        result = _invoke_lambda("transcode", {
            "videoId": video_id, "originalKey": key})
        _check(result["videoId"] == video_id, f"payload videoId: {result}")
        _check(result["processedKey"] == processed_key,
               f"payload processedKey: {result}")
        _check(result["sizeBytes"] == len(FIXTURE_BODY),
               f"payload sizeBytes: {result}")
        obj = clients.s3_client().get_object(
            Bucket=_env("PROCESSED_BUCKET"), Key=processed_key)
        _check(obj["Body"].read() == FIXTURE_BODY,
               "processed object body differs from fixture")
        # Pure worker (AD-4): the metadata record must stay UPLOADED.
        stored = status.get_record(table, video_id)
        _check(stored["status"] == "UPLOADED",
               f"transcode mutated status: {stored}")
        return {"videoId": video_id, "processedKey": processed_key,
                "sizeBytes": result["sizeBytes"]}
    finally:
        _cleanup_video(table, video_id, key, processed_key)


def _wait_execution(sfn, execution_arn, timeout_s=60):
    """Poll describe_execution until a terminal status."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        desc = sfn.describe_execution(executionArn=execution_arn)
        if desc["status"] in ("SUCCEEDED", "FAILED", "TIMED_OUT",
                              "ABORTED"):
            return desc
        time.sleep(1)
    raise RuntimeError(
        f"execution {execution_arn} still running after {timeout_s}s")


def scenario_state_machine(table):
    """Drive the DEPLOYED state machine end to end: StartExecution ->
    record walks to PROCESSED -> processed object exists -> exactly one
    video.processed with the deterministic eventId -> re-run fails at
    MarkProcessing with no regression and no second event."""
    video_id = f"smoke-sm-{uuid.uuid4()}"
    key = f"{video_id}/{FIXTURE_NAME}"
    processed_key = f"processed/{video_id}/{FIXTURE_NAME}"
    _seed_video(table, video_id, key)
    sfn = clients.states_client()
    execution_name = f"smoke-sm-{uuid.uuid4()}"
    _drain_capture_queue()
    try:
        start = sfn.start_execution(
            stateMachineArn=_env("STATE_MACHINE_ARN"),
            name=execution_name,
            input=json.dumps({
                "videoId": video_id, "status": "UPLOADED",
                "bucket": _env("UPLOADS_BUCKET"), "key": key}))
        desc = _wait_execution(sfn, start["executionArn"])
        _check(desc["status"] == "SUCCEEDED",
               f"execution status: {desc['status']}")
        stored = status.get_record(table, video_id)
        _check(stored["status"] == "PROCESSED",
               f"record status: {stored['status']}")
        _check(stored.get("processedKey") == processed_key,
               f"record processedKey: {stored.get('processedKey')}")
        obj = clients.s3_client().get_object(
            Bucket=_env("PROCESSED_BUCKET"), Key=processed_key)
        _check(obj["Body"].read() == FIXTURE_BODY,
               "processed object body differs from fixture")
        # Exactly one video.processed with the deterministic eventId.
        expected_event_id = events.event_id(video_id, "PROCESSED")
        found = _collect_processed_events(video_id)
        _check(len(found) == 1,
               f"expected exactly 1 video.processed, found {len(found)}")
        got_id = (_detail_of(found[0]) or {}).get("eventId")
        _check(got_id == expected_event_id,
               f"eventId mismatch: {got_id} != {expected_event_id}")
        # Re-run: fails at MarkProcessing (record already PROCESSED),
        # no regression, no second event.
        rerun = sfn.start_execution(
            stateMachineArn=_env("STATE_MACHINE_ARN"),
            name=f"{execution_name}-rerun",
            input=json.dumps({
                "videoId": video_id, "status": "UPLOADED",
                "bucket": _env("UPLOADS_BUCKET"), "key": key}))
        rerun_desc = _wait_execution(sfn, rerun["executionArn"])
        _check(rerun_desc["status"] == "FAILED",
               f"re-run should fail, got {rerun_desc['status']}")
        stored = status.get_record(table, video_id)
        _check(stored["status"] == "PROCESSED",
               f"re-run regressed status: {stored['status']}")
        found = _collect_processed_events(video_id, timeout_s=10)
        _check(len(found) == 0,
               f"re-run published another event: {len(found)}")
        return {"videoId": video_id, "execution": execution_name,
                "rerun_failed": True, "events": 1}
    finally:
        _cleanup_video(table, video_id, key, processed_key)


def _receive_capture_queue(max_messages=100):
    """Receive whatever is pending on the smoke capture queue (declared in
    terraform/smoke.tf; the video.processed rule targets it). Returns the
    parsed EventBridge envelopes; deletes every message it reads — the
    queue is a smoke fixture, its backlog is test residue."""
    sqs = clients.sqs_client()
    queue_url = _env("CAPTURE_QUEUE_URL")
    envelopes = []
    while len(envelopes) < max_messages:
        resp = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10,
            WaitTimeSeconds=0)
        messages = resp.get("Messages") or []
        if not messages:
            break
        for msg in messages:
            try:
                envelopes.append(json.loads(msg["Body"]))
            except ValueError:
                pass
            sqs.delete_message(
                QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
    return envelopes


def _drain_capture_queue():
    """Empty the capture queue so a scenario counts only its own events."""
    while _receive_capture_queue():
        pass


def _detail_of(envelope):
    """The EventBridge envelope's detail as a dict (tolerating a
    JSON-stringified detail), or None."""
    detail = envelope.get("detail")
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return None
    return detail if isinstance(detail, dict) else None


def _collect_processed_events(video_id, timeout_s=30):
    """Collect video.processed envelopes for this videoId arriving on the
    capture queue within the window."""
    found = []
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for envelope in _receive_capture_queue():
            detail = _detail_of(envelope)
            if detail and detail.get("videoId") == video_id:
                found.append(envelope)
        if found:
            # Give redeliveries/duplicates a moment to show up, then stop.
            time.sleep(3)
            for envelope in _receive_capture_queue():
                detail = _detail_of(envelope)
                if detail and detail.get("videoId") == video_id:
                    found.append(envelope)
            break
        time.sleep(1)
    return found


def scenario_trigger_leg(table):
    """Drive the DEPLOYED trigger leg: publish video.uploaded on the bus ->
    rule -> processing-trigger-queue -> shim (ESM) -> StartExecution.
    Asserts the execution exists and the record walks to PROCESSED."""
    video_id = f"smoke-tl-{uuid.uuid4()}"
    key = f"{video_id}/{FIXTURE_NAME}"
    processed_key = f"processed/{video_id}/{FIXTURE_NAME}"
    _seed_video(table, video_id, key)
    try:
        envelope = events.build_envelope(
            events.EVENT_UPLOADED,
            events.uploaded_detail(video_id, _env("UPLOADS_BUCKET"), key))
        detail_payload = {**envelope, **envelope["detail"]}
        put_resp = clients.events_client().put_events(Entries=[{
            "Source": "smoke",
            "DetailType": events.EVENT_UPLOADED,
            "Detail": json.dumps(detail_payload),
            "EventBusName": _env("EVENT_BUS_NAME"),
        }])
        _check(not put_resp.get("FailedEntryCount"),
               f"put_events rejected: {put_resp}")
        # The shim runs asynchronously via the ESM; poll the record until
        # the state machine walks it to PROCESSED (rule -> queue -> shim ->
        # StartExecution -> ASL).
        deadline = time.time() + 90
        final = None
        while time.time() < deadline:
            stored = status.get_record(table, video_id)
            final = stored["status"]
            if stored["status"] == "PROCESSED":
                break
            time.sleep(2)
        _check(final == "PROCESSED",
               f"trigger leg did not process the video: status={final}")
        obj = clients.s3_client().get_object(
            Bucket=_env("PROCESSED_BUCKET"), Key=processed_key)
        _check(obj["Body"].read() == FIXTURE_BODY,
               "processed object body differs from fixture")
        return {"videoId": video_id, "eventId": envelope["eventId"],
                "status": final}
    finally:
        _cleanup_video(table, video_id, key, processed_key)


SCENARIOS = {
    "create": scenario_create,
    "create-idempotent": scenario_create_idempotent,
    "transition-legal": scenario_transition_legal,
    "transition-illegal": scenario_transition_illegal,
    "reassert": scenario_reassert,
    "envelope": scenario_envelope,
    "transcode": scenario_transcode,
    "state-machine": scenario_state_machine,
    "trigger-leg": scenario_trigger_leg,
}


def lambda_handler(event, context):
    scenario = (event or {}).get("scenario", "all")
    if scenario != "all" and scenario not in SCENARIOS:
        return {"statusCode": 400,
                "body": {"error": f"unknown scenario: {scenario}"}}
    table = _table()
    report = {
        "boto3_available": clients.BOTO3_AVAILABLE,
        "table": os.environ.get("TABLE_NAME"),
        "endpoint": os.environ.get("AWS_ENDPOINT_URL"),
        "scenarios": {},
    }
    names = list(SCENARIOS) if scenario == "all" else [scenario]
    ok = True
    try:
        for name in names:
            try:
                result = SCENARIOS[name](table)
                report["scenarios"][name] = {"pass": True, "result": result}
            except Exception as exc:  # noqa: BLE001 - report, don't die
                ok = False
                report["scenarios"][name] = {
                    "pass": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc(limit=3),
                }
        # Unknown-videoId probe: NotFoundError, never silent success.
        try:
            status.get_record(table, "smoke-does-not-exist")
            ok = False
            report["scenarios"]["not-found"] = {
                "pass": False, "error": "unknown videoId did not raise"}
        except NotFoundError:
            report["scenarios"]["not-found"] = {"pass": True}
    finally:
        _cleanup(table)  # reruns and Story 1.3 start from an empty table
    report["all_pass"] = ok and all(
        s.get("pass") for s in report["scenarios"].values())
    return {"statusCode": 200 if report["all_pass"] else 500,
            "body": report}
