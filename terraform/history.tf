# Story 3.1 — History Leg: status-history table, history queue, and the
# history-consumer Lambda.
#
# Wires the first video.processed consumer (AD-1): video.processed on
# video-bus -> history-queue (SQS) -> history-consumer Lambda ->
# one status-history entry per unique eventId. Dedupe is the write
# (PutItem + attribute_not_exists(eventId)); poison events (unknown
# videoId) are dropped and acked; transient errors raise for SQS retry.
#
# AD-1: a new consumer = new queue + new rule target. The video.uploaded
# rule (trigger.tf) and the video.processed capture rule (integration.tf)
# are untouched; this file declares its OWN video.processed rule targeting
# ONLY the history queue.
#
# REUSES aws_cloudwatch_event_bus.video_bus (upload.tf) and
# aws_dynamodb_table.video_metadata (integration.tf) — none redeclared.

# --- status-history table ---------------------------------------------------

# Append-only derived table (AD-3): PK eventId, exactly one writer
# (history-consumer), disposable and rebuildable from video-metadata.
resource "aws_dynamodb_table" "status_history" {
  name         = "status-history"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "eventId"

  attribute {
    name = "eventId"
    type = "S"
  }
}

# --- Queue ------------------------------------------------------------------

resource "aws_sqs_queue" "history" {
  name = "history-queue"
  # Real-AWS guidance: visibility timeout >= 6x the function timeout
  # (30 s) so a slow/failed invocation can be retried before the message
  # reappears to a second consumer.
  visibility_timeout_seconds = 300
}

# The EventBridge rule delivers to the queue; the queue must allow
# events.amazonaws.com to send, scoped to this rule only.
resource "aws_sqs_queue_policy" "history" {
  queue_url = aws_sqs_queue.history.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.history.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.video_processed_history.arn
        }
      }
    }]
  })
}

# --- EventBridge rule --------------------------------------------------------

# Routing is by event name (spine routing rule): any producer of
# video.processed on video-bus feeds history. The ONLY target is the
# history queue.
resource "aws_cloudwatch_event_rule" "video_processed_history" {
  name           = "video-processed-to-history"
  event_bus_name = aws_cloudwatch_event_bus.video_bus.name

  event_pattern = jsonencode({
    detail-type = ["video.processed"]
  })
}

resource "aws_cloudwatch_event_target" "history_queue" {
  rule           = aws_cloudwatch_event_rule.video_processed_history.name
  event_bus_name = aws_cloudwatch_event_bus.video_bus.name
  arn            = aws_sqs_queue.history.arn
}

# --- history-consumer Lambda --------------------------------------------------

data "archive_file" "history_consumer_zip" {
  type = "zip"
  # _shared package at zip root (importable as `shared`) + the
  # history_consumer package. NOTE: these source blocks are maintained BY
  # HAND because the local dir is `_shared/` but the zip package must be
  # `shared/` — archive_file's source_dir cannot rename. Adding a module
  # to lambdas/_shared/ or lambdas/history_consumer/ REQUIRES a matching
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
    content  = file("${path.module}/../lambdas/history_consumer/__init__.py")
    filename = "history_consumer/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/history_consumer/handler.py")
    filename = "history_consumer/handler.py"
  }
  output_path = "${path.module}/history_consumer.zip"
}

resource "aws_iam_role" "history_consumer" {
  name = "history-consumer-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "history_consumer" {
  name = "history-consumer-lambda-policy"
  role = aws_iam_role.history_consumer.id

  # Least privilege: logs + GetItem on video-metadata (poison validation)
  # + PutItem on status-history (the append) + the standard SQS
  # event-source-mapping set on the history queue only. No S3, no
  # EventBridge, no Step Functions.
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
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.video_metadata.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.status_history.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.history.arn
      },
    ]
  })
}

