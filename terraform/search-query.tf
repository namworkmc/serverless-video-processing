# Story 4.2 — Search Surface: the search-query Lambda behind the gateway.
#
# Completes the third and final client journey (FR-18/FR-21): title-
# substring search over search-index through GET /videos/search?title=.
# The read is a plain table Scan with a contains(title, :t)
# FilterExpression — sanctioned at lab scale (AD-3/NFR-7), no GSI.
#
# This Lambda is REQUEST-INVOKED behind the EXISTING gateway (upload.tf):
# by design there is NO queue, NO EventBridge rule, and NO event-source
# mapping here — the one-rule-per-consumer rule does not apply to query
# functions, and adding a rule would be wrong.
#
# REUSES aws_apigatewayv2_api.gateway + aws_apigatewayv2_stage.local
# (upload.tf) and aws_dynamodb_table.search_index (search.tf) — none
# redeclared.

# --- search-query Lambda ------------------------------------------------------

data "archive_file" "search_query_zip" {
  type = "zip"
  # Same hand-maintained source-block layout as every other zip above:
  # `shared/` at zip root (ALL FIVE modules — shared/__init__ imports them
  # all) + the search_query package. Adding a module to lambdas/_shared/
  # or lambdas/search_query/ REQUIRES a matching source block here; the
  # invoke fails loudly on a missing module (ImportError).
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
    content  = file("${path.module}/../lambdas/search_query/__init__.py")
    filename = "search_query/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/search_query/handler.py")
    filename = "search_query/handler.py"
  }
  output_path = "${path.module}/search_query.zip"
}

resource "aws_iam_role" "search_query" {
  name = "search-query-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "search_query" {
  name = "search-query-lambda-policy"
  role = aws_iam_role.search_query.id

  # Least privilege: logs + Scan on search-index (the read). No writes,
  # no metadata access, no S3, no queues, no EventBridge.
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
        Resource = aws_dynamodb_table.search_index.arn
      },
    ]
  })
}

resource "aws_lambda_function" "search_query" {
  function_name    = "search-query"
  role             = aws_iam_role.search_query.arn
  runtime          = "python3.11"
  handler          = "search_query.handler.handler"
  filename         = data.archive_file.search_query_zip.output_path
  source_code_hash = data.archive_file.search_query_zip.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      SEARCH_INDEX_TABLE = aws_dynamodb_table.search_index.name
      AWS_ENDPOINT_URL   = local.lambda_endpoint_url
    }
  }
}

# --- Gateway route (joins the EXISTING aws_apigatewayv2_api.gateway) ---------

resource "aws_apigatewayv2_integration" "search_query" {
  api_id                 = aws_apigatewayv2_api.gateway.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.search_query.invoke_arn
  payload_format_version = "2.0"
}

# Route keys carry no query string — the title parameter arrives via
# queryStringParameters (API GW v2 payload format 2.0).
resource "aws_apigatewayv2_route" "search_query" {
  api_id    = aws_apigatewayv2_api.gateway.id
  route_key = "GET /videos/search"
  target    = "integrations/${aws_apigatewayv2_integration.search_query.id}"
}

resource "aws_lambda_permission" "gateway_invoke_search_query" {
  statement_id  = "AllowAPIGatewayInvokeSearchQuery"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.search_query.function_name
  principal     = "apigateway.amazonaws.com"
  # Scoped to the local stage + search route only. The exact literal
  # segment /videos/search takes precedence over the /videos/{videoId}/...
  # parametrized routes — no conflict with Story 3.2's history route.
  source_arn = "${aws_apigatewayv2_api.gateway.execution_arn}/${aws_apigatewayv2_stage.local.name}/GET/videos/search"
}

# --- Outputs ---------------------------------------------------------------

output "search_query_function" {
  value = aws_lambda_function.search_query.function_name
}
