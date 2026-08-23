"""T5–T6 — ad-hoc state machine runs (Story 2.2)."""

import uuid

from conftest import PROCESSED_BUCKET, UPLOADS_BUCKET, event_id


def test_t5_ad_hoc_start_execution(stack, binary_payload, video_id):
    """Seed fixture object + UPLOADED record -> StartExecution with the
    domain payload -> PROCESSED with processedKey -> processed object exists
    -> exactly one processed event."""
    stack.drain_capture_queue()
    key = stack.seed_video(video_id, binary_payload)
    processed_key = f"processed/{video_id}/fixture.mp4"

    start = stack.start_execution(f"it-t5-{uuid.uuid4()}", {
        "videoId": video_id, "status": "UPLOADED",
        "bucket": UPLOADS_BUCKET, "key": key})
    desc = stack.wait_execution(start["executionArn"])
    assert desc["status"] == "SUCCEEDED", desc

    record = stack.get_record(video_id)
    assert record["status"] == "PROCESSED", record
    assert record.get("processedKey") == processed_key, record

    obj = stack.s3.get_object(Bucket=PROCESSED_BUCKET, Key=processed_key)
    assert obj["Body"].read() == binary_payload, (
        "processed object is not byte-identical to the fixture")

    events = stack.collect_processed_events(video_id)
    assert len(events) == 1, (
        f"expected exactly 1 video.processed, got {len(events)}")
    assert events[0].get("eventId") == event_id(video_id, "PROCESSED")


def test_t6_rerun_fails_without_regression(stack, binary_payload, video_id):
    """StartExecution again (fresh name, record already PROCESSED) ->
    execution fails at MarkProcessing -> status stays PROCESSED, no second
    event (FR-11 via ASL)."""
    stack.drain_capture_queue()
    key = stack.seed_video(video_id, binary_payload)

    first = stack.start_execution(f"it-t6-{uuid.uuid4()}", {
        "videoId": video_id, "status": "UPLOADED",
        "bucket": UPLOADS_BUCKET, "key": key})
    desc = stack.wait_execution(first["executionArn"])
    assert desc["status"] == "SUCCEEDED", desc
    first_events = stack.collect_processed_events(video_id)
    assert len(first_events) == 1, first_events

    rerun = stack.start_execution(f"it-t6-{uuid.uuid4()}", {
        "videoId": video_id, "status": "UPLOADED",
        "bucket": UPLOADS_BUCKET, "key": key})
    rerun_desc = stack.wait_execution(rerun["executionArn"])
    assert rerun_desc["status"] == "FAILED", rerun_desc
    # Failed at MarkProcessing's condition (record already PROCESSED), not
    # anywhere else — floci surfaces the DynamoDB error on the execution.
    assert "ConditionalCheckFailed" in rerun_desc.get("error", ""), (
        f"rerun failed for an unexpected reason: {rerun_desc}")

    assert stack.get_record(video_id)["status"] == "PROCESSED"
    assert stack.collect_processed_events(video_id, timeout=10) == [], (
        "rerun published a second processed event")
