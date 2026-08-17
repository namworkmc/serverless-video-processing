# Phase 0 smoke test — proves Terraform <-> floci wiring.
# Remove once real resources land (Epic 1+).
resource "aws_s3_bucket" "smoke" {
  bucket = "phase0-smoke-test"
}

output "smoke_bucket" {
  value = aws_s3_bucket.smoke.bucket
}
