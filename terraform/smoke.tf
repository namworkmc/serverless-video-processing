# Story 1.2 — Shared Access Layer smoke fixture.
#
# Declares the video-metadata table (the shared layer's enforcement target;
# Story 1.3 REUSES this table, it does not redeclare it) and a smoke Lambda
# that exercises the shared layer inside floci's real Docker runtime.
#
# These resources stay declared after verification as a re-runnable lab
# fixture. Invoke ad-hoc:
#   aws lambda invoke --endpoint-url http://localhost:4566 \
#     --function-name smoke --payload '{"scenario":"all"}' out.json

data "archive_file" "smoke_zip" {
  type = "zip"
  # _shared package at zip root (importable as `shared`) + smoke handler.
  # NOTE: these source blocks are maintained BY HAND because the local dir
  # is `_shared/` but the zip package must be `shared/` — archive_file's
  # source_dir cannot rename. Adding a module to lambdas/_shared/ REQUIRES
  # a matching source block here (and in every later function's zip); the
  # smoke invoke fails loudly on a missing module (ImportError).
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
    content  = file("${path.module}/../lambdas/smoke/handler.py")
    filename = "handler.py"
  }
  output_path = "${path.module}/smoke.zip"
}

resource "aws_dynamodb_table" "video_metadata" {
  name         = "video-metadata"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "videoId"

  attribute {
    name = "videoId"
    type = "S"
  }
}

# --- Runtime-scenario fixtures (retro action item: backstop deployed
# epic-2 wiring in ci-local.sh stage 5) ------------------------------------

# Capture queue: the video.processed rule targets it so the state-machine
# smoke scenario can assert "exactly one event with the deterministic
# eventId". The smoke Lambda drains it; backlog is test residue. Epic 3's
# history queue is a SEPARATE consumer of the same event (AD-1 pattern).
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

resource "aws_iam_role" "smoke" {
  name = "smoke-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "smoke" {
  name = "smoke-lambda-policy"
  role = aws_iam_role.smoke.id

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
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
        ]
        Resource = aws_dynamodb_table.video_metadata.arn
      },
      # Runtime scenarios: seed/cleanup fixture objects in both buckets.
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = [
          "${aws_s3_bucket.video_uploads.arn}/*",
          "${aws_s3_bucket.video_processed.arn}/*",
        ]
      },
      # transcode scenario: invoke the deployed transcode zip.
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.transcode.arn
      },
      # state-machine scenario: drive the deployed state machine.
      {
        Effect = "Allow"
        Action = [
          "states:StartExecution",
          "states:DescribeExecution",
        ]
        Resource = aws_sfn_state_machine.processing.arn
      },
      # trigger-leg scenario: publish video.uploaded on the bus.
      {
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = aws_cloudwatch_event_bus.video_bus.arn
      },
      # state-machine scenario: drain/read the capture queue.
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.smoke_capture.arn
      },
    ]
  })
}

resource "aws_lambda_function" "smoke" {
  function_name    = "smoke"
  role             = aws_iam_role.smoke.arn
  runtime          = "python3.11"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.smoke_zip.output_path
  source_code_hash = data.archive_file.smoke_zip.output_base64sha256
  # Runtime scenarios poll the state machine (up to ~90s trigger leg).
  timeout = 180

  environment {
    variables = {
      TABLE_NAME        = aws_dynamodb_table.video_metadata.name
      UPLOADS_BUCKET    = aws_s3_bucket.video_uploads.bucket
      PROCESSED_BUCKET  = aws_s3_bucket.video_processed.bucket
      STATE_MACHINE_ARN = aws_sfn_state_machine.processing.arn
      EVENT_BUS_NAME    = aws_cloudwatch_event_bus.video_bus.name
      CAPTURE_QUEUE_URL = aws_sqs_queue.smoke_capture.url
      AWS_ENDPOINT_URL  = local.lambda_endpoint_url
    }
  }
}

output "smoke_function" {
  value = aws_lambda_function.smoke.function_name
}
