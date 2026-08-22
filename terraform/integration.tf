# Integration test infrastructure (formerly smoke.tf — the smoke Lambda,
# its role, and its zip were retired when CI stage 5 switched to the
# tests/integration/ pytest suite; see
# _bmad-output/test-artifacts/integration-test-plan.md).
#
# Declares the video-metadata table (the shared layer's enforcement target;
# Story 1.3 REUSES this table, it does not redeclare it) and the capture
# queue the integration suite uses as its video.processed observation point.

resource "aws_dynamodb_table" "video_metadata" {
  name         = "video-metadata"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "videoId"

  attribute {
    name = "videoId"
    type = "S"
  }
}

# --- video.processed capture queue -----------------------------------------
#
# Capture queue: the video.processed rule targets it so the integration
# suite can assert "exactly one event with the deterministic eventId".
# The pytest suite drains it; backlog is test residue. Epic 3's history
# queue is a SEPARATE consumer of the same event (AD-1 pattern).
resource "aws_sqs_queue" "smoke_capture" {
  name                       = "smoke-capture-queue"
  visibility_timeout_seconds = 60
}

resource "aws_sqs_queue_policy" "smoke_capture" {
  queue_url = aws_sqs_queue.smoke_capture.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.smoke_capture.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.video_processed_capture.arn
        }
      }
    }]
  })
}

resource "aws_cloudwatch_event_rule" "video_processed_capture" {
  name           = "video-processed-to-smoke-capture"
  event_bus_name = aws_cloudwatch_event_bus.video_bus.name

  event_pattern = jsonencode({
    detail-type = ["video.processed"]
  })
}

resource "aws_cloudwatch_event_target" "smoke_capture_queue" {
  rule           = aws_cloudwatch_event_rule.video_processed_capture.name
  event_bus_name = aws_cloudwatch_event_bus.video_bus.name
  arn            = aws_sqs_queue.smoke_capture.arn
}
