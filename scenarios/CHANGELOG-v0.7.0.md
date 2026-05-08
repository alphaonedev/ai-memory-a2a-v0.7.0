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

## v0.7.0-alpha postgres + Apache AGE additions (S70–S76)

Seven net-new scenarios were added to cover the postgres + Apache AGE substrate
that v0.7.0-alpha ships. **Important scope clarification:** v0.7.0-alpha does
NOT ship live daemon-on-postgres — `ai-memory serve --store-url postgres://...`
is deferred to v0.7.1. v0.7.0-alpha ships only:

  * the migration tool (`ai-memory migrate --from sqlite://X --to postgres://Y`),
  * the SAL trait + adapters (feature `sal-postgres`, sqlx + pgvector),
  * the Apache AGE Cypher path for KG ops with recursive-CTE fallback.

These seven scenarios are scoped to that surface and explicitly do not assume
the live-pg-backend path. The schema-parity gap (postgres@v15 vs sqlite@v28)
is documented and trip-wired by S75.

| Scenario | Surface | Run policy |
|----------|---------|-----------|
| S70 — pg_migration_roundtrip      | sqlite→pg→sqlite migration round-trip + idempotency + sha256 equivalence | inline with S1–S69 |
| S71 — age_cte_equivalence         | AGE Cypher ≡ recursive-CTE for kg_query / kg_timeline / kg_invalidate / find_paths | run last; destructive on AGE state (uses disposable aimemory_kgtest db) |
| S72 — age_a2a_kg                  | A2A: openclaw migrates KG to pg, hermes reads same fingerprint via shared postgres+AGE | inline |
| S73 — pg_sal_contract             | upstream `tests/sal_contract.rs` against live postgres droplet | SKIP-allowed when cargo not on PATH; otherwise inline |
| S74 — pg_unsupported_capability   | `link()` and `register_agent()` return `UnsupportedCapability` cleanly | inline |
| S75 — pg_schema_parity            | postgres@v15 vs sqlite@v28 snapshot; missing-migration roster | inline; guards against silent schema drift |
| S76 — age_perf_gate               | AGE p95 ≥ 30% faster than CTE p95 at depth=5 (README bench gate) | run last alongside S71 — both touch the AGE extension state |

S70–S76 inherit the same `Harness.from_env` / `h.emit(passed=..., per_agent=...,
reasons=[])` reporting contract used by S52–S69. New helpers
`Harness.postgres_url(db=...)` and `Harness.postgres_node_ip()` are added to
`scripts/a2a_harness.py`; they read `POSTGRES_PASSWORD_PATH` from env (default
`/tmp/v07-a2a-pg-password.txt`, mode 600 on the orchestrator host).

The `.env.example` is extended with the `POSTGRES_*` block.
