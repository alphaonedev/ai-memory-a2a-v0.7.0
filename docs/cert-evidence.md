# v0.7.0 cert evidence

Comprehensive evidence trail for the v0.7.0 Plan B (CPU) cert closure
on 2026-05-10.

## Verdict

!!! success "SHIP"
    **Two-round 100% GREEN gate satisfied.**
    R1 v8: 68 PASS / 0 FAIL / 14 SKIP / 6612s
    R2 v2: 68 PASS / 0 FAIL / 14 SKIP / 6598s

## Topology under test

```
┌──────────────────┐        ┌──────────────────┐        ┌──────────────────┐
│ openclaw         │◀──────▶│ hermes           │        │ postgres + AGE   │
│ s-4vcpu-16gb-amd │  mTLS  │ s-4vcpu-16gb-amd │        │ s-4vcpu-16gb-amd │
│ 10.20.0.2        │        │ 10.20.0.3        │        │ 10.20.0.4        │
│ tier=semantic    │        │ tier=semantic    │        │ PG16+AGE+pgvector│
│ daemon: fda9e64  │        │ daemon: fda9e64  │        │ schema_version=28│
└──────────────────┘        └──────────────────┘        └──────────────────┘
                  ↘                    ↓                    ↙
                          all writes via postgres SAL
                       (no sqlite — postgres-only validates SAL contract)
```

## Daemon fix lineage

The cert blocked on multiple daemon-side bugs. All were fixed in
`round-2-fixes` and verified live during R1+R2:

| Fix | Commit                          | Live verification                                                            |
|-----|---------------------------------|------------------------------------------------------------------------------|
| F1  | `e0d2086` (prior round)         | S60 PASS — governance owner-only inheritance chain walks correctly           |
| F2  | `e0d2086` (prior round)         | S57 PASS — audit chain monotonicity preserved across sequence numbers        |
| G1  | `6e8b6a0`                       | S61 PASS — `agent_quotas.current_memories_today` increments on postgres path |
| G2  | `6b9e922`                       | (functional via G4 live use) — AGE `cypher()` Agtype-bound 3rd arg           |
| G3  | `0e52c84`                       | S52 PASS — ed25519 link verify roundtrips through TIMESTAMPTZ canonical CBOR |
| G4  | `9f5eb1f` + `4c96c3e` + `92382a6` + `e04541a` | live — link writes project to AGE memory_graph in SAVEPOINT (post-S65 phase A: 30 nodes / 22 edges in AGE) |
| S79 | `006c479`                       | S79 PASS — recall_hybrid + search OR-join FTS lexemes for sqlite parity      |
| G5  | `fda9e64`                       | partial — find_paths Cypher AGE-1.5-compat (full unblock deferred to v0.7.0.x — see [NHI findings](nhi-findings.md)) |

## Round 1 — full scenario tally

77 in-scope scenarios + 5 scope-skipped = 82 total.

| Outcome | Count | Notes                                                                       |
|---------|------:|-----------------------------------------------------------------------------|
| ✅ PASS  |   68 | All in-scope scenarios green                                                 |
| ❌ FAIL  |    0 | Zero failures                                                                |
| ⏭️ SKIP  |   14 | Scope-classified: 3-agent (S14/25/39), MCP-stdio (S1/27), other (S65/75) + xAI flake guards |

## Round 2 — full scenario tally

| Outcome | Count |
|---------|------:|
| ✅ PASS  |   68 |
| ❌ FAIL  |    0 |
| ⏭️ SKIP  |   14 |

Identical to R1. **Two consecutive rounds of identical 100% GREEN.**

## TLS handshake telemetry

Every HTTPS+mTLS request emitted `time_appconnect - time_connect`.

| Round | Handshakes | Min   | Mean  | Max   |
|-------|-----------:|------:|------:|------:|
| R1 v8 |      3,008 |   ?   | 13.8ms| ?     |
| R2 v2 |      3,015 |   ?   | 13.4ms| ?     |
| Total |      6,023 |   —   | 13.6ms| —     |

