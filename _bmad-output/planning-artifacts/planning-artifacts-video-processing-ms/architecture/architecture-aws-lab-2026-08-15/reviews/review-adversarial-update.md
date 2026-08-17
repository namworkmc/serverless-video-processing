---
type: architecture-review
scope: ARCHITECTURE-SPINE.md (10 ADs, update run: AD-9, AD-10, AD-6 amendment)
method: adversarial construction — two units one level down, each obeying every AD to the letter, built incompatibly
sources:
  - ARCHITECTURE-SPINE.md
  - .memlog.md (last 8 entries)
  - prd.md §4.7 FR-21..24, §4.8 FR-25/26
verdict: FAIL — the update introduces two single-source-of-truth illusions (AD-6 names, AD-10 "no drift") with no enforcement hook, and two path/URL contracts (route-vs-facade, stage/invoke-URL) are left with two owners, so compliant units can and will build end-to-end-broken systems.
---

# Adversarial Review — updated spine (AD-9, AD-10, AD-6 amendment)

Method: for each hole, two units one level down (Terraform config ↔ a service, or gateway config ↔ a service facade, or gateway config ↔ the client) are constructed so that each satisfies the spine as written and still produces a system that cannot interoperate. A hole is any point where the spine leaves a shared contract to be re-derived independently by two units.

---

## F1 — CRITICAL — AD-6 "Names MUST match" is documentation, not enforcement. Terraform↔JVM name drift is uncaught by any mechanism.

- **Hits:** AD-6 (Names authority, spine:96), AD-10, Consistency Conventions (spine:155), PRD FR-3.
- **Constructed pair:**
  - *Unit A (Terraform story):* declares `aws_s3_bucket.video_uploads` named `video-uploads` and `aws_sqs_queue.video_uploaded` named `video-uploaded`. Fully AD-10/AD-6 compliant — names are declared in Terraform, nothing out-of-band.
  - *Unit B (upload-service story):* compiles against `Names.VIDEO_UPLOADS = "video-uploads"`, never string-types inline. Fully FR-3/AD-6 compliant.
  - Both green in isolation. A sanctioned AD-6 rename (change the name in Terraform — that is *exactly* the documented authority) in Unit A only, `Names` untouched → Unit B's `PutObject` hits a nonexistent bucket (`NoSuchBucket`), its SQS publisher/consumer targets a dead queue. Nothing fails at compile, boot, or unit-test time.
- **Why the AD fails:** AD-6 says the names *must* match but provides no observer that detects a mismatch. There is no `terraform output` fed into config, no build/startup assertion, no test comparing `Names` to the applied names. The rule prevents drift only by exhortation; the moment two units change independently, drift is silent and the failure surfaces at runtime in the *other* unit.
- **Fix (tighten AD-6):** add an enforcement clause — Terraform exposes names via `locals`/`output`, and a verification step (a unit-test or a `terraform output` ↔ `Names` comparison run in the verification story) MUST fail when they diverge; a rename is one coordinated change (Terraform + `Names` + the assertion in the same commit). If no enforcement is wanted, AD-6's "prevents drift" claim must be downgraded to "documents the single source".

---

## F2 — CRITICAL — the client path has two owners: the Terraform route path and the service facade path. AD-6 and AD-9 each claim a piece and never compose them.

- **Hits:** AD-6 (route paths declared in Terraform, spine:96), AD-9 (spine:120-124), AD-10, Consistency Conventions (facade paths `/api/videos/*` in code, spine:156).
- **Constructed pair:**
  - *Unit A (gateway story, Terraform):* route `POST /upload`, HTTP_PROXY integration to `http://upload-service:8080`, path passed through unchanged. Route path is declared in Terraform — AD-6/AD-10 compliant.
  - *Unit B (upload-service story):* facade `@PostMapping("/api/videos/upload")` — the consistency table's own convention for facade paths, AD-1/AD-9 compliant.
  - Gateway forwards `/upload`; the service maps `/api/videos/upload` → 404 on every real call. Unit A proves its route exists (`terraform apply` + describe), Unit B proves its facade works (local curl), yet the composed system is broken.
