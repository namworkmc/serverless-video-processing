# Story 1.3 — Upload Journey Through the Gateway.
#
# Declares the upload leg: video-uploads bucket, custom EventBridge bus,
# the upload-handler Lambda (zip: shared/ at root + upload_handler/
# package), its role, and API Gateway v2 with POST /videos/upload.
#
# REUSES aws_dynamodb_table.video_metadata from smoke.tf — the table is
# NOT redeclared here.
#
# Gateway data plane (floci): the Terraform invoke URL does not resolve
# locally; the gateway is reachable only at
#   http://localhost:4566/_aws/execute-api/{apiId}/{stage}/{path}
# — see the gateway_base_url output.

data "archive_file" "upload_handler_zip" {
  type = "zip"
  # _shared package at zip root (importable as `shared`) + the
  # upload_handler package. NOTE: these source blocks are maintained BY
  # HAND because the local dir is `_shared/` but the zip package must be
  # `shared/` — archive_file's source_dir cannot rename. Adding a module
  # to lambdas/_shared/ or lambdas/upload_handler/ REQUIRES a matching
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
    content  = file("${path.module}/../lambdas/upload_handler/__init__.py")
    filename = "upload_handler/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/upload_handler/handler.py")
    filename = "upload_handler/handler.py"
  }
  output_path = "${path.module}/upload_handler.zip"
}

resource "aws_s3_bucket" "video_uploads" {
  bucket = "video-uploads"
  # Lab bucket: allow terraform destroy even when uploads exist.
  force_destroy = true
}

resource "aws_cloudwatch_event_bus" "video_bus" {
  name = "video-bus"
}

resource "aws_iam_role" "upload_handler" {
  name = "upload-handler-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "upload_handler" {
  name = "upload-handler-lambda-policy"
  role = aws_iam_role.upload_handler.id

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
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.video_uploads.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
        ]
        Resource = aws_dynamodb_table.video_metadata.arn
      },
      {
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = aws_cloudwatch_event_bus.video_bus.arn
      },
    ]
  })
}

resource "aws_lambda_function" "upload_handler" {
  function_name    = "upload-handler"
  role             = aws_iam_role.upload_handler.arn
  runtime          = "python3.11"
  handler          = "upload_handler.handler.handler"
  filename         = data.archive_file.upload_handler_zip.output_path
  source_code_hash = data.archive_file.upload_handler_zip.output_base64sha256
  timeout          = 30
  # Buffers the whole multipart body in memory; 128 MB default is too
  # tight for anything but the smallest clips.
  memory_size = 256

  environment {
    variables = {
      UPLOADS_BUCKET   = aws_s3_bucket.video_uploads.bucket
      METADATA_TABLE   = aws_dynamodb_table.video_metadata.name
      EVENT_BUS_NAME   = aws_cloudwatch_event_bus.video_bus.name
      AWS_ENDPOINT_URL = local.lambda_endpoint_url
    }
  }
}

# --- API Gateway v2 (HTTP API) -------------------------------------------

resource "aws_apigatewayv2_api" "gateway" {
  name          = "video-gateway"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "upload" {
  api_id                 = aws_apigatewayv2_api.gateway.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.upload_handler.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "upload" {
  api_id    = aws_apigatewayv2_api.gateway.id
  route_key = "POST /videos/upload"
  target    = "integrations/${aws_apigatewayv2_integration.upload.id}"
}

resource "aws_apigatewayv2_stage" "local" {
  api_id      = aws_apigatewayv2_api.gateway.id
  name        = "local"
  auto_deploy = true
}

resource "aws_lambda_permission" "gateway_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.upload_handler.function_name
  principal     = "apigateway.amazonaws.com"
  # Scoped to the local stage + upload route only.
  source_arn = "${aws_apigatewayv2_api.gateway.execution_arn}/${aws_apigatewayv2_stage.local.name}/POST/videos/upload"
}

# --- Outputs ---------------------------------------------------------------

output "api_id" {
  value = aws_apigatewayv2_api.gateway.id
}

output "gateway_base_url" {
  value = "http://localhost:4566/_aws/execute-api/${aws_apigatewayv2_api.gateway.id}/${aws_apigatewayv2_stage.local.name}"
}
