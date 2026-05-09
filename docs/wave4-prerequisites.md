# Wave 4 Prerequisites

Wave 4 of the v0.7.0 A2A campaign promotes the daemon's storage backend from sqlite to **postgres**, then re-runs the full 76-scenario test suite plus the six new postgres-specific scenarios (S77-S82) to demand **two consecutive 100 % GREEN rounds** before the v0.7.0-rc tag is cut.

This document lists the hard-gate prerequisites the orchestrator must verify (or wait for) **before** dispatching Wave 4. The harness changes that consume these prerequisites are pre-staged on `main` and will not run any postgres-only logic until `A2A_BACKEND_KIND` is flipped — so this doc is also the operator runbook for the dispatch itself.

## 1. Phase 22 droplet remediation must be complete

Phase 22 brings the postgres-node into a clean, deterministic state:

- Postgres 16 with extensions `vector` (pgvector >= 0.7.4) and `age` (>= 1.5) installed.
- Steady-state per-daemon databases: `aimemory_openclaw`, `aimemory_hermes` (created with `OWNER aimemory`).
- Per-scenario disposable databases pre-provisioned for S70-S76: `aimemory_s70`, `aimemory_sal72`, `aimemory_sal73`, `aimemory_perf_r3`, etc. Wave-4-specific disposables are created on demand by the scenarios themselves and need not exist at Phase 22 completion.
- VPC firewall: postgres-node port 5432 open from openclaw + hermes private IPs only.
- Schema bootstrap is **not** required at this layer — `ai-memory serve` runs `AI_MEMORY_AUTO_MIGRATE=1` on first boot and will create the canonical tables idempotently.

**Verification:**

```bash
# from openclaw:
PGPASSWORD=$(cat /tmp/v07-a2a-pg-password.txt) \
  psql -h 10.20.0.4 -U aimemory -d postgres \
       -tAc "SELECT datname FROM pg_database WHERE datname LIKE 'aimemory%' ORDER BY 1"
# expect: aimemory, aimemory_hermes, aimemory_openclaw, ...
```

If the disposable databases for S70-S76 are missing, the corresponding scenarios will SKIP rather than FAIL — they create their own ephemeral DBs. The hard gate is the steady-state `aimemory_openclaw` + `aimemory_hermes` plus working extensions.

## 2. Continuation 3 must be merged into the source repo

Continuation 3 lands the daemon-side support for `ai-memory serve --store-url postgres://...`. Until that merges, the binary at `$AI_MEMORY_BINARY_PATH` either does not advertise `--store-url` (older builds) or rejects it at runtime.

**Verification:**

```bash
ai-memory serve --help | grep -- --store-url
# expect: --store-url <URL>   Storage backend URL (sqlite://path or postgres://...)
```

`scripts/deploy_wave4.sh` runs this check automatically and bails before touching any droplet if it fails.

## 3. Operator authorization to redeploy droplets

`deploy_wave4.sh` performs a **destructive systemd-unit edit** on both daemon droplets. Specifically it:

- Replaces `/usr/local/bin/ai-memory` with the post-Continuation-3 build.
- Backs up `/etc/systemd/system/ai-memory.service` to `*.wave3.bak.<unix-ts>` then rewrites the `ExecStart` line, swapping `--db <path>` for `--store-url postgres://...`.
- `systemctl daemon-reload` + `systemctl restart ai-memory`.
- Does **not** delete the legacy sqlite file at `/var/lib/ai-memory/<agent>.db`. Rolling back to Wave 3 is a single `mv` of the systemd backup + restart.

The Wave-3-tested sqlite databases are preserved on disk; nothing is deleted. The orchestrator must still confirm with the operator before invoking the deploy script — at minimum the operator should answer:

- Has Phase 22 finished?
- Is the post-Continuation-3 binary at the expected path?
- Are the droplets currently outside any active campaign window?

## 4. Per-scenario disposable databases

Wave 4 scenarios that need a clean slate (S77 bootstrap probe, S81 federation seed, S82 KG path query) write into the steady-state `aimemory_openclaw` / `aimemory_hermes` DBs and use unique namespaces / agent_ids per run. The legacy postgres scenarios (S70-S76) still allocate their own disposables (`aimemory_s70`, `aimemory_sal72`, …); Phase 22 either pre-creates them or the scenarios `DROP DATABASE IF EXISTS` + `CREATE DATABASE` at the start.

No additional bootstrap is required for S77-S82.

## 5. Rollback

If Wave 4 dispatch reveals a regression in Continuation 3, the operator can roll a single droplet back to Wave 3 sqlite without re-imaging:

```bash
ssh root@<droplet_ip> '
  set -e
  UNIT=/etc/systemd/system/ai-memory.service
  BACKUP=$(ls -1t ${UNIT}.wave3.bak.* | head -n1)
  cp "$BACKUP" "$UNIT"
  systemctl daemon-reload
  systemctl restart ai-memory
'
```

The legacy sqlite file at `/var/lib/ai-memory/<agent>.db` is intact; the daemon resumes off Wave 3 state immediately. Re-running the campaign with `--backend-kind sqlite` (the default) will produce the prior 56/68 GREEN baseline.

## 6. Dispatch checklist

When all four prereqs above are satisfied, Wave 4 dispatch is:

```bash
# 1. orchestrator (mac) — re-deploy droplets in postgres mode
bash scripts/deploy_wave4.sh

# 2. round 1
A2A_BACKEND_KIND=postgres CAMPAIGN_ID=v0.7.0-a2a-wave4-r1 \
    ROUND_LABEL='Round 1' python3 scripts/run_round1.py --backend-kind postgres

# 3. round 2 (must hit 100 % GREEN identical to round 1 on the same scope)
A2A_BACKEND_KIND=postgres CAMPAIGN_ID=v0.7.0-a2a-wave4-r2 \
    ROUND_LABEL='Round 2' python3 scripts/run_round1.py --backend-kind postgres
```

GREEN definition for Wave 4: every in-scope scenario (76 baseline + 6 new = 82 candidates; some legitimately self-skip on the 2-agent topology) reports `pass=true` OR `skipped=true` and `pass=false` count is zero. The Stream A target is two such rounds back-to-back.