- **Why the AD fails:** AD-6 declares the *route* path in Terraform as authoritative; the code owns the *facade* path. Two path namespaces, no rule saying they must be identical, whether the route is a short alias needing gateway rewrite, or how the mapping table is stored. Each unit can truthfully claim compliance.
- **Fix (tighten AD-9):** pin one contract — in the lab the gateway route path MUST equal the facade's full client path (`/api/videos/*`), with no path rewriting at the edge; the spine (or Terraform) carries the single authoritative path table and the facade `@RequestMapping` must mirror it (same assertion mechanism as F1). FR-22's "routes to the correct facade" becomes testable only with an explicit path-equality rule.

---

## F3 — HIGH — stage name and invoke-URL shape are unpinned; the client and the Terraform config cannot agree on a URL. (The memlog concedes this.)

- **Hits:** AD-9 (port table "client HTTP via execute-api path", spine:171), AD-10 (binds routes/integrations/stage, spine:128), PRD A-2, memlog:31.
- **Constructed pair:**
  - *Unit A (gateway story):* creates `aws_apigatewayv2_stage` named `dev` (or relies on `$default`), with or without an explicit deployment resource.
  - *Unit B (client/Bruno story):* base URL `http://localhost:4566/_aws/execute-api/{apiId}/{stage}/{path}` using stage `$default`, or the host-shape `{apiId}.execute-api.localhost:4566/{stage}/{path}`.
  - Stage name mismatch → every call 404s with `{"message":"Not Found"}`. Host-shape mismatch (localstack/ministack data-plane path `/_aws/execute-api/...` vs `{apiId}.execute-api.<host>:4566/...`) → nothing routes at all. Both units individually demonstrate their half works.
- **Why the AD fails:** AD-10 binds "stage" as a resource but never fixes the client-visible contract: the stage name, whether the stage is in the path, the exact host shape for the ministack data plane, or how `apiId` flows to the client (a `terraform output`?). The memlog records this as open ("to be pinned in the gateway Terraform story") — i.e. the spine deliberately leaves the client-facing URL to the exact story that is built independently.
- **Fix (tighten AD-10):** the spine must pin the invoke-URL contract now: exact host/path shape for the ministack emulation, the stage name (and that it appears in the path), and that `apiId`+stage are exposed via `terraform output` and consumed by the client config/README. Add "route exists without a stage/deployment" to the forbidden list: AD-10 should require stage + deployment and a URL that the client story can derive from outputs alone.

---

## F4 — HIGH — bring-up and rebuild ordering is unspecified: who runs `terraform apply`, and must services restart after destroy/re-apply?

- **Hits:** AD-10 (binds "the setup/teardown procedure", spine:128, but the procedure is never stated), PRD FR-25 ("fresh ministack fully brought up by terraform apply alone").
- **Constructed pair:**
  - *Unit A (infra story):* `terraform apply` against a running ministack creates buckets, queues, gateway. Green on its own.
  - *Unit B (service story):* processing-service boots and creates its SQS listener / S3 client against `localhost:4566`. If the service starts before the queue exists (or a running service survives a `terraform destroy` + re-apply and keeps polling the old queue URL), the consumer errors forever and uploads fail with `NoSuchBucket` — each independently "verified green" in a different order.
- **Why the AD fails:** AD-10 binds a procedure it never defines. Nothing states: ministack up → `terraform apply` → services boot last; every re-apply/destroy requires a service restart (queue/bucket URLs change); verification runs on a clean `destroy + apply`; who executes `terraform apply` (a runbook, a script, a story) — which the Deferred CI/CD block silently leaves ownerless.
- **Fix (tighten AD-10):** a one-line bring-up contract in the AD (order of operations, restart-after-apply, clean-env verification) plus an explicit owner for `terraform apply` in the verification story (`mvn package` + compose-up smoke must run against a `destroy + apply` result, else a config change that only fails on a fresh apply ships green).

