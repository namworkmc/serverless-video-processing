# Addendum — Video processing platform PRD

Companion to `prd.md`. Holds options-considered rationale and technical detail that does not belong in the PRD's requirements narrative. Downstream consumers: architecture spine revision, epics/stories, deployment envelope.

## Options Considered

### API Gateway: v2 HTTP API vs v1 REST API

- **Chosen:** API Gateway v2 (HTTP API).
- **Why:** modern, simpler model; HTTP proxy (`HTTP_PROXY`) integrations map 1:1 onto the services' plain HTTP facades; no auth in the lab makes v1's usage plans/API keys/authorizers dead weight. Lightest real-AWS footprint.
- **Rejected:** v1 REST API — resource tree + models + usage-plan vocabulary adds ceremony with zero benefit for an open, proxy-to-Spring-Boot lab.
- **Ministack support:** both v1 and v2 emulated; v2 chosen as above. Data plane reachable via path-based form `/_aws/execute-api/{apiId}/{stage}/{path}` (no `*.execute-api.localhost` DNS/Host override), which works with strict clients like Bruno.

### Provisioning: Terraform vs aws cli vs control-plane calls

- **Chosen:** Terraform, as the hard requirement (FR-25/26).
- **Why:** declarative, repeatable, reviewable; the same `.tf` targets real AWS later — aligns with the lab's teaching goal (explain IaC, not click-driving a CLI). Eliminates ad-hoc control-plane calls and out-of-band resource creation.
- **Rejected:** `aws cli` — imperative, unrepeatable, and against the user's explicit rule; raw ministack control-plane calls (SDK-style) — same problem, plus no artifact to review.

### Database per record store: PostgreSQL everywhere vs polyglot vs AWS-managed

Initial design left the record-store engine implicit (H2 dev only, "future RDS"). Architecture-team discussion (3 options, requirement-driven, not anchored on the existing H2 choice):

- **Chosen — PostgreSQL everywhere (spine AD-11):** one engine for all three record stores. metadata video record → Postgres `video` table; notification history → Postgres append table keyed on `eventId`; search index → Postgres table with title-substring via `ILIKE`. **Real AWS: Amazon Aurora, PostgreSQL-compatible** (user preference — Aurora over regular RDS), reached through the same `spring.datasource.*` (SCAWS 4.x has no RDS/Aurora module). Dev: each record-owning service declares its own `postgres` instance in its own repository (`metadata` for metadata-service, `notification` for notification-service, `search` for search-service) — the root `docker-compose.yml` holds only the agentic engineering workspace (ministack + redis) and never a service's database.
- **Why:** the requirement set is small and relational-flavored — ACID state-machine transitions in metadata need a real DB; the *only* search requirement is title-substring (PRD §4.6; richer search explicitly deferred), which Postgres `ILIKE` serves directly. One engine = one dialect to learn/teach, minimal lab footprint, and a boring-tech match to the teaching goal (SM-C1: no optimization polish).
- **Rejected — PostgreSQL + OpenSearch:** a real full-text engine over-builds the explicitly deferred richer-search scope; OpenSearch is weak at literal substring (`%query%`) matching, is memory-hungry on a laptop, and adds a second dialect/surface for zero requirement win.
- **Rejected — AWS-managed DynamoDB + OpenSearch:** most AWS-native but the strict status state machine and idempotent create become conditional-write gymnastics; ministack's DynamoDB emulation is weaker; moves off the already-seeded JPA path; heaviest lab footprint for no requirement gain.
- **Kept from the spine:** single-writer ownership (AD-2) is unchanged — one DB per service, no service reads another's database. Title search stays a Postgres table (`ILIKE`), not a separate search engine.

## Technical Facts for Downstream Work

- **Ministack endpoint:** `http://localhost:4566` (compose service `ministack`). AWS provider in Terraform points at this endpoint with dummy credentials; region `us-east-1`.
- **Record-store databases:** each record-owning service declares its own `postgres` container in a `docker-compose.yml` inside its own repository — metadata-service: `video-processing-ms/metadata-service/docker-compose.yml` (`postgres` service, localhost:5432, DB `metadata`); notification-service and search-service get the same shape (`notification`, `search`) when those repos exist. The root `docker-compose.yml` never holds a service's database — it stands up only the agentic engineering workspace (ministack + its backing redis). Services connect via Spring Boot `spring.datasource.*`; real AWS swaps the URL/credentials to Aurora PostgreSQL-compatible.
- **API Gateway v2 on ministack:** control plane via standard `apigatewayv2` operations; data plane via path-based `/_aws/execute-api/{apiId}/{stage}/{path}`; `HTTP_PROXY` integration targets a service's HTTP facade. **Stage is pinned to `dev` (named, with a deployment) per spine AD-10** — `terraform output` exposes the invoke URL to the client. (An `ms-custom-id` tag could pin `apiId` for stable URLs across runs, but the spine's `terraform output` handoff is the operative contract.)
- **Provisioned resources (all Terraform):** `video-uploads` bucket, `video-processed` bucket, `video-uploaded` SQS queue, `video-processed` SQS queue, API Gateway v2 API + routes + integrations + `dev` stage.
- **Client-facing HTTP routes (through the gateway):** upload → upload-service HTTP facade; status history → notification-service HTTP facade; search → search-service HTTP facade. Route paths must equal the facade client paths (`/api/videos/*`, no edge rewriting) per spine AD-9's authoritative path table.
- **Strictly internal:** all gRPC (metadata-service, search-service) stays service-to-service; never gateway-exposed. Gateway reaches host facades via `host.docker.internal:<port>` (services as host JVMs).

## Spine Binding Summary (resolved 2026-08-15)

The architecture spine (`planning-artifacts/architecture/architecture-aws-lab-2026-08-15/ARCHITECTURE-SPINE.md`) was revised to bind the API Gateway as single client ingress (**AD-9**), Terraform-managed ministack infrastructure (**AD-10**), and PostgreSQL for every record store with Aurora PostgreSQL-compatible on real AWS (**AD-11**); AD-6 was amended so names authority lives in Terraform with a `Names`-vs-`terraform output` enforcement hook. Spine details that now govern this PRD and win on conflict:

- Client-facing surface enumerated to exactly upload / status history / search; gateway route path == facade path with **no edge rewriting** (authoritative path table in AD-9).
- Gateway reaches host-local service facades via `host.docker.internal:<port>` (services as host JVMs; the root compose runs ministack + redis only — each service's database runs from a compose file in that service's repo, AD-11).
- Upload integration payload format pinned to **1.0 passthrough** with `multipart/form-data` + binary media types; a multipart upload through the gateway is an acceptance gate.
- Search rebuild trigger is **gRPC/admin only** (no HTTP surface) — reconciles FR-20.
- Gateway stage pinned to **`dev`** (named, with a deployment); the invoke URL flows to client config via `terraform output` (see the provisioned-resources note above — the `ms-custom-id` tag is optional, not the operative contract).
- Bring-up order fixed: ministack up → `terraform apply` → services boot last; verification runs on a clean `destroy` + `apply`; services surviving a destroy+re-apply MUST be restarted (queue/bucket URLs change).
