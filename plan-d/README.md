# Plan D — Mac Mini + f2 native local-host test cell

Tracks #700 (v0.7.0 SHIP CAMPAIGN). Plan D is the **local-host** sibling of
Plan B/C: the 4-domain test cell runs natively on a single Mac Mini
(Apple M4, 32 GB) with the federation graph + AGE projection living on
a single Postgres 16 host on the LAN (`f2`, Pop!\_OS 24.04). No DigitalOcean
droplets, no Docker — runs entirely on hardware the operator already owns.

## Topology

```
Mac Mini (192.168.50.100)                f2  (192.168.50.1 / tailnet 100.70.167.11)
─────────────────────────                ─────────────────────────────────────────
ai-memory daemons      x4                 Postgres 16.13 + Apache AGE 1.5.0 +
  alice  127.0.0.1:9077                    pgvector
  bob    127.0.0.1:9078                       └─ federation_meta  (AGE graph
  charlie 127.0.0.1:9079                          memory_links_graph; 4 ic_*
  dave   127.0.0.1:9080                           schemas for IronClaw state)
  · all bind 127.0.0.1 (single-host
    mesh) and require mTLS via the
    shared CA in plan-d/tls/
  · quorum_writes = 2 of N=3 peers
  · tier = autonomous (config.toml)
  · per-domain SQLite a2a.db
IronClaw daemons       x4 (1 per domain)
  · stdio-MCP into local ai-memory
  · xAI grok-4.20-0309-reasoning via
    the openai_compatible provider
Ollama
  · gemma3:4b warm (LLM)
  · nomic-embed-text-v1.5 (embedder)
```

## Why Plan D

Plan B (DO cert) and Plan C (Docker plan-c) both exercise the v0.7.0
federation stack over rented infra. Plan D is the **no-cloud** test cell:
the operator runs the same 4-domain mesh on a single Mac Mini and a LAN
Postgres host, with mTLS still mandatory and quorum still 2-of-N. This
becomes the regression bed for D/E/F/G/H phases of #700 — every later
phase assumes the cell is live and reproducible.

## Quick start

```bash
# 1. f2 — drop+recreate federation_meta, install AGE + pgvector, grant
#    USAGE on ag_catalog, harden listen_addresses + pg_hba.conf.
ssh f2 -- "..."   # see plan-d/setup-f2.sh

# 2. Mac Mini — fan out 4 daemons, TLS material, configs, launch.
bash plan-d/setup-mac-mini.sh

# 3. Smoke — store a memory on alice, recall on bob/charlie/dave.
TLS=/Users/fate/v07/test-cell/tls
curl -sS --cacert $TLS/ca.pem --cert $TLS/node-alice.pem --key $TLS/node-alice.key \
  -X POST https://127.0.0.1:9077/api/v1/memories \
  -H 'Content-Type: application/json' \
  -d '{"tier":"mid","namespace":"_v070_grand_slam.smoke","title":"hello","content":"world","priority":5,"confidence":1.0,"source":"api","metadata":{"agent_id":"ai:alice"}}'
# expect: {"quorum_acks":2,...}
```

## Files

| Path                     | What it does                                                    |
|--------------------------|-----------------------------------------------------------------|
| `setup-f2.sh`            | drop/recreate federation_meta, AGE + pgvector, pg_hba, reload  |
| `setup-mac-mini.sh`      | TLS material, 4 domain dirs, configs, daemon + IronClaw launch |
| `teardown.sh`            | kill tmux sessions, drop f2 DB, wipe Mac Mini test-cell dir    |
| `tls/.gitignore`         | refuse to commit `*.key` / `*.pem` (private material)          |

## Networking gotcha — macOS Tailscale per-app intercept

Filed as issue #704 against `ai-memory-mcp` after the Phase B test
cell hit it repeatedly. Documented here for operator awareness — this
is **not** a substrate guarantee, it's a known third-party-VPN
behaviour the operator must work around.

**Symptom.** Outbound TCP from Homebrew `psql`, the Rust `ai-memory`
binary, and other non-Apple-signed processes to the f2 LAN IP
(`192.168.50.1`) fails with `EHOSTUNREACH`. The same address responds
fine from `nc(1)`, `ssh(1)`, and Safari, so it looks at first glance
like an `ai-memory` regression rather than a routing-table issue.

**Diagnosis.** `tailscale status` shows the tailnet-assigned address
(`100.70.167.11` on this cell) alongside the LAN address. macOS
Tailscale installs a system-level NetworkExtension that performs
per-app interception of LAN-range packets; Apple-signed binaries
bypass the extension, non-Apple-signed binaries route through it,
and the extension returns `EHOSTUNREACH` for LAN destinations it
hasn't been instructed to allow. The behaviour is documented at the
NEAR AI / Apple / Tailscale notarization layer and is not actionable
from inside `ai-memory`.

**Workaround.** Use the tailnet address instead of the LAN address
for any non-Apple-signed binary that needs to talk to f2. On this
cell:

```bash
# In ~/.env on the Mac Mini
FED_PG_HOST=100.70.167.11    # tailnet (works for Homebrew psql + ai-memory)
# FED_PG_HOST=192.168.50.1   # LAN (works for nc/ssh, fails for psql/ai-memory)
```

`setup-f2.sh` keeps `listen_addresses = '*'` and adds `pg_hba.conf`
entries for both `192.168.50.100/32` (Mac Mini LAN) and
`100.64.0.0/10` (CGNAT tailnet range) so either interface works
once Postgres is reached.

**Long-term.** No substrate-level fix. The NEAR AI / Apple /
Tailscale notarization landscape would need to change (either
Tailscale ships its extension with broader allowlisting for unsigned
binaries, or Apple's notarization policy changes), and neither is
actionable from this project. Operator can disable the gotcha by
reconfiguring Tailscale to leave `192.168.50.0/24` un-intercepted,
but the default install on macOS reproduces the failure mode.

## Other hard caveats (operator must know)

* **Federation outbound does not carry `x-api-key`** — `post_once()` in
  `src/federation/sync.rs` only forwards the body + `Idempotency-Key`.
  If `[api] api_key` is set in config.toml, peer `/api/v1/sync/push`
  POSTs return 401 and quorum never converges. Plan D therefore binds
  daemons to `127.0.0.1` (single-host mesh) and relies on the mTLS
  allowlist for identity. Cross-host federation with `api_key` set
  needs a small patch (or `--quorum-api-key` flag) — see #700.

* **Tier flag is config-only on `serve`** — `ai-memory serve` does NOT
  accept `--tier`. Tier lives in `config.toml` at top-level
  (`tier = "autonomous"`). The brief's `--tier autonomous` is correct
  for `mcp` / `store` / `recall` subcommands only.

* **`--store-url` and `--db` are mutually exclusive on `serve`** —
  passing both is rejected at startup. Plan D uses per-domain SQLite
  via `--db`; the Postgres+AGE graph on f2 is the cross-host federation
  metadata store, accessed via the peer mesh, not the local store.

* **ECDSA P-256 certs** — macOS `curl` (LibreSSL) cannot load ED25519
  client certs. The TLS gen script uses `prime256v1` so `curl` smoke
  tests work without rebuilding curl from source.
