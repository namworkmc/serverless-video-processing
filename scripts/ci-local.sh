#!/usr/bin/env bash
# Local mirror of the GitHub Actions CI pipeline (.github/workflows/ci.yml).
# Runs the same commands, in the same order, so a green run here predicts
# a green run in CI. Requires: uv, terraform, docker (for the integration stage),
# gitleaks (for the secrets stage — `scoop install gitleaks`).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> [1/5] secrets-scan (gitleaks, full history)"
command -v gitleaks >/dev/null || { echo "gitleaks not installed — run: scoop install gitleaks"; exit 1; }
gitleaks detect --no-banner --config .gitleaks.toml

echo "==> [2/5] lint"
uv run --with ruff ruff check lambdas/ --select E,F
(cd terraform && terraform fmt -check -recursive)

echo "==> [3/5] unit-test"
uv run --with 'pytest>=8.0' pytest lambdas/ -q

echo "==> [4/5] terraform-validate"
(cd terraform && terraform init -backend=false -input=false >/dev/null && terraform validate)

echo "==> [5/5] integration (requires Docker; reuses running floci if healthy)"
# Pin the compose project name: it defaults to the directory name, so a
# git-worktree checkout would otherwise try to start a SECOND floci on the
# same port instead of reusing the healthy one.
COMPOSE_PROJECT_NAME=serverless-video-processing docker compose up -d --wait
(cd terraform && terraform init -input=false >/dev/null && terraform apply -auto-approve)
GATEWAY_BASE_URL="$(cd terraform && terraform output -raw gateway_base_url)" \
  uv run --with 'pytest>=8.0' --with requests --with boto3 \
  pytest tests/integration/ -v

echo "==> CI mirror: all stages green"
