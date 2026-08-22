"""ASL <-> shared-layer mirror backstop (Story 2.2, AD-4).

AD-4: the ASL's inline condition pairs MUST mirror the shared layer's
legal-transition table exactly; a transition-table change is one
coordinated ASL + shared-layer change. This test parses the ASL
definition (terraform/processing.asl.json) as real JSON and asserts it
against `shared.status.LEGAL_TRANSITIONS` — so a one-sided edit to
either side fails in CI without a live apply.

Also asserts the four-state chain order, the direct-integration
resources, and that the Lambda tasks target the right functions via the
templatefile placeholders.

Story: 2-2-processing-state-machine-event-publisher
"""

import json
import re
from pathlib import Path

import pytest

from shared import status

_TERRAFORM_DIR = Path(__file__).resolve().parents[3] / "terraform"
_ASL_PATH = _TERRAFORM_DIR / "processing.asl.json"
_TF_PATH = _TERRAFORM_DIR / "processing.tf"


@pytest.fixture(scope="module")
def asl():
    return json.loads(_ASL_PATH.read_text(encoding="utf-8"))


def _update_item_tasks(asl):
    """(state_name, params) for every dynamodb:updateItem task."""
    return {
        name: state["Parameters"]
        for name, state in asl["States"].items()
        if state["Resource"] == "arn:aws:states:::dynamodb:updateItem"
    }


def _condition_pair(params):
    """Extract the (:expected, :next) status pair from an updateItem
    task's Parameters."""
    values = params["ExpressionAttributeValues"]
    return values[":expected"]["S"], values[":next"]["S"]


# ---------------------------------------------------------------------------
# Chain structure
# ---------------------------------------------------------------------------

class TestChainStructure:
    def test_starts_at_mark_processing(self, asl):
        assert asl["StartAt"] == "MarkProcessing"

    def test_four_states_in_order(self, asl):
        states = asl["States"]
        assert set(states) == {
            "MarkProcessing", "Transcode", "MarkProcessed",
            "PublishProcessed"}
        assert states["MarkProcessing"]["Next"] == "Transcode"
        assert states["Transcode"]["Next"] == "MarkProcessed"
        assert states["MarkProcessed"]["Next"] == "PublishProcessed"
        assert states["PublishProcessed"].get("End") is True
        assert "Next" not in states["PublishProcessed"]

    def test_no_catch_or_retry(self, asl):
        """Failure semantics: any task failure fails the execution
        (FR-11 via ASL) — no Catch/Retry softening."""
        for name, state in asl["States"].items():
            assert "Catch" not in state, f"{name} has Catch"
            assert "Retry" not in state, f"{name} has Retry"

    def test_task_resources(self, asl):
        states = asl["States"]
        assert states["MarkProcessing"]["Resource"] == \
            "arn:aws:states:::dynamodb:updateItem"
        assert states["Transcode"]["Resource"] == \
            "arn:aws:states:::lambda:invoke"
        assert states["MarkProcessed"]["Resource"] == \
            "arn:aws:states:::dynamodb:updateItem"
        assert states["PublishProcessed"]["Resource"] == \
            "arn:aws:states:::lambda:invoke"


# ---------------------------------------------------------------------------
# Condition pairs mirror LEGAL_TRANSITIONS (AD-4)
# ---------------------------------------------------------------------------

