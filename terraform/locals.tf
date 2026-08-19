# Shared Terraform locals.
#
# lambda_endpoint_url is the endpoint every Lambda function uses to reach
# floci from INSIDE its Docker container (host.docker.internal resolves to
# the host machine there; localhost would resolve to the container itself).
# Declared once, consumed by every function's AWS_ENDPOINT_URL env var —
# never hardcode it per-function.
locals {
  lambda_endpoint_url = "http://host.docker.internal:4566"
}
