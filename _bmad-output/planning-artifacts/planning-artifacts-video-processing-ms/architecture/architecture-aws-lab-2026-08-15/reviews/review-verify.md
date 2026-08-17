# Verification Review — Architecture Spine (Stack & Config Reality Check)

- **Spine reviewed:** `ARCHITECTURE-SPINE.md` (architecture-aws-lab-2026-08-15)
- **Lens:** every committed Stack/decision claim was web-researched or reality-checked against current (Aug 2026) sources; no claim accepted from training data or the spine's own Stack table.
- **Date of review:** 2026-08-15
- **Verdict:** **PASS with caveats** — every named technology exists and the version pairing is current and coherent, but two items carry documentation gaps that should be confirmed at build time (SCAWS↔Boot 4.1 pairing, grpc starter coordinates) and one decision (ministack) rests on a very young, unpinned project. No critical or high findings.

---

## Method

Each committed stack/config decision was checked against vendor release notes, Maven Central, and official reference docs (sources below). Where the source could not confirm a claim, it is flagged rather than assumed.

---

## Critical

**None.** No named technology was found to be fabricated, renamed out of existence, or discontinued.

---

## High

**None.**

---

## Medium

### M-1 — Spring Cloud AWS 4.1.0 × Spring Boot 4.1.0 pairing is not explicitly documented
- **Spine location:** Stack table line 124 (`Spring Cloud AWS | 4.1.0 (BOM import)`); AD-6 line 75.
- **Verified:** Spring Cloud AWS 4.1.0 is a real, current release (tagged 2026-07-22; latest on the `awspring/spring-cloud-aws` releases list) on Maven Central (`io.awspring.cloud:spring-cloud-aws:4.1.0` and BOM `io.awspring.cloud:spring-cloud-aws-dependencies:4.1.0`, pom type, import scope — matches the spine's "BOM import"). The 4.x line is unambiguously the Spring Boot 4-compatible line (4.0.0 "delivers full compatibility with Spring Boot 4.x and Spring Framework 7.x"; 4.1.0 "Lift Spring versions").
- **Could not confirm:** the *specific* 4.1.0 × 4.1.0 pairing. The official SCAWS compatibility table (`awspring.io/what-is-spring-cloud-aws` and the project README) still lists `4.x.x → Spring Boot 4.0.x / Spring Cloud 2025.1.x`. There is no explicit "Spring Boot 4.1.x" row. This is very likely fine — SCAWS 4.1.0 post-dates Boot 4.1.0 (2026-06-10) and lifted Spring versions, and the Spring Cloud 2025.1 (Oakwood) train officially supports Boot 4.1.x starting 2025.1.2 — but the spine asserts the pairing as fact without a source.
- **Source:** https://github.com/awspring/spring-cloud-aws/releases/tag/v4.1.0 ; https://central.sonatype.com/artifact/io.awspring.cloud/spring-cloud-aws/4.1.0 ; https://awspring.io/what-is-spring-cloud-aws (compat table) ; https://spring.io/projects/spring-cloud/ (release-train compat, 2025.1.x → Boot 4.0.x/4.1.x)
- **Action:** confirm at first build that `spring-cloud-aws-dependencies:4.1.0` resolves cleanly under Boot 4.1.0; if SCAWS emits a Spring mismatch warning, either pin Boot 4.0.7 or bump SCAWS when the compat table is updated.

---

## Low

### L-1 — gRPC ecosystem moved into Spring Boot 4.1; spine's starter wording can mislead a builder
- **Spine location:** Stack table line 125 (`Spring gRPC (spring-grpc core) | 1.1.0 (Boot-managed; grpc-java 1.80.0, protobuf via Boot BOM)`); AD-1 line 45 (`@GrpcService`).
- **Verified:** `spring-grpc:1.1.0` exists (Maven Central, released 2026-06-09/10) and is Boot-managed — `spring-boot-dependencies:4.1.0` POM sets `<spring-grpc.version>1.1.0</spring-grpc.version>`, `<grpc-java.version>1.80.0</grpc-java.version>`, `<protobuf-java.version>4.34.2</protobuf-java.version>`. grpc-java 1.80.0 exists and is the latest release (2026-03-17). `io.github.ascopes:protobuf-maven-plugin` is Boot-managed and versionless under `spring-boot-starter-parent` (`<protobuf-maven-plugin.version>5.1.4</protobuf-maven-plugin.version>`), exactly as the spine claims; Boot 4.1 docs show a versionless POM snippet. `@GrpcService` still exists (moved to `org.springframework.grpc.server.service.GrpcService`).
- **Caveat (why low):** Boot 4.1 moved gRPC auto-configuration *into Spring Boot itself* (new `org.springframework.boot:spring-boot-starter-grpc-server` / `spring-boot-starter-grpc-client`; the old `org.springframework.grpc` starters are deprecated, and spring-grpc-core 1.1.0 arrives transitively). The spine's "spring-grpc core" is technically correct and current, but a builder may reach for the deprecated `org.springframework.grpc` starter coordinates. The spine's property `spring.grpc.client.channel.<name>.target` matches the *new* Boot 4.1 property exactly (old was `spring.grpc.client.channels.<name>.address`), so the config side is already current.
- **Source:** https://repo1.maven.org/maven2/org/springframework/boot/spring-boot-dependencies/4.1.0/spring-boot-dependencies-4.1.0.pom ; https://github.com/spring-projects/spring-grpc/releases/tag/v1.1.0 ; https://docs.spring.io/spring-boot/4.1/reference/io/grpc.html ; https://github.com/grpc/grpc-java/releases/tag/v1.80.0 ; https://github.com/ascopes/protobuf-maven-plugin
- **Action:** in the build story, use `spring-boot-starter-grpc-server`/`spring-boot-starter-grpc-client` (Boot-managed) rather than the deprecated `org.springframework.grpc` starters.

### L-2 — ministack is real but very young and unpinned
- **Spine location:** Stack table line 129; AD-6 line 75; docker-compose line 160.
- **Verified:** ministack exists and matches the spine's claims: `ministackorg/ministack` (GitHub + Docker Hub + PyPI), free/MIT, single endpoint `http://localhost:4566`, drop-in S3 and SQS (plus 40–60+ services), LocalStack-compatible, works with the AWS SDK 2 (boto3/AWS CLI/Terraform shown). The default port 4566 and S3+SQS usage match. `spring.cloud.aws.endpoint=localhost:4566` is the documented SCAWS way to point at such emulators.
- **Could not confirm:** a stable version. The project advertises 35→42→60+ services across README snapshots within weeks (rapid, possibly marketing-inflated churn), and the spine pins no image tag. This is a new, fast-moving project; localstack remains the more battle-tested option if the spine's "ministack" name was chosen casually.
- **Source:** https://github.com/ministackorg/ministack ; https://ministack.org/ ; https://hub.docker.com/r/ministackorg/ministack ; https://docs.awspring.io/spring-cloud-aws/docs/4.1.0/reference/html/index.html (§3.3 Endpoint)
- **Action:** pin a ministack image tag in `docker-compose.yml`; add a one-line note in the compose story that S3+SQS behavior may drift across ministack releases.

### L-3 — SCAWS 4.x dropped the RDS module; spine's JPA abstraction is fine but note the deletion
- **Spine location:** Stack table line 128 (`H2 file (dev) — JPA abstracts future RDS`); Deferred section line 197 (real AWS later).
- **Verified:** Spring Cloud AWS 4.x removed the RDS (and EC2, ElastiCache, CloudFormation) modules — the compat table marks them ❌ for 4.x. The spine does **not** use SCAWS RDS (it uses plain JPA + JDBC, which talks to RDS as a normal Postgres/MySQL endpoint), so the decision stands.
- **Source:** https://awspring.io/what-is-spring-cloud-aws (service matrix)
- **Action:** none required; ensure no later phase introduces `spring-cloud-aws` RDS expectations.

---

## Confirmed (reality-checked, no action)

- **Java 21 + Spring Boot 4.1.0** — Boot 4.1.0 is a real, current GA release (2026-06-10, latest GA; 4.2 expected Nov 2026). Boot 4 keeps a JDK 17 baseline, so Java 21 is fully supported (one 4.1 feature, jOOQ 3.20, even requires 21). ✓
  - Source: https://spring.io/blog/2026/06/10/spring-boot-4/ ; https://github.com/spring-projects/spring-boot/releases ; https://central.sonatype.com/artifact/org.springframework.boot/spring-boot/4.1.0
- **Spring Boot 4.1.0 dependency management** — verified directly in the `spring-boot-dependencies:4.1.0` POM: `grpc-java 1.80.0`, `spring-grpc 1.1.0`, `protobuf-java 4.34.2`, `protobuf-maven-plugin 5.1.4` (ascopes), `h2 2.4.240`, `spring-framework 7.0.8`. Every "Boot-managed" claim in the spine's Stack table is true. ✓
  - Source: https://repo1.maven.org/maven2/org/springframework/boot/spring-boot-dependencies/4.1.0/spring-boot-dependencies-4.1.0.pom
- **`spring.grpc.client.channel.<name>.target`** — matches the current Boot 4.1 gRPC client property exactly (`spring.grpc.client.channel.myservice.target=static://grpc.example.com:9090` in official docs). The spine is not stale on this. ✓
  - Source: https://docs.spring.io/spring-boot/4.1/reference/io/grpc.html ; https://dev.to/davebrown1975/grpc-moved-into-spring-boot-41-the-new-starters-the-property-renames-and-what-we-measured-1mne
- **`spring.cloud.aws.*` / `spring.cloud.aws.s3.path-style-access-enabled`** — present in the Spring Cloud AWS 4.1.0 reference docs appendix (and 4.0.0). Namespace and flag are current. ✓
  - Source: https://docs.awspring.io/spring-cloud-aws/docs/4.1.0/reference/html/appendix.html ; https://docs.awspring.io/spring-cloud-aws/docs/4.0.0/reference/html/appendix.html
- **`spring.cloud.aws.endpoint`** for emulator wiring — documented in SCAWS 4.1.0 (§3.3). ✓

---

## Summary

| Claim in spine | Status |
| --- | --- |
| Java 21 | Confirmed (Boot 4.1 supports ≥17) |
| Spring Boot 4.1.0 | Confirmed, current GA |
| Spring Cloud AWS 4.1.0 BOM | Confirmed real + Boot-4 line; 4.1.0×4.1.0 pairing only implicit (M-1) |
| spring-grpc 1.1.0 / grpc-java 1.80.0 / ascopes plugin | Confirmed and Boot-managed (L-1 starter-coordinate note) |
| ministack @ localhost:4566 (S3+SQS) | Confirmed exists; young/unpinned (L-2) |
| `spring.cloud.aws.s3.path-style-access-enabled` | Confirmed in 4.1.0 docs |
| `spring.grpc.client.channel.<name>.target` | Confirmed in Boot 4.1 docs |
