"""AC — title search through the gateway (Story 4.2)."""

import uuid

import requests

from conftest import poll_until

RESULT_KEYS = {"videoId", "title", "processedKey", "indexedAt"}


def _index_search_fixture(stack, gateway_base_url, binary_payload):
    """Upload with a fixed title -> PROCESSED -> search-index entry present
    (direct-table oracle seeded). Returns (videoId, oracle_row)."""
    resp = stack.upload(
        gateway_base_url, binary_payload, title="Search Integration Fixture")
    assert resp.status_code == 200, resp.text
    vid = resp.json()["videoId"]
    stack.wait_status(vid, "PROCESSED")
    poll_until(lambda: stack.search_entries(vid) or None)
    oracle = next(e for e in stack.search_entries(vid)
                  if e["videoId"] == vid)
    return vid, oracle


def test_search_query_returns_uploaded_video_via_gateway(
        stack, gateway_base_url, binary_payload):
    """Upload via gateway -> PROCESSED -> indexed (Story 4.1) ->
    GET /videos/search?title=Integration%20Fixture via the gateway returns
    200 whose single matching result equals the direct-table oracle row
    projected to exactly {videoId, title, processedKey, indexedAt}
    (FR-18, FR-21)."""
    vid, oracle = _index_search_fixture(stack, gateway_base_url, binary_payload)
    try:
        r = requests.get(
            f"{gateway_base_url}/videos/search",
            params={"title": "Integration Fixture"},
            timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["title"] == "Integration Fixture", body
        matches = [e for e in body["results"] if e["videoId"] == vid]
        assert len(matches) == 1, body
        assert set(matches[0]) == RESULT_KEYS, matches[0]
        assert matches[0] == {
            "videoId": oracle["videoId"],
            "title": oracle["title"],
            "processedKey": oracle["processedKey"],
            "indexedAt": oracle["indexedAt"],
        }, matches[0]
    finally:
        stack.cleanup_video(vid)


def test_search_query_true_substring_at_gateway(
        stack, gateway_base_url, binary_payload):
    """Query only a PARTIAL needle of the title ("gration Fix" ⊂ "Search
    Integration Fixture") -> 200 and the video is in results — pins
    contains() substring semantics against the DEPLOYED function, not
    just unit fakes (I/O matrix row 2)."""
    vid, _ = _index_search_fixture(stack, gateway_base_url, binary_payload)
    try:
        r = requests.get(
            f"{gateway_base_url}/videos/search",
            params={"title": "gration Fix"},
            timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert any(e["videoId"] == vid for e in body["results"]), body
    finally:
        stack.cleanup_video(vid)


def test_search_query_no_match_200_empty(gateway_base_url):
    """Unknown substring -> 200 {"title": <echo>, "results": []} — NOT an
    error — through the DEPLOYED route (I/O matrix row 3)."""
    needle = f"zzz-no-such-title-{uuid.uuid4()}"
    r = requests.get(
        f"{gateway_base_url}/videos/search", params={"title": needle},
        timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["results"] == [], body
    assert body["title"] == needle, body


def test_search_query_missing_title_400(gateway_base_url):
    """No query string at all -> the gateway passes the handler's 400 +
    {"error": ...} through unchanged; the body's ONLY key is "error"
    (I/O matrix row 5, NFR-3 passthrough)."""
    r = requests.get(f"{gateway_base_url}/videos/search", timeout=30)
    assert r.status_code == 400, r.text
    body = r.json()
    assert set(body) == {"error"}, body
    assert body["error"], body