resource "aws_lambda_function" "history_consumer" {
  function_name    = "history-consumer"
  role             = aws_iam_role.history_consumer.arn
  runtime          = "python3.11"
  handler          = "history_consumer.handler.handler"
  filename         = data.archive_file.history_consumer_zip.output_path
  source_code_hash = data.archive_file.history_consumer_zip.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      METADATA_TABLE   = aws_dynamodb_table.video_metadata.name
      HISTORY_TABLE    = aws_dynamodb_table.status_history.name
      AWS_ENDPOINT_URL = local.lambda_endpoint_url
    }
  }
}

# --- SQS event-source mapping ------------------------------------------------

resource "aws_lambda_event_source_mapping" "history" {
  event_source_arn = aws_sqs_queue.history.arn
  function_name    = aws_lambda_function.history_consumer.arn
  # One record per invocation: keeps dedupe semantics and per-video
  # traceability trivial.
  batch_size = 1
}

# --- history-query Lambda (Story 3.2) ----------------------------------------

# GET /videos/{videoId}/history through the EXISTING gateway (upload.tf):
# 404 gate on video-metadata, then a filtered Scan of status-history.
data "archive_file" "history_query_zip" {
  type = "zip"
  # Same hand-maintained source-block layout as the consumer zip above:
  # `shared/` at zip root + the history_query package. Adding a module to
  # lambdas/_shared/ or lambdas/history_query/ REQUIRES a matching source
  # block here.
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
    content  = file("${path.module}/../lambdas/history_query/__init__.py")
    filename = "history_query/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/history_query/handler.py")
    filename = "history_query/handler.py"
  }
  output_path = "${path.module}/history_query.zip"
}

resource "aws_iam_role" "history_query" {
  name = "history-query-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "history_query" {
  name = "history-query-lambda-policy"
  role = aws_iam_role.history_query.id

  # Least privilege: logs + GetItem on video-metadata (the 404 gate)
  # + Scan on status-history (the read). No writes, no S3, no queues.
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
        Action   = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.video_metadata.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Scan"]
        Resource = aws_dynamodb_table.status_history.arn
      },
    ]
  })
}

resource "aws_lambda_function" "history_query" {
  function_name    = "history-query"
  role             = aws_iam_role.history_query.arn
  runtime          = "python3.11"
  handler          = "history_query.handler.handler"
  filename         = data.archive_file.history_query_zip.output_path
  source_code_hash = data.archive_file.history_query_zip.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      METADATA_TABLE   = aws_dynamodb_table.video_metadata.name
      HISTORY_TABLE    = aws_dynamodb_table.status_history.name
      AWS_ENDPOINT_URL = local.lambda_endpoint_url
    }
  }
}

# --- Gateway route (joins the EXISTING aws_apigatewayv2_api.gateway) ---------

resource "aws_apigatewayv2_integration" "history_query" {
  api_id                 = aws_apigatewayv2_api.gateway.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.history_query.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "history_query" {
  api_id    = aws_apigatewayv2_api.gateway.id
  route_key = "GET /videos/{videoId}/history"
  target    = "integrations/${aws_apigatewayv2_integration.history_query.id}"
}

resource "aws_lambda_permission" "gateway_invoke_history_query" {
  statement_id  = "AllowAPIGatewayInvokeHistoryQuery"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.history_query.function_name
  principal     = "apigateway.amazonaws.com"
  # Scoped to the local stage + history route only.
  source_arn = "${aws_apigatewayv2_api.gateway.execution_arn}/${aws_apigatewayv2_stage.local.name}/GET/videos/{videoId}/history"
}

# --- Outputs ---------------------------------------------------------------

output "status_history_table_name" {
  value = aws_dynamodb_table.status_history.name
}

output "history_queue_name" {
  value = aws_sqs_queue.history.name
}

output "history_queue_url" {
  value = aws_sqs_queue.history.url
}

output "history_queue_arn" {
  value = aws_sqs_queue.history.arn
}

output "history_consumer_function" {
  value = aws_lambda_function.history_consumer.function_name
}

output "history_query_function" {
  value = aws_lambda_function.history_query.function_name
}
