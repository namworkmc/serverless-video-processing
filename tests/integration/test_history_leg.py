"""T8–T10 — history leg (Story 3.1)."""

import time
import uuid

from conftest import (EVENT_PROCESSED, PROCESSED_BUCKET, event_id,
                      poll_until)


def test_t8_history_entry_written(stack, gateway_base_url, binary_payload):
    """Upload via gateway -> exactly one status-history entry
    {eventId, videoId, status: PROCESSED, timestamp} with the deterministic
    eventId (FR-14)."""
    resp = stack.upload(gateway_base_url, binary_payload)
    assert resp.status_code == 200, resp.text
    vid = resp.json()["videoId"]
    try:
        expected_event_id = event_id(vid, "PROCESSED")
        entries = poll_until(
            lambda: stack.history_entries(vid) or None, timeout=180)
        # Give duplicates a moment to show up, then assert the final state.
        time.sleep(5)
        entries = stack.history_entries(vid)
        assert len(entries) == 1, f"expected 1 history entry, got {entries}"
        entry = entries[0]
        assert entry["eventId"] == expected_event_id, entry
        assert entry["videoId"] == vid, entry
        assert entry["status"] == "PROCESSED", entry
        assert entry.get("timestamp"), entry
    finally:
        stack.cleanup_video(vid)


def test_t9_duplicate_processed_event_deduped(
        stack, gateway_base_url, binary_payload):
    """Republish video.processed -> still exactly one entry for that eventId
    (NFR-1)."""
    resp = stack.upload(gateway_base_url, binary_payload)
    assert resp.status_code == 200, resp.text
    vid = resp.json()["videoId"]
    try:
        poll_until(lambda: stack.history_entries(vid) or None, timeout=180)
        record = stack.get_record(vid)

        # Republish with the SAME deterministic eventId the publisher used.
        stack.publish(EVENT_PROCESSED, stack.processed_payload(
            vid, PROCESSED_BUCKET, record["originalKey"],
            record.get("processedKey", f"processed/{vid}/fixture.bin")))

        time.sleep(15)
        entries = stack.history_entries(vid)
        assert len(entries) == 1, (
            f"duplicate processed event created a second entry: {entries}")
    finally:
        stack.cleanup_video(vid)


def test_t10_unknown_video_id_dropped(stack):
    """Publish video.processed with fabricated eventId + unknown videoId ->
    no table entry, message acked (FR-15)."""
    unknown_vid = f"it-unknown-{uuid.uuid4()}"
    stack.publish(EVENT_PROCESSED, stack.processed_payload(
        unknown_vid, PROCESSED_BUCKET, f"{unknown_vid}/fixture.mp4",
        f"processed/{unknown_vid}/fixture.mp4",
        eid=f"it-fabricated-{uuid.uuid4()}"))

    time.sleep(15)
    assert stack.history_entries(unknown_vid) == [], (
        "poison event produced a history entry")
