terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Floci — local AWS emulator (LocalStack-compatible).
# All services at http://localhost:4566, any region, dummy credentials.
provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true

  endpoints {
    s3           = "http://localhost:4566"
    sqs          = "http://localhost:4566"
    sns          = "http://localhost:4566"
    dynamodb     = "http://localhost:4566"
    lambda       = "http://localhost:4566"
    stepfunctions = "http://localhost:4566"
    events       = "http://localhost:4566"
    apigateway   = "http://localhost:4566"
    apigatewayv2 = "http://localhost:4566"
    iam          = "http://localhost:4566"
    cloudwatch   = "http://localhost:4566"
    sts          = "http://localhost:4566"
  }
}
