"""FR-19 structural proof for the search-rebuild wiring (Story 4.3,
review run 1 P3 — closes the automated-proof verification gap).

Pure pytest over the Terraform SOURCE TEXT: no shared import, no live
infra, no boto3. Two guarantees:

(a) `terraform/search-rebuild.tf` declares NONE of the client-facing /
    event-driven resource types — gateway integration/route/permission,
    SQS queue, EventBridge rule/target, Lambda event-source mapping.
    Resource BLOCKS are parsed (comment lines stripped first), so a
    comment that merely MENTIONS a forbidden type does not fail.

(b) No OTHER terraform/*.tf file references "search-rebuild" outside
    comments — proving no route/rule/queue elsewhere wires the function
    in. The admin-only constraint holds by structural absence, checked
    on every unit-test run instead of by eyeball.

Skips cleanly if the terraform dir is absent (defensive only).
"""

import re
from pathlib import Path

import pytest

_TERRAFORM_DIR = Path(__file__).resolve().parents[3] / "terraform"
REBUILD_FILE = _TERRAFORM_DIR / "search-rebuild.tf"

FORBIDDEN_RESOURCE_TYPES = (
    "aws_apigatewayv2_integration",
    "aws_apigatewayv2_route",
    "aws_lambda_permission",
    "aws_sqs_queue",
    "aws_cloudwatch_event_rule",
    "aws_cloudwatch_event_target",
    "aws_lambda_event_source_mapping",
)

# A resource block's type: `resource "<type>" "<name>" {` — anchored to
# the start of a line (comments already stripped), so prose mentioning a
# type mid-sentence cannot match.
_RESOURCE_BLOCK = re.compile(r'^\s*resource\s+"([^"]+)"', re.MULTILINE)


def _strip_hcl_comments(text):
    """Remove HCL comments (# and // to end of line) while respecting
    double-quoted strings, so `"arn:...#fragment"`-style content inside
    quotes survives and commented-out resources disappear."""
    stripped_lines = []
    for line in text.splitlines():
        out = []
        in_string = False
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == '"' and (i == 0 or line[i - 1] != "\\"):
                in_string = not in_string
                out.append(ch)
            elif not in_string and (
                    ch == "#" or line.startswith("//", i)):
                break  # comment till end of line
            else:
                out.append(ch)
            i += 1
        stripped_lines.append("".join(out))
    return "\n".join(stripped_lines)


def _declared_resource_types(tf_text):
    return _RESOURCE_BLOCK.findall(_strip_hcl_comments(tf_text))


@pytest.fixture(scope="module")
def terraform_dir():
    if not _TERRAFORM_DIR.is_dir():
        pytest.skip("terraform dir absent (defensive skip)")
    return _TERRAFORM_DIR


class TestSearchRebuildIsAdminOnly:
    def test_rebuild_file_declares_no_client_or_event_surface(
            self, terraform_dir):
        assert REBUILD_FILE.is_file(), f"{REBUILD_FILE} missing"
        declared = _declared_resource_types(
            REBUILD_FILE.read_text(encoding="utf-8"))
        offenders = [
            t for t in declared if t in FORBIDDEN_RESOURCE_TYPES]
        assert offenders == [], (
            f"FR-19 violation: search-rebuild.tf declares forbidden "
            f"resource type(s) {offenders} — the rebuild must be "
            "direct-invoke-only")

    def test_no_other_tf_file_references_the_function(
            self, terraform_dir):
        others = sorted(p for p in terraform_dir.glob("*.tf")
                        if p != REBUILD_FILE)
        assert others, "no other .tf files found — glob failed?"
        for path in others:
            body = _strip_hcl_comments(
                path.read_text(encoding="utf-8"))
            assert "search-rebuild" not in body, (
                f"FR-19 violation: {path.name} references "
                "'search-rebuild' outside comments — no other file may "
                "wire the function into a route/rule/queue/mapping")

    def test_rebuild_file_still_declares_the_function_itself(
            self, terraform_dir):
        """Guard against the absence checks passing vacuously (e.g. the
        file emptied out): the lambda resource must be there."""
        declared = _declared_resource_types(
            REBUILD_FILE.read_text(encoding="utf-8"))
        assert "aws_lambda_function" in declared
        assert "aws_iam_role" in declared
        assert "aws_iam_role_policy" in declared
