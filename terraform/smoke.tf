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
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME       = aws_dynamodb_table.video_metadata.name
      AWS_ENDPOINT_URL = local.lambda_endpoint_url
    }
  }
}

output "smoke_function" {
  value = aws_lambda_function.smoke.function_name
}
