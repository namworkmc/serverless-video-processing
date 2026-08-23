"""T7 — ad-hoc transcode invoke through floci's Lambda REST API (Story 2.1)."""

from conftest import PROCESSED_BUCKET


def test_t7_ad_hoc_transcode_invoke(stack, binary_payload, video_id):
    """Seed fixture object + record -> invoke the deployed transcode zip with
    {videoId, originalKey} -> processed object exists -> record still
    UPLOADED -> no event published (FR-6, AD-4)."""
    stack.drain_capture_queue()
    key = stack.seed_video(video_id, binary_payload)
    processed_key = f"processed/{video_id}/fixture.mp4"

    result = stack.invoke_transcode(
        {"videoId": video_id, "originalKey": key})
    assert result.get("videoId") == video_id, result
    assert result.get("processedKey") == processed_key, result
    assert result.get("sizeBytes") == len(binary_payload), result

    obj = stack.s3.get_object(Bucket=PROCESSED_BUCKET, Key=processed_key)
    assert obj["Body"].read() == binary_payload, (
        "processed object is not byte-identical to the fixture")

    # Pure worker (AD-4): no status writes, no events.
    assert stack.get_record(video_id)["status"] == "UPLOADED"
    assert stack.collect_processed_events(video_id, timeout=10) == [], (
        "transcode published an event")
