# Story 2.2 — Processing State Machine + Event Publisher.
#
# Declares the processing leg's orchestration: the event-publisher
# Lambda (sole constructor of the video.processed envelope, AD-4/AD-6),
# its least-privilege role, the Step Functions execution role, and the
# processing-state-machine whose ASL is, in order:
#   Task(dynamodb:updateItem UPLOADED->PROCESSING)
#   -> Task(lambda:invoke transcode)
#   -> Task(dynamodb:updateItem ->PROCESSED)
#   -> Task(lambda:invoke event-publisher)
#
# The ASL lives in processing.asl.json (templatefile fills the resource
# ARNs/names). Status-first ordering is structural (AD-4): each
# updateItem completes before the next state runs; the terminal event is
# published only after the terminal transition is acknowledged. The
# inline condition pairs (#s = UPLOADED -> PROCESSING, #s = PROCESSING
# -> PROCESSED) mirror lambdas/_shared/status.py LEGAL_TRANSITIONS
# exactly — a transition-table change is one coordinated ASL +
# shared-layer change.
#
# FLOCI PLATFORM FACT: floci has no UpdateStateMachine — any ASL change
# requires `terraform apply -replace=aws_sfn_state_machine.processing`
# (destroy+recreate). Documented in README.md.
#
# REUSES aws_dynamodb_table.video_metadata (smoke.tf),
# aws_lambda_function.transcode (transcode.tf), and
# aws_cloudwatch_event_bus.video_bus (upload.tf) — none redeclared.

data "archive_file" "event_publisher_zip" {
  type = "zip"
  # _shared package at zip root (importable as `shared`) + the
  # event_publisher package. NOTE: these source blocks are maintained BY
  # HAND because the local dir is `_shared/` but the zip package must be
  # `shared/` — archive_file's source_dir cannot rename. Adding a module
  # to lambdas/_shared/ or lambdas/event_publisher/ REQUIRES a matching
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
    content  = file("${path.module}/../lambdas/event_publisher/__init__.py")
    filename = "event_publisher/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/event_publisher/handler.py")
    filename = "event_publisher/handler.py"
  }
  output_path = "${path.module}/event_publisher.zip"
}

resource "aws_iam_role" "event_publisher" {
  name = "event-publisher-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "event_publisher" {
  name = "event-publisher-lambda-policy"
  role = aws_iam_role.event_publisher.id

  # Least privilege (AD-4/AD-6): logs + PutEvents on video-bus only.
  # No DynamoDB, no S3 — the publisher builds the envelope from the ASL
  # domain payload and its env vars, nothing else.
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
        Action   = ["events:PutEvents"]
        Resource = aws_cloudwatch_event_bus.video_bus.arn
      },
    ]
  })
}

resource "aws_lambda_function" "event_publisher" {
  function_name    = "event-publisher"
  role             = aws_iam_role.event_publisher.arn
  runtime          = "python3.11"
  handler          = "event_publisher.handler.handler"
  filename         = data.archive_file.event_publisher_zip.output_path
  source_code_hash = data.archive_file.event_publisher_zip.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      PROCESSED_BUCKET = aws_s3_bucket.video_processed.bucket
      EVENT_BUS_NAME   = aws_cloudwatch_event_bus.video_bus.name
      AWS_ENDPOINT_URL = local.lambda_endpoint_url
    }
  }
}

# --- Step Functions execution role ----------------------------------------

resource "aws_iam_role" "sfn_processing" {
  name = "processing-state-machine-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn_processing" {
  name = "processing-state-machine-policy"
  role = aws_iam_role.sfn_processing.id

  # Least privilege: the state machine may update the metadata table and
  # invoke exactly its two worker Lambdas — nothing else.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
        ]
        Resource = aws_dynamodb_table.video_metadata.arn
      },
      {
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.transcode.arn,
          aws_lambda_function.event_publisher.arn,
        ]
      },
    ]
  })
}

# --- Processing state machine ----------------------------------------------

resource "aws_sfn_state_machine" "processing" {
  name     = "processing-state-machine"
  role_arn = aws_iam_role.sfn_processing.arn

  # ASL with resource ARNs/names filled by templatefile. FLOCI: no
  # UpdateStateMachine — change the ASL, then
  # `terraform apply -replace=aws_sfn_state_machine.processing`.
  definition = templatefile("${path.module}/processing.asl.json", {
    table_name    = aws_dynamodb_table.video_metadata.name
    transcode_arn = aws_lambda_function.transcode.arn
    publisher_arn = aws_lambda_function.event_publisher.arn
  })
}

# --- Outputs ---------------------------------------------------------------

output "processing_state_machine_arn" {
  value = aws_sfn_state_machine.processing.arn
}

output "processing_state_machine_name" {
  value = aws_sfn_state_machine.processing.name
}

output "event_publisher_function" {
  value = aws_lambda_function.event_publisher.function_name
}
