"""AC — admin-only search-index rebuild (Story 4.3).

Pipeline coverage for the rebuild wiring (verification-gap closure, same
rationale as Story 4.2's review run 1): a renamed function, missing env
var, or dropped zip module would otherwise ship green-broken — this file
exercises the DEPLOYED function end-to-end in ci-local stage 5.
"""

import uuid

import requests

from conftest import poll_until

RESULT_KEYS = {"videoId", "title", "processedKey", "indexedAt"}


def test_search_rebuild_repopulates_cleared_index(
        stack, gateway_base_url, binary_payload):
    """Upload via gateway -> PROCESSED -> consumer auto-indexes (L3
    baseline) -> ad-hoc clear of search-index -> gateway search returns
    200 [] -> DIRECT invoke of the deployed search-rebuild (floci Lambda
    REST — admin, never setup) -> index repopulated with PROCESSED-only
    entries and the gateway surfaces the rebuilt video again; a second
    invoke overwrites the same PK — no duplicates (FR-19)."""
    title = f"Rebuild Journey Fixture {uuid.uuid4().hex[:8]}"
    resp = stack.upload(gateway_base_url, binary_payload, title=title)
    assert resp.status_code == 200, resp.text
    vid = resp.json()["videoId"]
    try:
        # Baseline: the CONSUMER path indexed it.
        stack.wait_status(vid, "PROCESSED")
        poll_until(lambda: stack.search_entries(vid) or None)

        # Disposable proof: clear the derived index ad-hoc.
        stack.clear_search_index()
        r = requests.get(
            f"{gateway_base_url}/videos/search", params={"title": title},
            timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["results"] == [], r.json()

        # Rebuild via direct invoke of the DEPLOYED function. Counts are
        # delta-tolerant: the shared metadata/index tables may hold
        # foreign rows from other tests, so only THIS video's outcome is
        # asserted (skipped must not be pinned to 0 against live data).
        summary = stack.invoke_search_rebuild({})
        assert set(summary) == {"scanned", "indexed", "skipped"}, summary
        assert summary["indexed"] >= 1, summary

        poll_until(lambda: stack.search_entries(vid) or None)
        r = requests.get(
            f"{gateway_base_url}/videos/search", params={"title": title},
            timeout=30)
        assert r.status_code == 200, r.text
        matches = [e for e in r.json()["results"] if e["videoId"] == vid]
        assert len(matches) == 1, r.json()
        assert set(matches[0]) == RESULT_KEYS, matches[0]
        assert matches[0]["title"] == title, matches[0]
        # Fidelity, not just key-set: the rebuilt processedKey must be
        # the exact deterministic string (upload fixture.bin ->
        # transcode's processed/{videoId}/{basename}).
        assert matches[0]["processedKey"] == \
            f"processed/{vid}/fixture.bin", matches[0]

        # Idempotent re-invoke: PK is the dedupe, count stays 1. Reads
        # after the invoke go through poll_until — an immediate bare
        # scan can race the eventual write.
        before = stack.search_entries(vid)
        assert len(before) == 1, before
        summary2 = stack.invoke_search_rebuild({})
        assert summary2["indexed"] >= 1, summary2
        after_entries = poll_until(
            lambda: (lambda es: es if len(es) == len(before) else None)(
                stack.search_entries(vid)))
        assert [e["videoId"] for e in after_entries] == [vid]
    finally:
        stack.cleanup_video(vid)
