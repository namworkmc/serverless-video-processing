# Review: Adversarial (two-compliant-builders lens)

**Verdict: PASS after 3 holes closed.** Constructed builder pairs obeying every AD to the letter; three pairs still built incompatibly. All three fixed in the spine.

## Attack scenarios

### A1 — ASL author vs shared-layer author: two encodings of the transition table [HIGH — fixed]
- **Units:** the Terraform author writing the state machine ASL (AD-4) and the Python author writing `lambdas/_shared/` (AD-2).
- **Divergence:** AD-2 says the shared layer is "the only code that knows the legal-transition table," but AD-4's ASL necessarily hardcodes condition pairs (`#s = UPLOADED` → `PROCESSING`, etc.) inline. Both obey their ADs; if the transition table ever changes, one author updates the layer and the other forgets the ASL — silent divergence between the two enforcement points.
- **Fix (AD-4):** the ASL's condition pairs are declared the authoritative encoding for the processing leg; they MUST mirror the shared layer's table, and a transition-table change is one coordinated spine-level change (ASL + shared layer together).

### A2 — ASL author vs publisher author: who shapes the video.processed envelope? [HIGH — fixed]
- **Units:** the ASL author (who passes parameters to the publish task) and the publisher-Lambda author (who calls PutEvents).
- **Divergence:** after the putEvents workaround, the envelope (eventId, schemaVersion, detail) could be assembled partly in ASL Parameters and partly in the publisher — two builders split the shape, one omits schemaVersion, the other derives eventId differently.
- **Fix (AD-4 + AD-6):** the event-publisher Lambda is the SOLE constructor of the `video.processed` envelope; the ASL passes only the domain payload (videoId, status, keys); eventId derivation and schemaVersion live only in the publisher (via the shared layer).

### A3 — upload-handler author vs gateway author: multipart body format [MEDIUM — fixed]
- **Units:** the upload-handler author and the Terraform gateway author.
- **Divergence:** spike-verified that API GW v2 delivers the multipart body RAW (`isBase64Encoded: false`) — but nothing in the spine told the upload-handler author that. One builder assumes base64 (the common API GW v1 assumption), the other parses raw; the upload journey breaks at integration time.
- **Fix (Consistency Conventions):** explicit note — the gateway delivers multipart bodies raw; the upload handler parses multipart itself (stdlib `email`/`cgi` or a small pinned dependency).

## Pairs checked that do NOT diverge (ADs hold)

- history-consumer vs search-consumer on redelivery: both key dedupe on eventId per AD-6 — compatible.
- search-consumer vs search-rebuild on search-index: both write the same shape keyed by videoId; rebuild reads metadata (truth) and is idempotent — last-writer-wins is safe.
- shim vs state machine on duplicate triggers: deterministic execution name (AD-5) makes the second StartExecution a dedupe — compatible.
- any function vs the metadata table: conditional writes (AD-2) reject illegal transitions regardless of caller — compatible.
