"""Smoke Lambda — proves the shared layer inside floci's real Docker runtime.

Invoked ad-hoc (never by a gateway route). Runs scenario-driven checks of
the shared access layer against the real video-metadata table:

    {"scenario": "all"}                -- every scenario, in order
    {"scenario": "create"}             -- create_record via PutItem condition
    {"scenario": "create-idempotent"}  -- second create returns existing
    {"scenario": "transition-legal"}   -- UPLOADED -> PROCESSING
    {"scenario": "transition-illegal"} -- UPLOADED -> PROCESSED => conflict
    {"scenario": "reassert"}           -- same-status re-assertion, no write
    {"scenario": "envelope"}           -- deterministic eventId + shape

Also reports boto3 availability in the runtime image (confirmed present by
the Story 1.2 run). The handler deletes its fixed test record at the end of
every run, so reruns and Story 1.3 start from an empty table.
"""

import os
import traceback

from shared import clients, events, status
from shared.errors import ConflictError, NotFoundError

TEST_VIDEO_ID = "smoke-00000000-0000-0000-0000-000000000001"
TEST_TITLE = "smoke-test.mp4"
TEST_BUCKET = "video-uploads"
TEST_KEY = f"uploads/{TEST_VIDEO_ID}/{TEST_TITLE}"


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


SCENARIOS = {
    "create": scenario_create,
    "create-idempotent": scenario_create_idempotent,
    "transition-legal": scenario_transition_legal,
    "transition-illegal": scenario_transition_illegal,
    "reassert": scenario_reassert,
    "envelope": scenario_envelope,
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