---

## F5 — HIGH — AD-9 asserts "responses pass through unchanged" but is silent on the request side: multipart upload + integration payload-format version are unpinned.

- **Hits:** AD-9 (pass-through clause, spine:124), FR-5 (multipart upload), FR-22.
- **Constructed pair:**
  - *Unit A (gateway story):* HTTP_PROXY integration configured with payload format version 2.0 (JSON-wrapped request/response), or 1.0 with no binary-media-types entry.
  - *Unit B (upload-service story):* facade consumes raw `multipart/form-data` (FR-5) and emits plain `{"error": ...}` bodies (AD-7).
  - Under 2.0 the multipart body is wrapped/encoded and the facade cannot parse it; a `Content-Type`/binary media-type gap mangles the upload. Unit A tests route existence, Unit B tests the facade directly — the passthrough path is the only untested seam.
- **Why the AD fails:** the pass-through promise covers responses only; the upload is a request through the same seam, and the integration payload format version (1.0 passthrough vs 2.0 envelope) and binary-media-types are exactly the knobs two configs will disagree on. This is the same class as F2/F3: the edge seam is governed from one side only.
- **Fix (tighten AD-9):** pin payload format version (1.0, passthrough) and the binary media type list for the upload route in Terraform, and require a real multipart upload *through the gateway* as an acceptance gate in the gateway story (not a facade-local test).

---

## F6 — MEDIUM — AD-9's "only door" is non-enforcing: the carve-out blesses direct-port HTTP, and the FR-20 rebuild surface is unowned by the gateway.

- **Hits:** AD-9 carve-out (spine:124 "not exercised, not formally banned"), FR-20 (rebuild surface may be "HTTP/gRPC"), SM-1.
- **Constructed pair:**
  - *Unit A (search-service story):* adds `POST /api/videos/rebuild` on :8091 for FR-20 (explicitly allowed over HTTP).
  - *Unit B (gateway story):* routes only the three client journeys; no rebuild route.
  - To trigger a rebuild a client must call :8091 directly — the carve-out ("not exercised, not formally banned") makes that compliant, but any other unit expecting rebuild via the gateway gets 404. Worse, the carve-out means a Bruno collection built entirely against direct ports is AD-9-compliant, so SM-1's "every client call made through the API Gateway" is unverifiable-by-construction.
- **Why the AD fails:** the carve-out converts a hard rule into documentation; combined with FR-20's "HTTP surface" allowance, every new facade endpoint on a service port is automatically a second door with no gateway route and no rule saying whether it must be one.
- **Fix (tighten AD-9):** decide the rebuild surface's fate — if client-facing, AD-9 must add it to the gateway route set; if tooling-only, forbid an HTTP surface (gRPC/admin only). And close or explicitly scope the carve-out so "client-facing" has a crisp, enumerated boundary.

---

## F7 — MEDIUM — AD-10's "prevents drift" claim is true-by-construction and covers none of the drift that actually bites.

- **Hits:** AD-10 (spine:129 prevents clause), AD-6, AD-9.
- **Argument:** `terraform apply` makes actual == declared by construction; the only thing AD-10 genuinely prevents is out-of-band creation (which FR-26 already forbids). The real drift — Terraform name vs `Names` (F1), Terraform route path vs facade path (F2), Terraform stage vs client URL (F3) — all lives on the code↔infra boundary that Terraform cannot observe. The AD's prevents-clause reads as coverage but is coverage of nothing, inviting builders to trust it.
- **Fix:** re-scope AD-10's prevents-clause to "no out-of-band resource creation / reproducible apply" and explicitly state that cross-boundary consistency (names, paths, URL) is NOT provided by Terraform and is delegated to AD-6/AD-9 enforcement.

