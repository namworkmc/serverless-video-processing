---
review-type: verify
spine: architecture-aws-lab-2026-08-15
scope: update run binding PRD 4.7 (API Gateway ingress, AD-9) and PRD 4.8 (Terraform-managed ministack, AD-10)
reviewed: 2026-08-15
verdict: PASS-with-findings — every web-verifiable decision in the update run is current; two claims need small corrections before the gateway/Terraform stories are written from them.
---

# Verify lens review — architecture spine update (AD-9 / AD-10 / Stack)

Lens: **verify**. Every committed decision introduced or touched by the update run (memlog entries 26–32) was checked against the live web rather than training data. Change context: `.memlog.md` (last 8 entries), PRD 4.7 (FR-21..24), PRD 4.8 (FR-25/26), and the spine `Stack` table.

## Claim-by-claim verdict

### 1. ministack API Gateway v2 emulation (HTTP APIs, HTTP_PROXY, execute-api data plane) — **CONFIRMED CURRENT**

Affects: AD-9, AD-10, Stack table, PRD FR-21..26, PRD Assumption A-2.

- **API Gateway v2 HTTP APIs:** ministack v1.0.2 (2026-03-25) shipped the full control plane (CreateApi/GetApi/UpdateApi/DeleteApi, routes, integrations, stages, deployments, authorizers, tags — 32 ops) plus a data plane that routes `{apiId}.execute-api.localhost:{port}` requests to Lambda (`AWS_PROXY`) **or HTTP (`HTTP_PROXY`) backends**, with `{param}` / `{proxy+}` matching and a `$default` catch-all route.
  - Source: `github.com/ministackorg/ministack/releases/tag/v1.0.2`.
- **HTTP_PROXY integrations:** confirmed in v1.0.2, still present in the current README service table ("Lambda proxy (`AWS_PROXY`) and HTTP proxy (`HTTP_PROXY`) integrations; data plane via `{apiId}.execute-api.localhost`"); v1.3.20 (2026-04-29) added HTTP proxy parameter mapping for HTTP/HTTP_PROXY integrations. Also evidenced directly in the v1.3.6 dispatcher code (`_invoke_http_proxy` for `integration_type == "HTTP_PROXY"`).
  - Sources: `github.com/ministackorg/ministack` README, `releases/tag/v1.3.20`.
- **execute-api data plane:** two forms are supported. Host-based `{apiId}.execute-api.localhost:4566/{stage}/{path}` (since v1.0.2) and **path-based `http://localhost:4566/_aws/execute-api/{apiId}/{stage}/{path}`** (added v1.3.6, 2026-04-20 — exactly the form PRD A-2 names). Path-based form needs **no DNS/Host override**, which is what makes Bruno → gateway work without hosts-file tricks.
  - Sources: `releases/tag/v1.3.6`, README, `ministack.org`.

**Condition attached:** the path-based data plane (`/_aws/execute-api/...`) is a v1.3.6+ feature. The spine's "pin an image tag in compose" rule is therefore not just good hygiene — the **pinned tag must be ≥ 1.3.6** (current line is 1.4.x, e.g. `1.4.13`) or the Bruno invoke-URL pattern assumed in PRD A-2 and the memlog question (line 31) breaks. This is worth stating in the gateway Terraform story.

### 2. Terraform AWS provider against localhost:4566 (endpoints{} + s3_use_path_style + dummy creds + skip-validation) — **CONFIRMED CURRENT**

Affects: AD-10, Stack table, PRD FR-25/26.

- The exact pattern committed in AD-10 (`endpoints{}` overrides to `http://localhost:4566`, `s3_use_path_style=true`, dummy `access_key`/`secret_key`, `skip_credentials_validation`, `skip_metadata_api_check`) is the current, official hashicorp/aws pattern for local AWS-compatible emulators.
  - Source: `registry.terraform.io/providers/hashicorp/aws/latest/docs/guides/custom-service-endpoints` → "Connecting to Local AWS Compatible Solutions" (LocalStack example uses literally the same block incl. `skip_requesting_account_id`, `s3_use_path_style`, endpoints at `localhost:4566`).
- ministack documents the identical provider block for its own `localhost:4566` endpoint, including the `apigatewayv2 = "http://localhost:4566"` endpoint key the gateway config needs.
  - Source: `ministack.org/docs.html` (Terraform provider snippet).
- No sign of the `endpoints{}`-style being deprecated in favor of anything else in the current provider docs. Confirmed current.

### 3. Stack table versions — **CONFIRMED CURRENT, one flagged item**

