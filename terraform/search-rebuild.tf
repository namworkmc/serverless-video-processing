# Story 4.3 — Admin-Only Search-Index Rebuild.
#
# A `search-rebuild` Lambda reachable ONLY by direct invoke (ad-hoc admin
# via local boto3 / floci's Lambda REST — permitted inspection/admin,
# never setup): scans video-metadata for PROCESSED records and upserts
# each into search-index as {videoId, title, processedKey, indexedAt} —
# the exact entry shape the search-consumer writes (Story 4.1).
#
# FR-19 STRUCTURAL CONSTRAINT — THIS FILE DELIBERATELY CONTAINS NONE OF:
#   - any aws_apigatewayv2_integration / route / aws_lambda_permission
#     (no gateway surface of any kind)
#   - any aws_sqs_queue / queue policy (no queue consumer)
#   - any aws_cloudwatch_event_rule / target (no event routing)
#   - any aws_lambda_event_source_mapping
# The admin-only constraint holds by ABSENCE in this declaration, not by
# convention. Do not "complete" this file with wiring from search.tf or
# search-query.tf — those blocks are deliberately omitted here.
#
# REUSES existing resources BY REFERENCE — none redeclared:
#   - aws_dynamodb_table.video_metadata  (integration.tf) — Scan only
#   - aws_dynamodb_table.search_index    (search.tf)       — PutItem only

# --- zip packaging -------------------------------------------------------------

data "archive_file" "search_rebuild_zip" {
  type = "zip"
  # _shared package at zip root (importable as `shared`) + the
  # search_rebuild package. NOTE: these source blocks are maintained BY
  # HAND because the local dir is `_shared/` but the zip package must be
  # `shared/` — archive_file's source_dir cannot rename. Adding a module
  # to lambdas/_shared/ or lambdas/search_rebuild/ REQUIRES a matching
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
    content  = file("${path.module}/../lambdas/search_rebuild/__init__.py")
    filename = "search_rebuild/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/search_rebuild/handler.py")
    filename = "search_rebuild/handler.py"
  }
  output_path = "${path.module}/search_rebuild.zip"
}

# --- IAM -----------------------------------------------------------------------

resource "aws_iam_role" "search_rebuild" {
  name = "search-rebuild-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "search_rebuild" {
  name = "search-rebuild-lambda-policy"
  role = aws_iam_role.search_rebuild.id

  # Least privilege: logs + Scan on video-metadata (the rebuild source)
  # + PutItem on search-index (the upsert). No DeleteItem (the rebuild
  # repopulates, it never sweeps), no GetItem, no S3, no EventBridge,
  # no Step Functions, no SQS.
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
        Action   = ["dynamodb:Scan"]
        Resource = aws_dynamodb_table.video_metadata.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = aws_dynamodb_table.search_index.arn
      },
    ]
  })
}

# --- Lambda ----------------------------------------------------------------------

resource "aws_lambda_function" "search_rebuild" {
  function_name    = "search-rebuild"
  role             = aws_iam_role.search_rebuild.arn
  runtime          = "python3.11"
  handler          = "search_rebuild.handler.handler"
  filename         = data.archive_file.search_rebuild_zip.output_path
  source_code_hash = data.archive_file.search_rebuild_zip.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      METADATA_TABLE     = aws_dynamodb_table.video_metadata.name
      SEARCH_INDEX_TABLE = aws_dynamodb_table.search_index.name
      AWS_ENDPOINT_URL   = local.lambda_endpoint_url
    }
  }

  # NO triggers of any kind are declared here: no gateway integration,
  # no lambda permission, no event-source mapping, no rule target.
  # Direct invocation only (admin tooling, FR-19).
}

# --- Outputs ---------------------------------------------------------------------

output "search_rebuild_function" {
  value = aws_lambda_function.search_rebuild.function_name
}
