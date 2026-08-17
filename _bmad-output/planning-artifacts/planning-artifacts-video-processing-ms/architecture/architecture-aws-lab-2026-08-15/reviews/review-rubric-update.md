# Rubric Review — Architecture Spine update run (AD-9, AD-10, AD-6 amendment)

- **Reviewer role:** independent rubric walker (no code written/edited)
- **Scope of review:** the update binding PRD 4.7 (FR-21..24 → AD-9) and PRD 4.8 (FR-25/26 → AD-10), the AD-6 names-authority amendment, SSE removal from the client-facing seed, and the direct-HTTP carve-out wording.
- **Artifacts reviewed:** `ARCHITECTURE-SPINE.md` (274 lines), `.memlog.md` (last 8 entries), `prd.md` (§4.7/§4.8, plus FR-1..20 for consistency).
- **Date:** 2026-08-15

## Feature-binding matrix (PRD → spine)

| PRD | Feature | Bound where | Coverage |
| --- | --- | --- | --- |
| 4.7 | FR-21 Single client ingress | AD-9 | Full — gateway as only door; carve-out keeps PRD wording verbatim |
| 4.7 | FR-22 Route client HTTP to services | AD-9 | Full — route-by-path, pass-through unchanged |
| 4.7 | FR-23 gRPC stays internal | AD-9 | Full — never gateway-exposed; direct gRPC is debugging-only |
| 4.7 | FR-24 No auth on gateway | AD-9 | Full — explicitly "no auth on the gateway (lab)" |
| 4.8 | FR-25 All ministack infra via Terraform | AD-10 | Full — buckets, queues, gateway API/routes/integrations/stage |
| 4.8 | FR-26 No aws cli in setup | AD-10 | Full — setup/teardown docs contain no `aws` CLI; destroy/re-apply rebuilds |

All six gated features are bound. PRD §4.7/§4.8 "Out of Scope" items map to the spine's Deferred list (gateway auth/rate limiting/custom domains, Terraform remote state/modules/workspaces). The PRD's "gated prerequisite" note (line 15) is now satisfied — a story may derive from these features.

## Checklist assessment

### 1. Real divergence points for the level below (stories) — and none missed

The update fixes the two headline divergence points: single client ingress (AD-9) and IaC/no-`aws cli` (AD-10). The AD-6 amendment correctly removes the split names authority (Names.java was the old single source; Terraform now wins, Names mirrors). SSE removal aligns the seed with PRD 4.5 out-of-scope.

**Gap:** one real divergence point is *not* fixed — how the ministack API Gateway container reaches the host-local service HTTP facades (see Finding 1, High). It is a story-level divergence that the update introduces and does not resolve. Also, the gateway route-path → facade mapping is not enumerated (Finding 2).

### 2. Every AD's Rule is enforceable and prevents its stated divergence

- AD-9: enforceable at the exercised-path level (Bruno targets the gateway; verify by test surface). The carve-out ("not part of the exercised path — not exercised, not formally banned") is deliberately softer than a ban, but it is PRD wording kept verbatim (user decision), so this is a faithful bind, not an invention. The statement "never `localhost:8080/8081/8082/8090/8091` directly" is partially unenforceable as written — direct HTTP to a service port is *not formally banned* — so the Rule's first sentence overstates what the carve-out allows. Consistent with PRD, but the Rule text and the carve-out should be read together; a story could pick either reading.
- AD-10: enforceable — resource inventory is finite, setup/teardown docs are reviewable for `aws` CLI, "local state only" is checkable.
- AD-6 (amended): the "MUST match" between Terraform and `Names` has **no enforcement or verification mechanism** (Finding 3). It is a convention with runtime-detected drift, which is weaker than the rest of the spine's rules.
- All other ADs (1–5, 7, 8) unchanged by this run and remain enforceable.

### 3. Nothing under Deferred could let two units diverge

