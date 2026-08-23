"""AC2/AC3 — history query through the gateway (Story 3.2)."""

import uuid

import requests

from conftest import poll_until


def test_history_query_returns_entries_via_gateway(
        stack, gateway_base_url, binary_payload):
    """Upload via gateway -> PROCESSED -> consumer records -> GET
    /videos/{videoId}/history via the gateway returns 200 with entries
    matching the direct-table oracle, sorted by timestamp (FR-16, FR-21)."""
    resp = stack.upload(gateway_base_url, binary_payload)
    assert resp.status_code == 200, resp.text
    vid = resp.json()["videoId"]
    try:
        poll_until(lambda: stack.history_entries(vid) or None, timeout=180)
        oracle = sorted(stack.history_entries(vid),
                        key=lambda e: e["timestamp"])
        r = requests.get(f"{gateway_base_url}/videos/{vid}/history",
                         timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["videoId"] == vid, body
        assert body["entries"] == [
            {"status": e["status"], "eventId": e["eventId"],
             "timestamp": e["timestamp"]}
            for e in oracle
        ], body
    finally:
        stack.cleanup_video(vid)


def test_history_query_unknown_video_id_404(gateway_base_url):
    """Unknown videoId -> the gateway passes the handler's 404 +
    {"error": ...} body through unchanged; the error names the requested
    videoId, which also proves pathParameters delivery (FR-13, FR-21,
    NFR-3)."""
    unknown_vid = f"it-unknown-{uuid.uuid4()}"
    r = requests.get(f"{gateway_base_url}/videos/{unknown_vid}/history",
                     timeout=30)
    assert r.status_code == 404, r.text
    body = r.json()
    assert body.get("error"), body
    assert unknown_vid in body["error"], body
