#!/usr/bin/env bash
# Local mirror of the GitHub Actions CI pipeline (.github/workflows/ci.yml).
# Runs the same commands, in the same order, so a green run here predicts
# a green run in CI. Requires: uv, terraform, docker (for the smoke stage).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> [1/4] lint"
uv run --with ruff ruff check lambdas/ --select E,F
(cd terraform && terraform fmt -check -recursive)

echo "==> [2/4] unit-test"
uv run --with 'pytest>=8.0' pytest lambdas/ -q

echo "==> [3/4] terraform-validate"
(cd terraform && terraform init -backend=false -input=false >/dev/null && terraform validate)

echo "==> [4/4] smoke (requires Docker; reuses running floci if healthy)"
docker compose up -d --wait
(cd terraform && terraform init -input=false >/dev/null && terraform apply -auto-approve)
RESPONSE="$(curl -sS -X POST "http://localhost:4566/2015-03-31/functions/smoke/invocations" \
  -H 'Content-Type: application/json' -d '{"scenario":"all"}')"
echo "$RESPONSE"
echo "$RESPONSE" | python -c "import json,sys; r=json.load(sys.stdin); sys.exit(0 if r['statusCode']==200 and r['body']['all_pass'] else 1)"

echo "==> CI mirror: all stages green"
