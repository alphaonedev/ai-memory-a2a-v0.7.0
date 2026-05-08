# v0.7.0 scenario deltas

This file lists S1–S51 scenarios that **may** need adaptation for the v0.7.0
schema. The scenario files themselves were copied verbatim from
`ai-memory-ai2ai-gate` v0.6.4 baseline (banner-tagged at the top of every file).

When a delta is confirmed during Round 1 dry-run, the orchestrator opens an
upstream PR against ai2ai-gate so the change lives in one place.

| Scenario | v0.6.4 behaviour | v0.7.0 expected delta | Status |
|----------|------------------|-----------------------|--------|
| S20 — mTLS happy-path     | tls_mode gate; HTTP 200/201 | port renamed to 19077 in v0.7.0 (was 9077). Harness `_remote_curl_prefix` already reads `A2A_PORT` env. | watch |
| S30 — capabilities handshake | `accept=v2` returns 8 families (no `audit`/`kg` family in v0.6.4 vocab). | v0.7.0 adds `audit` + `kg` to the 8-family roster. S64 covers explicitly; S30 may need an updated assertion list. | watch |
| S33 — subscribe pubsub    | DLQ available, no retry-attempt header. | v0.7.0 ships `X-Retry-Attempt` header on retries. S59 covers explicitly; S33 unchanged. | watch |
| S34 — pending governance  | `permissions.mode=advisory` default in v0.6.4. | v0.7.0 default flipped to `enforce`. S53 covers cross-agent. S34 may pass spuriously under the new default; review during Round 1. | watch |
| S41 — metrics/prometheus  | scrape endpoint `/metrics`. | v0.7.0 adds `decision_counts.enforce` series. S53 reads it directly. | watch |
| S43 — capabilities v2 schema | property roster. | new `audit` + `kg` family schema rows. | watch |

**No file in `scenarios/` has been modified from its ai2ai-gate origin.** Each
scenario carries the v0.7.0 banner only; the logic is byte-equivalent.

If a scenario is modified during the campaign, it MUST be done in an upstream
ai2ai-gate PR first; the modified file is then re-copied here. Do not fork the
logic in this repo.
