"""T1–T2 — upload journey through the gateway (Story 1.3, mirrors Bruno)."""

import requests

from conftest import UPLOADS_BUCKET


def test_t1_happy_path_binary_round_trip(
        stack, gateway_base_url, binary_payload):
    """POST multipart (binary fixture + title) -> 200 videoId; object in
    video-uploads byte-identical to the upload (the epic-1 F1 gap); record
    UPLOADED with timestamps."""
    resp = stack.upload(gateway_base_url, binary_payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "videoId" in body, body
    vid = body["videoId"]
    try:
        # Object exists under a key containing the videoId, byte-identical.
        listing = stack.s3.list_objects_v2(Bucket=UPLOADS_BUCKET,
                                           Prefix=f"{vid}/")
        contents = listing.get("Contents", [])
        assert len(contents) == 1, f"expected 1 object, got {contents}"
        obj = stack.s3.get_object(Bucket=UPLOADS_BUCKET,
                                  Key=contents[0]["Key"])
        assert obj["Body"].read() == binary_payload, (
            "uploaded object is not byte-identical to the fixture")

        # Metadata record UPLOADED with timestamps.
        record = stack.get_record(vid)
        assert record is not None, "video-metadata record missing"
        assert record["status"] == "UPLOADED", record
        assert record.get("createdAt"), record
        assert record.get("updatedAt"), record
    finally:
        stack.cleanup_video(vid)


def test_t2_malformed_missing_file_part(stack, gateway_base_url):
    """Multipart without a file part -> 400 {"error": ...} passed through
    unchanged (NFR-3, FR-21)."""
    # files={(None, value)} sends a multipart form field with no file part.
    resp = requests.post(
        f"{gateway_base_url}/videos/upload",
        files={"title": (None, "no file here")},
        timeout=60)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "error" in body, body
    assert isinstance(body["error"], str) and body["error"], body