- **Spring Boot 4.1.0** — confirmed current: released 2026-06-10; Java baseline 17+, so Java 21 is supported. OSS support through ~Jul 2027, so not near-EOL. Sources: `spring.io/blog/2026/06/10/spring-boot-4`, `github.com/spring-projects/spring-boot/releases`, `endoflife.date/spring-boot`.
- **Spring Cloud AWS 4.1.0 (BOM)** — confirmed current: released 2026-07-21/22; `io.awspring.cloud:spring-cloud-aws-dependencies:4.1.0` on Maven Central. The spine's parenthetical **"4.x has no RDS auto-config" is correct**: awspring's own matrix shows RDS ✅ in 2.x → ❌ in 3.x and 4.x (RDS was dropped from the 3.0 JDBC module). The "confirm 4.1.0 × Boot 4.1.0 pairing at first build" hedge is appropriate and endorsed — SCAWS 4.1.0 is built against Spring Cloud 5.0.2, so the pairing is almost certainly fine, but it was released after Boot 4.1.0 and the build is the only true proof. Sources: `github.com/awspring/spring-cloud-aws/releases/tag/v4.1.0`, Maven Central POM, `awspring.io/what-is-spring-cloud-aws`.
- **Spring gRPC 1.1.0 via `spring-boot-starter-grpc-server` / `spring-boot-starter-grpc-client` (Boot 4.1-managed; grpc-java 1.80.0)** — confirmed current. Spring gRPC 1.1.0 (2026-06-10) is the release whose "main change is the migration of autoconfiguration to Spring Boot 4.1.0", and Boot 4.1.0 ships `spring-boot-starter-grpc-server`/`-client` that manage grpc-netty/grpc-stub **1.80.0** (verified in the `spring-boot-starter-grpc-client:4.1.0` POM). Sources: `github.com/spring-projects/spring-grpc/releases/tag/v1.1.0`, `central.sonatype.com/.../spring-boot-starter-grpc-client@4.1.0`.
- **Java 21** — confirmed supported baseline for Boot 4.1 (17+); 21 is a current LTS. Not stale.
- **⚠ Protobuf codegen: `io.github.ascopes:protobuf-maven-plugin` "(versionless, Boot-managed)"** — **flagged, not confirmed.** The plugin is real (current release line 5.x, e.g. 5.1.8), but the plugin's own maintainer states Spring Boot does **not** manage its version in the Boot parent POM and omitting the version is "erroneous" — the plugin version must be pinned explicitly in the POM or parent `pluginManagement`. Boot's dependency management manages *dependencies*, not third-party build plugins. **Correction:** pin an explicit plugin version (e.g. latest 5.x) in the parent pom; drop the "Boot-managed" framing. Source: `github.com/ascopes/protobuf-maven-plugin/discussions/671`.

### 4. "Same .tf targets real AWS by swapping only the provider config" — **CONFIRMED IN DIRECTION, OVERSTATED IN THE SPECIFIC**

Affects: AD-10 (last sentence), Stack table, PRD FR-25/26 "Out of Scope".

- The provider-swap-only idea is the standard, documented LocalStack/miniStack pattern (hashicorp guide, ministack docs) and is directionally sound for S3/SQS resources: bucket/queue declarations are emulator-agnostic, and the path-style/credentials/skip-validation toggles all live in the provider block.
- **However, "only the provider config swaps, the resource declarations are unchanged" is overstated for the gateway integrations.** The HTTP_PROXY `aws_apigatewayv2_integration` declarations will embed `IntegrationUri` targets like `http://localhost:8080` / `host.docker.internal:8080`. Those are **resource-declaration content, not provider config**, and on real AWS they cannot reach a localhost service — real AWS needs a public/private endpoint (or a VPC link), and the integration URLs are exactly where that difference lands. So: provider-only swap holds for S3/SQS, but the **API Gateway integration targets will need edits** when the infra phase runs.
- Secondary nuance: any ministack-specific attributes (e.g. the `ms-custom-id` tag for predictable API ids, `MINISTACK_*` env) must not leak into shared resource declarations if "unchanged" is to hold — this is a "keep the .tf portable" constraint worth writing into the Terraform story rather than a defect in the spine.
- **Verdict:** the claim is confirmed as the intended pattern and is safe for the lab's scope; it should be softened to "provider config swaps, plus any emulator-bound values inside gateway integration attributes" so the infra-phase handoff isn't a surprise.

## Findings summary

| # | Severity | Area (AD / section) | Finding |
| --- | --- | --- | --- |
| 1 | medium | Stack — Protobuf codegen row | `protobuf-maven-plugin` is **not** Boot-managed/versionless; Boot does not manage third-party plugin versions. Pin an explicit 5.x plugin version in the parent pom. |
| 2 | medium | AD-10 (last sentence) | "Only the provider config swaps" is overstated for API Gateway HTTP_PROXY `IntegrationUri` (localhost targets live in resource declarations, not provider config) and for any ministack-specific attributes. Soften the claim; keep the .tf portable. |
| 3 | low | Stack — ministack row + AD-10 / PRD A-2 | Path-based data plane `/_aws/execute-api/...` is a ministack v1.3.6+ feature; the image tag pin must be ≥ 1.3.6 (current 1.4.x) or the assumed Bruno invoke-URL pattern breaks. State the floor version in the gateway story. |
| 4 | low | Stack — SCAWS row | "4.x has no RDS auto-config" confirmed correct; "confirm 4.1.0 × Boot 4.1.0 at first build" hedge is the right call (SCAWS 4.1.0 built on Spring Cloud 5.0.2). No change. |

## What was verified current (no action)

- ministack API Gateway v2: HTTP API control plane + HTTP_PROXY integrations + execute-api data plane (host-based and path-based) — **confirmed** (v1.0.2, v1.3.6, v1.3.20, README, ministack.org).
- Terraform AWS provider vs `localhost:4566`: `endpoints{}` + `s3_use_path_style` + dummy creds + `skip_credentials_validation`/`skip_metadata_api_check` — **confirmed** (hashicorp/aws custom-service-endpoints guide; ministack docs).
- Spring Boot 4.1.0, Spring Cloud AWS 4.1.0, Spring gRPC 1.1.0 (Boot 4.1-managed grpc-java 1.80.0), Java 21 — **confirmed current**; no stale entries.
- ministack image tag pinning rule — sound practice (the project's own changelog documents a `:latest` tagging incident, v1.3.18).

## Bottom line

The update run's decisions (AD-9, AD-10, memlog entries 26–32) are built on facts that all check out against current, primary sources. The two medium findings are small corrections to wording and POM hygiene, not architectural reversals. No committed decision needs to be revoked; findings 1 and 2 should be folded into the gateway/Terraform stories before they are written.