---

## F8 — LOW — AD-7 still governs an SSE surface that no longer exists as a client surface.

- **Hits:** AD-7 (SSE error/close-frame sentence, spine:112), vs the SSE removal decision (memlog, spine:268).
- **Pair:** a builder implementing SSE error framing per AD-7 is doing governed work on a surface the seed removed and Deferred; another builder implementing the history query (the actual surface) ignores AD-7's SSE half. Both comply; the AD-7 SSE text is dead weight that invites wasted effort.
- **Fix:** mark the AD-7 SSE sentence Deferred alongside the live-SSE deferral.

---

## F9 — LOW — metadata:8090 and processing:8081 web ports have no defined purpose; "HTTP controller and/or @GrpcService" (AD-1) leaves their surface open.

- **Hits:** AD-1, AD-9 (client-facing set is only upload/notification/search), Normative port table (spine:159-171).
- **Pair:** one builder adds an HTTP controller to metadata:8090 (AD-1 permits; it's an HTTP surface on a service port → AD-9 carve-out applies); another treats those ports as actuator-only. No hard break, but the client-facing set's boundary is fuzzy.
- **Fix:** state that non-client services expose actuator/health on their web port only and hold no HTTP facade — or extend AD-9's enumerated client surface.

---

## F10 — LOW — "the same .tf targets real AWS, only the provider config swaps" is asserted, not demonstrated.

- **Hits:** AD-10 (spine:130), PRD FR-25/FR-26, §4.8.
- **Argument:** S3 bucket names are globally unique on real AWS (`video-uploads` will collide); local state + hardcoded `s3_use_path_style=true`/region literals and `force_destroy` semantics differ; `terraform output` and name-uniqueness are not "resource declarations" that survive unchanged. The claim is an unverified future-phase assertion presented as a guarantee.
- **Fix:** qualify it — names will need env-scoped prefixes, and the real-AWS swap is a constraint to re-verify in the infra phase, not a property of this config.

---

## Cross-AD conflict summary (tension, not a pair)

- **AD-6 vs AD-9:** AD-6 makes Terraform the authority for *route paths*; AD-9/consistency makes service code the authority for *facade paths*. Both cannot be "the place where the client path is decided" — that is the root of F2 and should be resolved in the same tightening.
- **AD-9 vs PRD FR-21:** PRD says "no client call goes directly to a service"; the spine softens it to "not formally banned." The spine now contradicts its binding PRD — either the PRD is over-strong or the spine under-states; reconcile so both units read the same rule.
- **AD-10 vs Deferred CI/CD:** the spine defers CI/CD and assigns verification to `mvn package` + compose smoke, but the new infra layer (terraform apply, gateway URL) has no owner in that verification chain (F4).

---

## Consolidated fix list (new/tightened ADs)

1. **AD-6**: enforcement hook for Names↔Terraform match (test or `terraform output` comparison); rename = coordinated change + assertion. (F1)
2. **AD-9**: route path MUST equal facade client path, no edge rewriting; single authoritative path table; payload-format/binary pinned; multipart-through-gateway acceptance gate; enumerate the client-facing surface and close the carve-out or scope it; reconcile with PRD FR-21 wording. (F2, F5, F6, F9)
3. **AD-10**: pin stage name + invoke-URL host/path shape via `terraform output`; forbid route-without-stage/deployment; state bring-up order and restart-after-re-apply; assign `terraform apply` an owner in the verification story; re-scope the prevents-clause; qualify the real-AWS swap claim. (F3, F4, F7, F10)
4. **AD-7**: Defer the SSE sentence with the live-SSE deferral. (F8)

**Verdict: FAIL.** The update's own success test (two independent units, both obedient, interoperate) fails for at least the five seams above; the spine should not proceed to story derivation until AD-6/AD-9/AD-10 carry enforcement and single-owner contracts for names, client paths, and the invoke URL.
