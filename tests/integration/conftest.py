"""Integration test suite — shared fixtures and helpers.

Drives the DEPLOYED stack through real API Gateway calls and real AWS-API
side-effect reads (S3 / DynamoDB / SQS / EventBridge / Step Functions), per
_bmad-output/test-artifacts/integration-test-plan.md. Requires a live,
applied stack on floci (localhost:4566).

Design decisions honored here (plan §2):
- D5: gateway base URL from GATEWAY_BASE_URL env, fallback `terraform output`.
- D6: binary fixture generated in-process (all 256 byte values, deterministic).
- Capture-queue hygiene (plan §4): each journey test drains the queue at start
  and asserts only on messages matching its own videoId/eventId.
"""

import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pytest
import requests
from boto3.dynamodb.conditions import Attr

ENDPOINT_URL = "http://localhost:4566"
REGION = "us-east-1"
CREDS = {"aws_access_key_id": "test", "aws_secret_access_key": "test"}

# Terraform-set resource names (config-not-code; fixed in terraform/*.tf).
METADATA_TABLE = "video-metadata"
UPLOADS_BUCKET = "video-uploads"
PROCESSED_BUCKET = "video-processed"
EVENT_BUS = "video-bus"
HISTORY_TABLE = "status-history"
SEARCH_INDEX_TABLE = "search-index"
HISTORY_QUEUE = "history-queue"
TRIGGER_QUEUE = "processing-trigger-queue"
CAPTURE_QUEUE = "smoke-capture-queue"
STATE_MACHINE_NAME = "processing-state-machine"
TRANSCODE_FUNCTION = "transcode"
REBUILD_FUNCTION = "search-rebuild"

# Frozen wire contract (lambdas/_shared/events.py:24,38-40). Re-derived here
# rather than imported so the suite stays independent of the zip package layout.
EVENT_ID_NAMESPACE = uuid.UUID("99881bbf-05eb-5ec6-8f3a-490d7496e518")
SCHEMA_VERSION = "1"
EVENT_UPLOADED = "video.uploaded"
EVENT_PROCESSED = "video.processed"

# floci cold Lambda containers are slow — generous end-to-end timeout (plan §4).
JOURNEY_TIMEOUT = 180

REPO_ROOT = Path(__file__).resolve().parents[2]


