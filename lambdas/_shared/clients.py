"""Boto3 client factories — config-not-code (NFR-4).

The endpoint comes from AWS_ENDPOINT_URL (set by Terraform; resolves to
http://host.docker.internal:4566 inside floci's Lambda containers), the
region from AWS_REGION / AWS_DEFAULT_REGION, and credentials from
AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (dummy values in the lab).
Resource NAMES (tables, buckets, buses) are never typed here — callers pass
them in from their own Terraform-set env vars.

boto3 availability in the floci runtime image: CONFIRMED by the Story 1.2
smoke run (`boto3_available: true`). The stdlib/urllib fallback was not
needed; see lambdas/README.md.
"""

import os

try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on runtime image
    boto3 = None
    BOTO3_AVAILABLE = False

# Per-service cache: warm invocations reuse constructed clients instead of
# paying construction cost on every call.
_client_cache = {}
_resource_cache = {}


def _endpoint_url():
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    if not endpoint:
        raise RuntimeError(
            "AWS_ENDPOINT_URL is not set — Terraform must provide it "
            "(config-not-code, NFR-4)")
    return endpoint


def _region():
    return os.environ.get("AWS_REGION") or os.environ.get(
        "AWS_DEFAULT_REGION", "us-east-1")


def _credentials():
    return {
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        "aws_secret_access_key": os.environ.get(
            "AWS_SECRET_ACCESS_KEY", "test"),
    }


def _require_boto3():
    if not BOTO3_AVAILABLE:
        raise RuntimeError(
            "boto3 is not available in this runtime; wire the stdlib/urllib "
            "fallback in shared.clients (see lambdas/README.md)")


def _client(service):
    _require_boto3()
    if service not in _client_cache:
        _client_cache[service] = boto3.client(
            service,
            endpoint_url=_endpoint_url(),
            region_name=_region(),
            **_credentials(),
        )
    return _client_cache[service]


def _resource(service):
    _require_boto3()
    if service not in _resource_cache:
        _resource_cache[service] = boto3.resource(
            service,
            endpoint_url=_endpoint_url(),
            region_name=_region(),
            **_credentials(),
        )
    return _resource_cache[service]


def dynamodb_resource():
    return _resource("dynamodb")


def dynamodb_table(table_name):
    """Table handle for the shared status module; name comes from the
    caller's env (e.g. TABLE_NAME), never hardcoded."""
    return dynamodb_resource().Table(table_name)


def s3_client():
    return _client("s3")


def events_client():
    return _client("events")


def states_client():
    return _client("stepfunctions")


def sqs_client():
    return _client("sqs")


def lambda_client():
    return _client("lambda")