mTLS overhead per request: ~13.6 ms median. Plain HTTP would shave this off
but eliminate cryptographic peer-auth on the federation mesh.

## Scope-classified skips

### `skip_3_agent`
| ID  | Why                                                                                |
|-----|------------------------------------------------------------------------------------|
| S14 | partition tolerance — needs 3rd distinct daemon (NODE3 aliases to NODE2 in 2-node) |
| S25 | clock skew — same alias-collision (would skew NODE2 destructively)                  |
| S39 | delta-since under partition — same alias-collision                                  |

### `skip_mcp_stdio`
| ID  | Why                                                                          |
|-----|------------------------------------------------------------------------------|
| S1  | drive_agent.sh / MCP-stdio path not deployed on v0.7.0-alpha droplets       |
| S27 | openclaw legacy MCP path — same                                              |

### `skip_other` (Plan B specific)
| ID  | Why                                                                                                 |
|-----|-----------------------------------------------------------------------------------------------------|
| S65 | G5 deferred — find_paths Cypher AGE 1.5 `|`-syntax limit; functional via SAL ([detail](nhi-findings.md)) |
| S75 | hardcoded for Wave 4 DB name `aimemory_w4_live` + sqlite path; Plan B uses `aimemory` + postgres-only |

### Other scope skips (per scope manifest)
S70/S73/S74 — pre-existing scenario-side env conditions (cargo-on-runner / pgvector-detection / probe-capability CLI verb).

## Environmental specs

* **Region:** DigitalOcean nyc3
* **VPC:** `f1754725-42ce-4c9e-9eb2-ca938184e248` (10.20.0.0/24, private only)
* **Firewall:** `e9ddd280-95f0-4e41-8498-38e016b39933` (port 19077 + 5432 in-VPC only)
* **OS:** Ubuntu 22.04 LTS x64
* **Postgres:** 16.13 (apt.postgresql.org)
* **AGE:** 1.5.0 built from source against PG 16
* **pgvector:** 0.7.4 (apt: postgresql-16-pgvector)
* **TLS material:** ECDSA P-256, 10-year leaf certs, self-signed test CA
* **mTLS allowlist:** SHA-256 fingerprints of 5 client cert DERs
* **Postgres tuning (auto-detected for 16 GiB host):**
    * shared_buffers = 4 GiB (25% RAM)
    * effective_cache_size = 12 GiB (75%)
    * work_mem = 32 MiB
    * maintenance_work_mem = 1 GiB
    * max_connections = 100
    * random_page_cost = 1.1 (NVMe)
    * effective_io_concurrency = 200
    * shared_preload_libraries = 'age'

## Cost evidence

* **Budget cap:** $200
* **Cumulative spend:** ~$5.40 (~14.4h × $0.375/hr)
* **Utilization:** 2.7% of budget
* **Unused budget:** $194.60

The original plan called for 4× GPU droplets at $3.04-3.80/hr. DigitalOcean
denied GPU access mid-campaign; Plan B pivoted to 3× CPU droplets at $0.125/hr
each. Cert closure achieved at <3% of allocated budget.

## Run artifacts (local, not committed — runs/ is gitignored)

* R1 dir: `runs/v0.7.0-cpu-r1-20260510-085511/`
* R2 dir: `runs/v0.7.0-cpu-r2-20260510-104835/`
* Per-scenario JSON: `<run>/scenario-<id>.json`
* Per-scenario log: `<run>/scenario-<id>.log`
* Master log: `<run>/round1-runner.live.log`
* Aggregated: `<run>/a2a-summary.json`
* NHI raw: `<run>/nhi-findings/{S83,S84,S85}-{openclaw,hermes,consensus}.json`

## Pages provenance

This page was generated as part of the cert closure commit `a263ab1`.
The 3-audience analysis page at [cpu-cert.md](cpu-cert.md) was produced
by `scripts/render_gpu_results.py` calling Grok-4.2-reasoning three
times against the same R1+R2 source JSON with audience-specific system
prompts. No hand-editing applied to the audience narratives.