Deferred items that touch the new features are bounded: API Gateway beyond minimal lab (auth/rate limiting/custom domains/WebSocket) and Terraform beyond minimal lab (remote state/modules/workspaces) both carry revisit conditions and name the pinning location (gateway Terraform story for the invoke-URL pattern). The reconciliation/outbox/DLUQ items keep their AD-2/AD-4/AD-8 constraints. No Deferred item hands two stories an ungoverned choice **except** the residual SSE wording (Finding 5), which is stale rather than divergent.

### 4. Named tech is verified-current

Mostly current and internally hedged:
- Java 21 (LTS), Spring Boot 4.1.0 (plausible for 2026; Boot 4 line), protobuf-maven-plugin (Boot-managed), eclipse-temurin:21-jre — fine.
- **Spring Cloud AWS 4.1.0 × Boot 4.1.0** — the spine itself flags "confirm pairing at first build; 4.x has no RDS auto-config". Not stale per se, but unverified.
- **Spring gRPC 1.1.0** via `spring-boot-starter-grpc-server/client`, "Boot 4.1-managed; grpc-java 1.80.0" — no staleness flag raised by the spine, but this starter family/version should be verified at first build (it is a newer, less-established surface).
- ministack "pin an image tag in compose", Terraform AWS provider pinned — correct posture.
No clearly-stale pin found; the two Boot-4-era version pairings deserve build-time confirmation, and the spine already requests one of them.

### 5. Spec-driven: PRD features FR-21..26 all bound

All bound (matrix above). No PRD 4.7/4.8 capability is left unbound. The spine's scope/binds/paradigm/seed/capability map all updated to include api-gateway and terraform.

### 6. No new AD weakens or contradicts an existing one

- AD-9 vs AD-7: consistent — "responses pass through unchanged … no remapping at the edge" preserves AD-7's table and `{"error": ...}` bodies. One small unowned edge: a *gateway-level* 404 for an unknown route (not a facade error) is not governed by AD-7's table; the gateway "is not a service" so AD-7's bind is ambiguous for gateway-invented responses. Low.
- AD-9 vs AD-1: gateway explicitly "not a service", outside the layered structure — no conflict.
- AD-9 vs AD-2's grpcurl carve-out: direct gRPC debugging surface preserved — no conflict.
- AD-10 vs AD-6: names authority consolidated in Terraform (AD-6 amended in place) — the two now agree; this is the correct move and removes the prior split.
- AD-10 vs AD-9: gateway is Terraform-provisioned in both — consistent.
- No AD was weakened or contradicted; AD-6 was amended in a direction that strengthens single-authority.

### 7. Every dimension this altitude owns is decided, deferred, or an open question

The spine has no explicit **Open Questions** section (memlog holds the invoke-URL question; the spine surfaces it via the Deferred gateway entry). Dimension inventory:
- Deployment & environments: decided (ministack now via Terraform; real AWS = infra phase) — **except the container-networking sub-dimension, which is silent (Finding 1)**.
- Infra/provider strategy: decided (AD-10, local state, AWS provider, endpoints→4566).
- Client ingress: decided (AD-9).
- Containerization: decided (1 Dockerfile per service, temurin:21-jre), but deployment topology (services on host vs in containers) is only in the memlog, not the spine — and that ambiguity is what makes Finding 1 a silent dimension.
- Observability, CI/CD, secrets, security: deferred with revisit conditions.
- One un-surfaced sub-question: whether metadata-service's HTTP facade (port 8090) is a client surface at all — it is in the normative port table but not in AD-9's client-facing list and not in the gateway routing set. Silent, low-risk, but a story could go either way (add an unexercised HTTP controller, or drop it). Low.

## Findings

### Finding 1 — [HIGH] Deployment/container-networking envelope silent: the ministack API Gateway container cannot reach host-local facades as specified

Hits: AD-9, AD-10, env envelope (checklist item 7).

