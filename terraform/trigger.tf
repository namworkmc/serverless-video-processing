# Story 2.3 — Trigger Leg: EventBridge Rule, Queue, and Shim.
#
# Wires the automatic trigger (AD-5): video.uploaded on video-bus ->
# processing-trigger-queue (SQS) -> sfn-trigger-shim Lambda ->
# StartExecution on the processing state machine with the deterministic
# execution name eb-{eventId}. A republish/redelivery hits
# ExecutionAlreadyExists, which the shim treats as success (dedupe).
#
# FLOCI CONSTRAINT: EventBridge cannot target Step Functions state
# machines, so the rule targets ONLY the queue — the shim exists
# precisely because of this. If floci later supports SFN targets
# natively, the shim is replaced by a direct target (Terraform-only
# change).
#
# REUSES aws_cloudwatch_event_bus.video_bus (upload.tf) and
# aws_sfn_state_machine.processing (processing.tf) — none redeclared.

# --- Queue -----------------------------------------------------------------

resource "aws_sqs_queue" "processing_trigger" {
  name = "processing-trigger-queue"
  # Real-AWS guidance: visibility timeout >= 6x the function timeout
  # (30 s) so a slow/failed invocation can be retried before the message
  # reappears to a second consumer.
  visibility_timeout_seconds = 300
}

# The EventBridge rule delivers to the queue; the queue must allow
# events.amazonaws.com to send, scoped to this rule only.
resource "aws_sqs_queue_policy" "processing_trigger" {
  queue_url = aws_sqs_queue.processing_trigger.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.processing_trigger.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.video_uploaded.arn
        }
      }
    }]
  })
}

# --- EventBridge rule --------------------------------------------------------

# Routing is by event name (spine routing rule): any producer of
# video.uploaded on video-bus triggers processing. The ONLY target is the
# queue — never the state machine (floci cannot, AD-5).
resource "aws_cloudwatch_event_rule" "video_uploaded" {
  name           = "video-uploaded-to-processing-trigger"
  event_bus_name = aws_cloudwatch_event_bus.video_bus.name

  event_pattern = jsonencode({
    detail-type = ["video.uploaded"]
  })
}

resource "aws_cloudwatch_event_target" "processing_trigger_queue" {
  rule           = aws_cloudwatch_event_rule.video_uploaded.name
  event_bus_name = aws_cloudwatch_event_bus.video_bus.name
  arn            = aws_sqs_queue.processing_trigger.arn
}

# --- sfn-trigger-shim Lambda -------------------------------------------------

data "archive_file" "sfn_trigger_shim_zip" {
  type = "zip"
  # _shared package at zip root (importable as `shared`) + the
  # sfn_trigger_shim package. NOTE: these source blocks are maintained BY
  # HAND because the local dir is `_shared/` but the zip package must be
  # `shared/` — archive_file's source_dir cannot rename. Adding a module
  # to lambdas/_shared/ or lambdas/sfn_trigger_shim/ REQUIRES a matching
  # source block here; the invoke fails loudly on a missing module
  # (ImportError).
  source {
    content  = file("${path.module}/../lambdas/_shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/_shared/status.py")
    filename = "shared/status.py"
  }
  source {
    content  = file("${path.module}/../lambdas/_shared/events.py")
    filename = "shared/events.py"
  }
  source {
    content  = file("${path.module}/../lambdas/_shared/errors.py")
    filename = "shared/errors.py"
  }
  source {
    content  = file("${path.module}/../lambdas/_shared/clients.py")
    filename = "shared/clients.py"
  }
  source {
    content  = file("${path.module}/../lambdas/sfn_trigger_shim/__init__.py")
    filename = "sfn_trigger_shim/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/sfn_trigger_shim/handler.py")
    filename = "sfn_trigger_shim/handler.py"
  }
  output_path = "${path.module}/sfn_trigger_shim.zip"
}

resource "aws_iam_role" "sfn_trigger_shim" {
  name = "sfn-trigger-shim-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn_trigger_shim" {
  name = "sfn-trigger-shim-lambda-policy"
  role = aws_iam_role.sfn_trigger_shim.id

  # Least privilege (AD-5): logs + StartExecution on the processing state
  # machine only + the standard SQS event-source-mapping set on the
  # trigger queue only. No DynamoDB, no S3, no EventBridge.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.processing.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.processing_trigger.arn
      },
    ]
  })
}

resource "aws_lambda_function" "sfn_trigger_shim" {
  function_name    = "sfn-trigger-shim"
  role             = aws_iam_role.sfn_trigger_shim.arn
  runtime          = "python3.11"
  handler          = "sfn_trigger_shim.handler.handler"
  filename         = data.archive_file.sfn_trigger_shim_zip.output_path
  source_code_hash = data.archive_file.sfn_trigger_shim_zip.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.processing.arn
      AWS_ENDPOINT_URL  = local.lambda_endpoint_url
    }
  }
}

# --- SQS event-source mapping ------------------------------------------------

resource "aws_lambda_event_source_mapping" "processing_trigger" {
  event_source_arn = aws_sqs_queue.processing_trigger.arn
  function_name    = aws_lambda_function.sfn_trigger_shim.arn
  # One record per invocation: keeps dedupe semantics and per-video
  # traceability trivial.
  batch_size = 1
}

# --- Outputs ---------------------------------------------------------------

output "processing_trigger_queue_name" {
  value = aws_sqs_queue.processing_trigger.name
}

output "processing_trigger_queue_url" {
  value = aws_sqs_queue.processing_trigger.url
}

output "processing_trigger_queue_arn" {
  value = aws_sqs_queue.processing_trigger.arn
}

output "sfn_trigger_shim_function" {
  value = aws_lambda_function.sfn_trigger_shim.function_name
}
