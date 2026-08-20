# Story 2.1 — Transcode Worker Lambda (pure S3 in -> S3 out).
#
# Declares the processing leg's first worker: video-processed bucket,
# the transcode Lambda (zip: shared/ at root + transcode/ package), and
# its least-privilege role. PURE WORKER (AD-4): no DynamoDB, no
# EventBridge — the policy grants logs, s3:GetObject on video-uploads/*,
# and s3:PutObject on video-processed/*, nothing else.
#
# Invoked ad-hoc for Story 2.1 verification; Story 2.2's state machine
# invokes it as the transcode task.

data "archive_file" "transcode_zip" {
  type = "zip"
  # _shared package at zip root (importable as `shared`) + the transcode
  # package. NOTE: these source blocks are maintained BY HAND because the
  # local dir is `_shared/` but the zip package must be `shared/` —
  # archive_file's source_dir cannot rename. Adding a module to
  # lambdas/_shared/ or lambdas/transcode/ REQUIRES a matching source
  # block here; the invoke fails loudly on a missing module (ImportError).
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
    content  = file("${path.module}/../lambdas/transcode/__init__.py")
    filename = "transcode/__init__.py"
  }
  source {
    content  = file("${path.module}/../lambdas/transcode/handler.py")
    filename = "transcode/handler.py"
  }
  output_path = "${path.module}/transcode.zip"
}

resource "aws_s3_bucket" "video_processed" {
  bucket = "video-processed"
  # Lab bucket: allow terraform destroy even when objects exist.
  force_destroy = true
}

resource "aws_iam_role" "transcode" {
  name = "transcode-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "transcode" {
  name = "transcode-lambda-policy"
  role = aws_iam_role.transcode.id

  # Least privilege (AD-4): logs only + read uploads + write processed.
  # No DynamoDB, no EventBridge — the pure worker never touches them.
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
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.video_uploads.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.video_processed.arn}/*"
      },
    ]
  })
}

resource "aws_lambda_function" "transcode" {
  function_name    = "transcode"
  role             = aws_iam_role.transcode.arn
  runtime          = "python3.11"
  handler          = "transcode.handler.handler"
  filename         = data.archive_file.transcode_zip.output_path
  source_code_hash = data.archive_file.transcode_zip.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      UPLOADS_BUCKET   = aws_s3_bucket.video_uploads.bucket
      PROCESSED_BUCKET = aws_s3_bucket.video_processed.bucket
      AWS_ENDPOINT_URL = local.lambda_endpoint_url
    }
  }
}

output "transcode_function" {
  value = aws_lambda_function.transcode.function_name
}