The spine's operational model is "services as local JVMs on host ports + ministack in docker compose" (memlog line 11; AD-6 `localhost:4566` / `localhost` gRPC targets; compose line corrected to "ministack only"). Under that model, the ministack API Gateway runs *inside* the ministack container, and its HTTP_PROXY integrations must make outbound calls to the services' HTTP ports (`8080/8081/8082/8091`). `localhost:8080` resolved from inside the container is the container itself, not the host — so AD-9's "responses pass through unchanged" and AD-10's "routes/integrations" have no defined addressing path on the win32/Docker-Desktop setup (host networking is Linux-only; `host.docker.internal` or `extra_hosts` or a shared compose network, or containerizing the services, are all viable and none is chosen).

This is a genuine story-level divergence point introduced by the update and left **completely silent** — no AD, no Deferred entry, no open question addresses how the gateway addresses the facades. The gateway Terraform story and the compose story can each pick a different mechanism and disagree. Must be decided (or explicitly deferred with a pinned default) before stories derive.

### Finding 2 — [MEDIUM] Gateway route-path → facade mapping not enumerated

Hits: AD-9, AD-6.

AD-9 says the gateway "routes by path to the correct service HTTP facade" and AD-6 makes route paths Terraform-authoritative, but the actual path prefixes (which gateway path → which facade: upload / history / search) are not pinned anywhere in the spine. Facade paths are only loosely governed ("HTTP facade paths under `/api/videos/*`"). The gateway Terraform story and the three facade stories can disagree on the mapping and break FR-22's routing contract silently until Bruno runs. Pin the mapping (or bind it to a table in the spine, mirroring the normative port table).

### Finding 3 — [MEDIUM] AD-6 "Names mirrors Terraform and MUST match" has no enforcement/verification mechanism

Hits: AD-6 (amended).

Moving names authority to Terraform is correct, but the JVM side (`com.videolab.common.Names`) is now a hand-maintained mirror with only a convention ("MUST match") to keep it aligned. Nothing in the spine mandates a verification step (e.g. a build/test that asserts `Names` values equal the Terraform-declared names, or a smoke check at bring-up). Drift is detectable only at runtime as a queue/bucket-not-found failure. The rest of the spine is stricter than this; add a mechanical check or a named consistency test.

### Finding 4 — [LOW] AD-10 overstates the real-AWS migration as "only the provider config swaps"

Hits: AD-10.

API Gateway **integration targets** (the service endpoints the gateway routes to) are part of the *resource declarations* (`aws_api_gateway_integration`), not provider config — as is any environment-scoped naming. In real AWS those targets are not the localhost ports, so the migration is not provider-config-only. The claim is in the future infra phase, so it won't let current stories diverge, but it will mislead the infra phase if carried forward. Suggest rewording to "provider config and integration addressing".

### Finding 5 — [LOW] Residual SSE references while SSE is no longer a client surface

Hits: AD-7, Deferred.

The seed correctly dropped `N -->|SSE status| C`, but AD-7 still governs "SSE error/close frames use `event: error`" and the Deferred block still describes "in-memory SSE history" as notification's delivery mechanism. Not contradictory — SSE is now internal/in-memory only — but the wording is stale and a story could read AD-7 as binding an exposed SSE surface. Tidy the wording to "internal in-memory SSE history; no client-exposed SSE surface in this lab".

### Minor notes (not findings)

- Stack: Spring Cloud AWS 4.1.0 and Spring gRPC 1.1.0 (Boot-4-era pairings) are unverified at build time; the spine already requests confirmation for one — extend it to the gRPC starter. (Checklist item 4.)
- metadata-service HTTP facade (port 8090) is neither in AD-9's client-facing set nor in the gateway routes; its existence is an un-surfaced micro-question (gRPC-only vs HTTP+inspection). Low.

## Verdict

**Pass with conditions.** The update correctly binds all six gated PRD features (FR-21..26), amends AD-6 into a single names authority without weakening any existing AD, and aligns the seed with the PRD's SSE scope. Before stories derive, resolve Finding 1 (gateway→facade addressing / container networking — a silent operational dimension this altitude owns) and pin the route-path mapping (Finding 2); add a mechanical Terraform↔Names consistency check (Finding 3). Findings 4–5 are tidy-ups.