def event_id(video_id, status):
    """Deterministic eventId for (videoId, status) — mirrors shared layer."""
    return str(uuid.uuid5(EVENT_ID_NAMESPACE, f"{video_id}:{status}"))


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def poll_until(fn, timeout=JOURNEY_TIMEOUT, interval=2):
    """Call fn() until it returns a truthy value; return it. Raise on timeout."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise TimeoutError(
        f"condition not met within {timeout}s (last={last!r})")


class Stack:
    """Bundles boto3 clients + the operations the integration tests share."""

    def __init__(self):
        self.s3 = boto3.client(
            "s3", endpoint_url=ENDPOINT_URL, region_name=REGION, **CREDS)
        self.dynamodb = boto3.resource(
            "dynamodb", endpoint_url=ENDPOINT_URL, region_name=REGION, **CREDS)
        self.sqs = boto3.client(
            "sqs", endpoint_url=ENDPOINT_URL, region_name=REGION, **CREDS)
        self.events = boto3.client(
            "events", endpoint_url=ENDPOINT_URL, region_name=REGION, **CREDS)
        self.sfn = boto3.client(
            "stepfunctions", endpoint_url=ENDPOINT_URL, region_name=REGION,
            **CREDS)
        self._capture_queue_url = None
        self._state_machine_arn = None

    # --- Resource lookups ---------------------------------------------------

    @property
    def capture_queue_url(self):
        if self._capture_queue_url is None:
            self._capture_queue_url = self.sqs.get_queue_url(
                QueueName=CAPTURE_QUEUE)["QueueUrl"]
        return self._capture_queue_url

    @property
    def state_machine_arn(self):
        if self._state_machine_arn is None:
            resp = self.sfn.list_state_machines()
            for sm in resp["stateMachines"]:
                if sm["name"] == STATE_MACHINE_NAME:
                    self._state_machine_arn = sm["stateMachineArn"]
                    break
            else:
                raise RuntimeError(
                    f"state machine {STATE_MACHINE_NAME} not found")
        return self._state_machine_arn

    # --- Gateway upload -----------------------------------------------------

    def upload(self, gateway_base_url, body, title="Integration Fixture",
               filename="fixture.bin"):
        """POST multipart to the gateway upload route; return the response."""
        return requests.post(
            f"{gateway_base_url}/videos/upload",
            files={"file": (filename, body, "application/octet-stream")},
            data={"title": title},
            timeout=60)

    # --- Metadata table -----------------------------------------------------

    def get_record(self, video_id):
        resp = self.dynamodb.Table(METADATA_TABLE).get_item(
            Key={"videoId": video_id})
        return resp.get("Item")

    def wait_status(self, video_id, status, timeout=JOURNEY_TIMEOUT):
        return poll_until(
            lambda: (lambda r: r if r and r.get("status") == status else None)(
                self.get_record(video_id)),
            timeout=timeout)

    # --- SQS queue observation ----------------------------------------------

    def _queue_url(self, name):
        return self.sqs.get_queue_url(QueueName=name)["QueueUrl"]

    def wait_queue_drained(self, name, arrive_timeout=30, drain_timeout=60):
        """Wait until a message arrives on the queue, then until the queue is
        fully empty (visible + in-flight == 0, i.e. consumed and deleted).
        Makes negative assertions condition-based instead of fixed sleeps."""
        url = self._queue_url(name)

        def attrs():
            a = self.sqs.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=["ApproximateNumberOfMessages",
                                "ApproximateNumberOfMessagesNotVisible"])
            return (int(a["Attributes"]["ApproximateNumberOfMessages"]),
                    int(a["Attributes"][
                        "ApproximateNumberOfMessagesNotVisible"]))

        deadline = time.time() + arrive_timeout
        while time.time() < deadline:
            visible, inflight = attrs()
            if visible + inflight > 0:
                break
            time.sleep(1)
        else:
            raise TimeoutError(
                f"no message arrived on {name} within {arrive_timeout}s")
        deadline = time.time() + drain_timeout
        while time.time() < deadline:
            visible, inflight = attrs()
            if visible + inflight == 0:
                return
            time.sleep(1)
        raise TimeoutError(
            f"queue {name} not drained within {drain_timeout}s")

    # --- Capture queue (video.processed observation point) ------------------

    def _receive_capture(self, max_messages=100):
        envelopes = []
        while len(envelopes) < max_messages:
            resp = self.sqs.receive_message(
                QueueUrl=self.capture_queue_url, MaxNumberOfMessages=10,
                WaitTimeSeconds=0)
            messages = resp.get("Messages") or []
            if not messages:
                break
            for msg in messages:
                try:
                    envelopes.append(json.loads(msg["Body"]))
                except ValueError:
                    pass
                self.sqs.delete_message(
                    QueueUrl=self.capture_queue_url,
                    ReceiptHandle=msg["ReceiptHandle"])
        return envelopes

    def drain_capture_queue(self):
        while self._receive_capture():
            pass

    @staticmethod
    def _detail_of(envelope):
        detail = envelope.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                return None
        return detail if isinstance(detail, dict) else None

    def collect_processed_events(self, video_id, timeout=60):
        """video.processed details for this videoId arriving within the window."""
        found = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            for envelope in self._receive_capture():
                detail = self._detail_of(envelope)
                if detail and detail.get("videoId") == video_id:
                    found.append(detail)
            if found:
                # Give redeliveries/duplicates a moment to show up, then stop.
                time.sleep(3)
                for envelope in self._receive_capture():
                    detail = self._detail_of(envelope)
                    if detail and detail.get("videoId") == video_id:
                        found.append(detail)
                break
            time.sleep(1)
        return found

    # --- Event publishing ---------------------------------------------------

    def publish(self, detail_type, detail_payload):
        put_resp = self.events.put_events(Entries=[{
            "Source": "integration-test",
            "DetailType": detail_type,
            "Detail": json.dumps(detail_payload),
            "EventBusName": EVENT_BUS,
        }])
        assert not put_resp.get("FailedEntryCount"), (
            f"put_events rejected: {put_resp}")

    @staticmethod
    def uploaded_payload(video_id, bucket, key):
        detail = {"videoId": video_id, "status": "UPLOADED",
                  "bucket": bucket, "key": key}
        envelope = {"eventId": event_id(video_id, "UPLOADED"),
                    "schemaVersion": SCHEMA_VERSION, "detail": detail}
        return {**envelope, **envelope["detail"]}

    @staticmethod
    def processed_payload(video_id, bucket, original_key, processed_key,
                          eid=None):
        detail = {"videoId": video_id, "status": "PROCESSED", "bucket": bucket,
                  "originalKey": original_key, "processedKey": processed_key}
        envelope = {"eventId": eid or event_id(video_id, "PROCESSED"),
                    "schemaVersion": SCHEMA_VERSION, "detail": detail}
        return {**envelope, **envelope["detail"]}

    # --- Seeding (T5/T6/T7: isolate legs from the upload path) --------------

    def seed_video(self, video_id, body, filename="fixture.mp4"):
        """Fixture object in video-uploads + an UPLOADED metadata record."""
        key = f"{video_id}/{filename}"
        self.s3.put_object(
            Bucket=UPLOADS_BUCKET, Key=key, Body=body, ContentType="video/mp4")
        now = _now_iso()
        self.dynamodb.Table(METADATA_TABLE).put_item(Item={
            "videoId": video_id, "title": filename, "status": "UPLOADED",
            "bucket": UPLOADS_BUCKET, "originalKey": key,
            "createdAt": now, "updatedAt": now,
            "contentType": "video/mp4", "sizeBytes": len(body),
        })
        return key

    # --- Step Functions -----------------------------------------------------

    def start_execution(self, name, asl_input):
        return self.sfn.start_execution(
            stateMachineArn=self.state_machine_arn,
            name=name, input=json.dumps(asl_input))

    def wait_execution(self, execution_arn, timeout=JOURNEY_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            desc = self.sfn.describe_execution(executionArn=execution_arn)
            if desc["status"] in ("SUCCEEDED", "FAILED", "TIMED_OUT",
                                  "ABORTED"):
                return desc
            time.sleep(2)
        raise TimeoutError(
            f"execution {execution_arn} still running after {timeout}s")

    def find_execution_by_name(self, name):
        next_token = None
        while True:
            kwargs = {"stateMachineArn": self.state_machine_arn}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = self.sfn.list_executions(**kwargs)
            for ex in resp["executions"]:
                if ex["name"] == name:
                    return ex
            next_token = resp.get("nextToken")
            if not next_token:
                return None

    # --- Transcode (ad-hoc invoke through floci's Lambda REST API) ----------

    def invoke_transcode(self, payload):
        resp = requests.post(
            f"{ENDPOINT_URL}/2015-03-31/functions/{TRANSCODE_FUNCTION}"
            "/invocations",
            json=payload, timeout=60)
        assert resp.status_code == 200, (
            f"transcode invoke HTTP {resp.status_code}: {resp.text}")
        body = resp.json()
        # floci may wrap the result as {Payload, StatusCode}; unwrap if so.
        if isinstance(body, dict) and "Payload" in body:
            body = body["Payload"]
            if isinstance(body, str):
                body = json.loads(body)
        if isinstance(body, dict) and body.get("errorType"):
            raise RuntimeError(f"transcode invocation failed: {body}")
        return body

    def invoke_search_rebuild(self, payload):
        """Direct invoke of the DEPLOYED search-rebuild through floci's
        Lambda REST API (Story 4.3 — ad-hoc admin, never setup)."""
        resp = requests.post(
            f"{ENDPOINT_URL}/2015-03-31/functions/{REBUILD_FUNCTION}"
            "/invocations",
            json=payload, timeout=60)
        assert resp.status_code == 200, (
            f"search-rebuild invoke HTTP {resp.status_code}: {resp.text}")
        body = resp.json()
        if isinstance(body, dict) and "Payload" in body:
            body = body["Payload"]
            if isinstance(body, str):
                body = json.loads(body)
        if isinstance(body, dict) and (
                "errorType" in body or "errorMessage" in body):
            raise RuntimeError(
                f"search-rebuild invocation failed: {body}")
        return body

    # --- status-history -----------------------------------------------------

    def history_entries(self, video_id):
        resp = self.dynamodb.Table(HISTORY_TABLE).scan(
            FilterExpression=Attr("videoId").eq(video_id))
        return resp.get("Items", [])

    def search_entries(self, video_id):
        """Direct-table oracle for the search-index (Story 4.2)."""
        resp = self.dynamodb.Table(SEARCH_INDEX_TABLE).scan(
            FilterExpression=Attr("videoId").eq(video_id))
        return resp.get("Items", [])

    def clear_search_index(self):
        """Ad-hoc admin clear of the whole derived index (Story 4.3's
        disposable proof: the rebuild must be able to bring it back).
        LOAD-BEARING SETUP, not best-effort cleanup: a partial or failed
        clear would fake a rebuild success, so this paginates the scan
        and lets ANY error raise."""
        table = self.dynamodb.Table(SEARCH_INDEX_TABLE)
        kwargs = {}
        while True:
            resp = table.scan(**kwargs)
            for item in resp.get("Items", []):
                table.delete_item(Key={"videoId": item["videoId"]})
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                return
            kwargs = {"ExclusiveStartKey": last_key}

    # --- Cleanup ------------------------------------------------------------

    def cleanup_video(self, video_id):
        try:
            self.dynamodb.Table(METADATA_TABLE).delete_item(
                Key={"videoId": video_id})
        except Exception:  # noqa: BLE001 - cleanup must never fail the run
            pass
        try:
            table = self.dynamodb.Table(HISTORY_TABLE)
            for item in self.history_entries(video_id):
                table.delete_item(Key={"eventId": item["eventId"]})
        except Exception:  # noqa: BLE001
            pass
        try:
            table = self.dynamodb.Table(SEARCH_INDEX_TABLE)
            for item in self.search_entries(video_id):
                table.delete_item(Key={"videoId": item["videoId"]})
        except Exception:  # noqa: BLE001
            pass
        for bucket, prefix in (
                (UPLOADS_BUCKET, f"{video_id}/"),
                (PROCESSED_BUCKET, f"processed/{video_id}/")):
            try:
                resp = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
                for obj in resp.get("Contents", []):
                    self.s3.delete_object(Bucket=bucket, Key=obj["Key"])
            except Exception:  # noqa: BLE001
                pass


@pytest.fixture(scope="session")
def stack():
    return Stack()


@pytest.fixture(scope="session")
def gateway_base_url():
    url = os.environ.get("GATEWAY_BASE_URL")
    if url:
        return url.rstrip("/")
    out = subprocess.run(
        ["terraform", "output", "-raw", "gateway_base_url"],
        cwd=REPO_ROOT / "terraform",
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(
            "terraform output gateway_base_url failed (is the stack "
            f"applied?): {out.stderr.strip()}")
    return out.stdout.strip().rstrip("/")


@pytest.fixture(scope="session")
def binary_payload():
    # D6: all 256 byte values, deterministic, no fixture file.
    return bytes(range(256)) * 4


@pytest.fixture()
def video_id(stack):
    vid = str(uuid.uuid4())
    yield vid
    stack.cleanup_video(vid)