class TestTransitionMirror:
    def test_every_condition_pair_is_a_legal_transition(self, asl):
        """Each updateItem's (:expected -> :next) must be an edge of the
        shared layer's table — derived from LEGAL_TRANSITIONS, not
        hardcoded, so a table change without an ASL change fails here."""
        for name, params in _update_item_tasks(asl).items():
            expected, nxt = _condition_pair(params)
            assert nxt in status.LEGAL_TRANSITIONS.get(expected, frozenset()), (
                f"{name}: {expected} -> {nxt} is not a legal transition "
                f"per shared.status.LEGAL_TRANSITIONS")

    def test_chain_covers_uploaded_processing_processed(self, asl):
        """The processing leg walks exactly UPLOADED -> PROCESSING ->
        PROCESSED, in that order."""
        pairs = [
            _condition_pair(asl["States"]["MarkProcessing"]["Parameters"]),
            _condition_pair(asl["States"]["MarkProcessed"]["Parameters"]),
        ]
        assert pairs == [
            (status.UPLOADED, status.PROCESSING),
            (status.PROCESSING, status.PROCESSED),
        ]

    def test_condition_expression_shape(self, asl):
        """The table rejects illegal transitions: ConditionExpression
        asserts the legal source state, ExpressionAttributeNames maps
        #s -> status (same encoding as shared.status.transition)."""
        for name, params in _update_item_tasks(asl).items():
            assert params["ConditionExpression"] == "#s = :expected", name
            assert params["ExpressionAttributeNames"] == {"#s": "status"}, \
                name
            assert params["TableName"] == "${table_name}", name
            assert params["Key"] == {"videoId": {"S.$": "$.videoId"}}, name

    def test_mark_processed_sets_processed_key_and_updated_at(self, asl):
        params = asl["States"]["MarkProcessed"]["Parameters"]
        values = params["ExpressionAttributeValues"]
        assert values[":pk"] == {"S.$": "$.processedKey"}
        assert values[":updatedAt"] == {"S.$": "$$.State.EnteredTime"}
        assert "processedKey = :pk" in params["UpdateExpression"]
        assert "updatedAt = :updatedAt" in params["UpdateExpression"]

    def test_mark_processing_sets_updated_at(self, asl):
        params = asl["States"]["MarkProcessing"]["Parameters"]
        values = params["ExpressionAttributeValues"]
        assert values[":updatedAt"] == {"S.$": "$$.State.EnteredTime"}


# ---------------------------------------------------------------------------
# Lambda task wiring
# ---------------------------------------------------------------------------

class TestLambdaTaskWiring:
    def test_transcode_task_targets_transcode_function(self, asl):
        task = asl["States"]["Transcode"]
        assert task["Parameters"]["FunctionName"] == "${transcode_arn}"
        # Worker contract (Story 2.1): videoId + originalKey, mapped from
        # the state-machine input (the video.uploaded detail).
        assert task["Parameters"]["Payload"] == {
            "videoId.$": "$.videoId",
            "originalKey.$": "$.key",
        }
        # The gateway wraps the Lambda result as {Payload: ...,
        # StatusCode: ...} (real-AWS shape). The ResultSelector unwraps
        # $.Payload so the ASL is identical on floci and real AWS.
        assert task["ResultSelector"] == {
            "videoId.$": "$.Payload.videoId",
            "originalKey.$": "$.Payload.originalKey",
            "processedKey.$": "$.Payload.processedKey",
            "sizeBytes.$": "$.Payload.sizeBytes",
        }
        assert task["ResultPath"] == "$"

    def test_publisher_task_targets_publisher_with_domain_payload(self,
                                                                  asl):
        """AD-4: the ASL passes the publisher only the domain payload
        (the transcode result) — no envelope fields, no bucket."""
        task = asl["States"]["PublishProcessed"]
        assert task["Parameters"]["FunctionName"] == "${publisher_arn}"
        assert task["Parameters"]["Payload.$"] == "$"

    def test_update_item_results_discarded(self, asl):
        """updateItem responses must not pollute the domain payload the
        publisher receives."""
        for name in ("MarkProcessing", "MarkProcessed"):
            assert asl["States"][name]["ResultPath"] is None, name


# ---------------------------------------------------------------------------
# Terraform wiring of the ASL file
# ---------------------------------------------------------------------------

class TestTerraformWiring:
    def test_state_machine_loads_asl_via_templatefile(self):
        tf = _TF_PATH.read_text(encoding="utf-8")
        assert re.search(
            r'resource\s+"aws_sfn_state_machine"\s+"processing"', tf)
        assert "templatefile(\"${path.module}/processing.asl.json\"" in tf
        # All three placeholders are filled by Terraform.
        for var in ("table_name", "transcode_arn", "publisher_arn"):
            assert re.search(rf"{var}\s*=", tf), f"{var} not filled"

    def test_asl_placeholders_match_templatefile_vars(self, asl):
        raw = _ASL_PATH.read_text(encoding="utf-8")
        assert "${table_name}" in raw
        assert "${transcode_arn}" in raw
        assert "${publisher_arn}" in raw
        # No unfilled or unknown placeholders beyond the three.
        assert set(re.findall(r"\$\{(\w+)\}", raw)) == {
            "table_name", "transcode_arn", "publisher_arn"}
