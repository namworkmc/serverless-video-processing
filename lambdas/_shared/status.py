"""Status state machine enforced by DynamoDB conditional writes (FR-11/12/13).

This module is the ONLY place that knows the legal-transition table.
Table handles are boto3 resource Tables (shared.clients.dynamodb_table),
so items use plain Python values.

- create:     PutItem with ConditionExpression attribute_not_exists(videoId)
              -> idempotent by videoId: a retry returns the existing record
              unchanged (FR-12).
- transition: UpdateItem with ConditionExpression #s = :expected
              -> illegal transitions and transitions out of terminal statuses
              raise ConflictError (the table rejects them); re-asserting the
              current status is an idempotent no-op (no write).
- get:        unknown videoId raises NotFoundError, never silent success
              (FR-13).
"""

from datetime import datetime, timezone

from shared.errors import (ConflictError, MalformedInputError, NotFoundError,
                           is_conditional_check_failed)

UPLOADED = "UPLOADED"
PROCESSING = "PROCESSING"
PROCESSED = "PROCESSED"
FAILED = "FAILED"

STATUSES = (UPLOADED, PROCESSING, PROCESSED, FAILED)
TERMINAL_STATUSES = frozenset({PROCESSED, FAILED})

# The legal-transition table. Epic 2's ASL inline condition pairs MUST mirror
# this exactly; a change here is one coordinated ASL + shared-layer change.
LEGAL_TRANSITIONS = {
    UPLOADED: frozenset({PROCESSING}),
    PROCESSING: frozenset({PROCESSED, FAILED}),
    PROCESSED: frozenset(),
    FAILED: frozenset(),
}

# Inverted view: each target status -> its unique legal source. This is the
# :expected value in the ConditionExpression, which is what makes the TABLE
# the enforcement point: requesting PROCESSED asserts #s = PROCESSING, so a
# record still at UPLOADED fails the condition (illegal transition rejected
# by the table, not by an if-statement). Every target currently has exactly
# one legal source; if one ever gains several, this becomes a set + IN.
_EXPECTED_SOURCE = {
    target: source
    for source, targets in LEGAL_TRANSITIONS.items()
    for target in targets
}
# Enforce the single-source invariant the ConditionExpression design relies
# on: if a target ever gains two legal sources, :expected can no longer be a
# scalar and this must become a set + IN — fail loudly at import, not silently
# keep the last source.
assert len(_EXPECTED_SOURCE) == sum(
    len(targets) for targets in LEGAL_TRANSITIONS.values()), (
    "a target status has multiple legal sources; the :expected scalar "
    "ConditionExpression design no longer holds")

# Optional attributes a caller may set alongside a transition (e.g. the
# processing state machine records processedKey / failureReason as it goes).
TRANSITION_EXTRA_ATTRIBUTES = frozenset(
    {"processedKey", "failureReason", "durationSeconds"}
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def create_record(table, video_id, title, bucket, original_key,
                  content_type=None, size_bytes=None):
    """Create the metadata record for a freshly uploaded video (status
    UPLOADED). Idempotent by videoId: if the record already exists, the
    existing record is returned unchanged (FR-12)."""
    if not video_id:
        raise MalformedInputError("videoId is required")
    if not title:
        raise MalformedInputError("title is required")
    if not bucket:
        raise MalformedInputError("bucket is required")
    if not original_key:
        raise MalformedInputError("originalKey is required")
    if size_bytes is not None:
        try:
            size_bytes = int(size_bytes)
        except (TypeError, ValueError):
            raise MalformedInputError(
                f"sizeBytes must be an integer, got: {size_bytes!r}")
    now = _now_iso()
    item = {
        "videoId": video_id,
        "title": title,
        "status": UPLOADED,
        "bucket": bucket,
        "originalKey": original_key,
        "createdAt": now,
        "updatedAt": now,
    }
    if content_type:
        item["contentType"] = content_type
    if size_bytes is not None:
        item["sizeBytes"] = size_bytes
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(videoId)",
        )
    except Exception as exc:  # noqa: BLE001 - inspect type name
        if is_conditional_check_failed(exc):
            return get_record(table, video_id)
        raise
    return dict(item)


def get_record(table, video_id):
    """Fetch one record; unknown videoId raises NotFoundError (FR-13)."""
    if not video_id:
        raise MalformedInputError("videoId is required")
    resp = table.get_item(Key={"videoId": video_id})
    item = resp.get("Item")
    if not item:
        raise NotFoundError(f"video not found: {video_id}")
    return dict(item)


def transition(table, video_id, to_status, extra_attributes=None):
    """Move a record to to_status via a conditional write.

    - unknown videoId            -> NotFoundError
    - to_status == current       -> idempotent success, no write
    - legal transition           -> UpdateItem with #s = :expected succeeds,
                                    where :expected is to_status's unique
                                    legal source (_EXPECTED_SOURCE)
    - illegal / out of terminal  -> ConflictError (the table rejects it:
                                    the condition asserts the legal source
                                    state, which the record does not have)
    """
    if to_status not in STATUSES:
        raise MalformedInputError(f"unknown status: {to_status}")
    current = get_record(table, video_id)
    if current["status"] == to_status:
        return current  # idempotent re-assertion, no write side effect

    expected = _EXPECTED_SOURCE.get(to_status)
    if expected is None:
        # UPLOADED is the initial state — only create_record mints it.
        raise ConflictError(
            f"status conflict for {video_id}: {to_status} cannot be "
            f"reached by transition")

    names = {"#s": "status"}
    values = {":expected": expected, ":next": to_status,
              ":updatedAt": _now_iso()}
    sets = ["#s = :next", "updatedAt = :updatedAt"]

    extras = {}
    if extra_attributes:
        for key, value in extra_attributes.items():
            if key not in TRANSITION_EXTRA_ATTRIBUTES:
                raise MalformedInputError(
                    f"attribute not settable on transition: {key}")
            if value is None:
                continue
            names[f"#extra_{key}"] = key
            placeholder = f":extra_{key}"
            values[placeholder] = value
            sets.append(f"#extra_{key} = {placeholder}")
            extras[key] = value

    try:
        resp = table.update_item(
            Key={"videoId": video_id},
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="#s = :expected",
            ReturnValues="ALL_NEW",
        )
    except Exception as exc:  # noqa: BLE001 - inspect type name
        if is_conditional_check_failed(exc):
            # Lost a race or the record changed underneath us: conflict.
            try:
                fresh = get_record(table, video_id)
            except NotFoundError:
                raise  # record vanished between get and update
            raise ConflictError(
                f"status conflict for {video_id}: transition to {to_status} "
                f"requires status {expected}, record is "
                f"{fresh['status']}") from exc
        raise
    # Return the table's actual post-update item, not a local reconstruction.
    return dict(resp["Attributes"])
