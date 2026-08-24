# Story 4.1 — Search Leg: search-index table, search queue, and the
# search-consumer Lambda.
#
# Wires the second video.processed consumer (AD-1): video.processed on
# video-bus -> search-queue (SQS) -> search-consumer Lambda -> one
# search-index entry per PROCESSED event, upserted by videoId. The PK IS
# the dedupe (NFR-1): plain PutItem, redelivery overwrites. Status filter
# runs before the metadata lookup; the metadata record fetched during
# poison validation is also the source of `title` (FR-17/AD-6).
#
# AD-1 as-built precedent: a new consumer = new queue + new rule. This
# file declares its OWN video.processed rule targeting ONLY the search
# queue; the history rule (history.tf), upload rule (trigger.tf), and
# capture rule (integration.tf) are untouched.
#
# REUSES aws_cloudwatch_event_bus.video_bus (upload.tf) and
# aws_dynamodb_table.video_metadata (integration.tf) — none redeclared.

# --- search-index table -----------------------------------------------------

# Derived table (AD-3): PK videoId, exactly one writer
# (search-consumer), disposable and rebuildable from video-metadata.
resource "aws_dynamodb_table" "search_index" {
  name         = "search-index"
  billing_mode = "PAY_PER_REQUEST"

  hash_key = "videoId"

  attribute {
    name = "videoId"
    type = "S"
  }
}

# --- Queue ------------------------------------------------------------------

resource "aws_sqs_queue" "search" {
  name = "search-queue"
  # Real-AWS guidance: visibility timeout >= 6x the function timeout
  # (30 s) so a slow/failed invocation can be retried before the message
  # reappears to a second consumer.
  visibility_timeout_seconds = 300
}

# The EventBridge rule delivers to the queue; the queue must allow
# events.amazonaws.com to send, scoped to this rule only.
resource "aws_sqs_queue_policy" "search" {
  queue_url = aws_sqs_queue.search.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.search.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.video_processed_search.arn
        }
      }
    }]
  })
}

# --- EventBridge rule --------------------------------------------------------

# Routing is ONE RULE PER CONSUMER: this new rule's ONLY target is the
# search queue. Never add a target to an existing rule.
resource "aws_cloudwatch_event_rule" "video_processed_search" {
  name           = "video-processed-to-search"
  event_bus_name = aws_cloudwatch_event_bus.video_bus.name

  event_pattern = jsonencode({
    detail-type = ["video.processed"]
  })
}

resource "aws_cloudwatch_event_target" "search_queue" {
  rule           = aws_cloudwatch_event_rule.video_processed_search.name
  event_bus_name = aws_cloudwatch_event_bus.video_bus.name
  arn            = aws_sqs_queue.search.arn
}

# --- search-consumer Lambda ---------------------------------------------------

data "archive_file" "search_consumer_zip" {
  type = "zip"
  # _shared package at zip root (importable as `shared`) + the
  # search_consumer package. NOTE: these source blocks are maintained BY
  # HAND because the local dir is `_shared/` but the zip package must be
  # `shared/` — archive_file's source_dir cannot rename. Adding a module
  # to lambdas/_shared/ or lambdas/search_consumer/ REQUIRES a matching
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
    content  = file("${path.module}/../lambdas/search_consumer/__init__.py")
    filename = "search_consumer/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/search_consumer/handler.py")
    filename = "search_consumer/handler.py"
  }
  output_path = "${path.module}/search_consumer.zip"
}

resource "aws_iam_role" "search_consumer" {
  name = "search-consumer-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "search_consumer" {
  name = "search-consumer-lambda-policy"
  role = aws_iam_role.search_consumer.id

  # Least privilege: logs + GetItem on video-metadata (poison validation
  # + title source) + PutItem on search-index (the upsert) + the standard
  # SQS event-source-mapping set on the search queue only. No S3, no
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
        Resource = aws_dynamodb_table.search_index.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.search.arn
      },
    ]
  })
}

resource "aws_lambda_function" "search_consumer" {
  function_name    = "search-consumer"
  role             = aws_iam_role.search_consumer.arn
  runtime          = "python3.11"
  handler          = "search_consumer.handler.handler"
  filename         = data.archive_file.search_consumer_zip.output_path
  source_code_hash = data.archive_file.search_consumer_zip.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      METADATA_TABLE     = aws_dynamodb_table.video_metadata.name
      SEARCH_INDEX_TABLE = aws_dynamodb_table.search_index.name
      AWS_ENDPOINT_URL   = local.lambda_endpoint_url
    }
  }
}

# --- SQS event-source mapping ------------------------------------------------

resource "aws_lambda_event_source_mapping" "search" {
  event_source_arn = aws_sqs_queue.search.arn
  function_name    = aws_lambda_function.search_consumer.arn
  # One record per invocation: keeps per-video traceability trivial AND
  # makes the handler's raise-on-transient-error policy retry exactly the
  # failed message — a larger batch would reprocess (re-index) the whole
  # batch on any single failure. batch_size=1 is load-bearing for FR-15.
  batch_size = 1
}

# --- Outputs ---------------------------------------------------------------

output "search_index_table_name" {
  value = aws_dynamodb_table.search_index.name
}

output "search_queue_name" {
  value = aws_sqs_queue.search.name
}

output "search_queue_url" {
  value = aws_sqs_queue.search.url
}

output "search_queue_arn" {
  value = aws_sqs_queue.search.arn
}

output "search_consumer_function" {
  value = aws_lambda_function.search_consumer.function_name
}
