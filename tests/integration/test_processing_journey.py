"""T3–T4 — end-to-end auto-processing and redelivery no-op (Stories 2.2, 2.3)."""

from conftest import (EVENT_UPLOADED, PROCESSED_BUCKET, TRIGGER_QUEUE,
                      UPLOADS_BUCKET, event_id)


def test_t3_end_to_end_auto_processing(
        stack, gateway_base_url, binary_payload):
    """Upload via gateway -> record walks to PROCESSED -> processed object
    exists -> exactly one video.processed with the deterministic eventId ->
    SFN execution eb-{uploaded-eventId} exists."""
    stack.drain_capture_queue()
    resp = stack.upload(gateway_base_url, binary_payload)
    assert resp.status_code == 200, resp.text
    vid = resp.json()["videoId"]
    try:
        record = stack.wait_status(vid, "PROCESSED")
        assert record.get("processedKey"), record

        obj = stack.s3.get_object(
            Bucket=PROCESSED_BUCKET, Key=record["processedKey"])
        assert obj["Body"].read() == binary_payload, (
            "processed object is not byte-identical to the fixture")

        events = stack.collect_processed_events(vid)
        assert len(events) == 1, (
            f"expected exactly 1 video.processed, got {len(events)}")
        assert events[0].get("eventId") == event_id(vid, "PROCESSED"), (
            f"eventId mismatch: {events[0].get('eventId')}")

        execution = stack.find_execution_by_name(
            f"eb-{event_id(vid, 'UPLOADED')}")
        assert execution is not None, (
            "no SFN execution named eb-{uploaded-eventId}")
    finally:
        stack.cleanup_video(vid)


def test_t4_redelivered_uploaded_event_is_no_op(
        stack, gateway_base_url, binary_payload):
    """Republish the same video.uploaded -> still exactly one execution,
    status still PROCESSED, no second processed event (FR-9)."""
    stack.drain_capture_queue()
    resp = stack.upload(gateway_base_url, binary_payload)
    assert resp.status_code == 200, resp.text
    vid = resp.json()["videoId"]
    try:
        stack.wait_status(vid, "PROCESSED")
        # Let the first processed event land and be counted, then drain it
        # so the republish window counts only new arrivals.
        first = stack.collect_processed_events(vid)
        assert len(first) == 1, (
            f"expected 1 video.processed before republish, got {len(first)}")

        key = stack.get_record(vid)["originalKey"]
        stack.publish(
            EVENT_UPLOADED,
            stack.uploaded_payload(vid, UPLOADS_BUCKET, key))

        # The shim acks ExecutionAlreadyExists; nothing new may happen.
        # Wait for the redelivered message to be consumed, then assert the
        # steady state.
        stack.wait_queue_drained(TRIGGER_QUEUE)
        assert stack.get_record(vid)["status"] == "PROCESSED"
        assert stack.collect_processed_events(vid, timeout=10) == [], (
            "republish produced a second processed event")
        executions = [
            ex for ex in _all_executions(stack)
            if ex["name"] == f"eb-{event_id(vid, 'UPLOADED')}"]
        assert len(executions) == 1, (
            f"expected exactly 1 execution, got {len(executions)}")
    finally:
        stack.cleanup_video(vid)


def _all_executions(stack):
    executions = []
    next_token = None
    while True:
        kwargs = {"stateMachineArn": stack.state_machine_arn}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = stack.sfn.list_executions(**kwargs)
        executions.extend(resp["executions"])
        next_token = resp.get("nextToken")
        if not next_token:
            return executions
