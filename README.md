# Serverless Video Processing

Serverless video-processing platform (upload → transcode → status/search) running
entirely local on [floci](https://github.com/floci-io/floci), a free LocalStack-compatible
AWS emulator. Infrastructure is managed exclusively with **Terraform** — no `aws cli`
in setup/teardown.

## Stack

| Layer | Technology |
|---|---|
| Emulator | floci (`localhost:4566`, no auth token) |
| IaC | Terraform (AWS provider → `http://localhost:4566`) |
| Compute | AWS Lambda (ffmpeg transcode) — planned |
| Orchestration | Step Functions / EventBridge — planned |
| Storage | S3 + DynamoDB — planned |
| Ingress | API Gateway v2 — planned |

## Quick start

```bash
# 1. Start the emulator
docker compose up -d

# 2. Wait for health (all services "running")
curl -s http://localhost:4566/_localstack/health | python -m json.tool

# 3. Provision infrastructure
cd terraform
terraform init
terraform apply
```

Teardown: `terraform destroy`, then `docker compose down`.

## Repository layout

```
docker-compose.yaml # floci emulator
terraform/          # all AWS resources (buckets, queues, tables, lambdas, gateway)
lambdas/            # Lambda function source code (one dir per function)
_bmad-output/       # BMAD planning artifacts (PRD, architecture, epics)
```

## Status

Story 1.2 complete: shared access layer (`lambdas/_shared/`) implemented —
status state machine via DynamoDB conditional writes, deterministic UUID5
event envelopes, error mapping, env-driven boto3 clients — and verified
inside floci's real Docker runtime by the `smoke` Lambda fixture
(`terraform/smoke.tf`, incl. the `video-metadata` table). boto3 confirmed
present in the runtime image. Next: Story 1.3, the upload journey through
the gateway — see `_bmad-output/`.
