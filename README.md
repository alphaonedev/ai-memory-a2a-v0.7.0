# ai-memory-a2a-v0.7.0

**Two-round consecutive 100% GREEN A2A regression + net-new campaign for ai-memory v0.7.0.**

| Round | Status | Scenarios | Notes |
|---|---|---|---|
| Round 1 | _pending_ | 0 / ~67 | Provisioning + dispatch via `.github/workflows/two-rounds.yml` |
| Round 2 | _pending_ | 0 / ~67 | Auto-dispatched after Round 1 lands GREEN |
| Soak | gated | — | `.github/workflows/soak.yml` enables only after both rounds are GREEN |

Live evidence: **https://alphaonedev.github.io/ai-memory-a2a-v0.7.0/** (Pages site populates after the first run).

---

## What this campaign tests

A two-droplet topology proving the v0.7.0 A2A surface end-to-end:

```
                   ┌──────────────────────────────────┐
                   │  GitHub Actions runner (driver)  │
                   │   harness/scripts/scripts/*      │
                   └───────┬───────────────┬──────────┘
                           │ ssh + xAI     │ ssh + xAI
                           ▼               ▼
            ┌──────────────────────┐  ┌──────────────────────┐
            │  openclaw-node       │  │  hermes-node         │
            │  s-4vcpu-16gb-amd    │  │  s-4vcpu-16gb-amd    │
            │  ai:openclaw@nyc3:1  │  │  ai:hermes@nyc3:2    │
            │  + ai-memory v0.7.0  │  │  + ai-memory v0.7.0  │
            │  + Grok 4.2 (xAI)    │  │  + Grok 4.2 (xAI)    │
            │                      │◀─┤                      │
            │       A2A mTLS :19077  ──▶│                    │
            └──────────────────────┘  └──────────────────────┘
                       VPC 10.250.0.0/20 (nyc3)
```

Both nodes run:
* `ai-memory v0.7.0` (binary ≥ `dfb184f` from `target/release/ai-memory`)
* Audit feature ON (`AI_MEMORY_AUDIT_DIR=/var/log/ai-memory/audit/`)
* Their respective agent framework (openclaw or hermes)
* Grok 4.2 reasoning (`grok-4.20-0309-reasoning` via xAI API)

---

## Scenario coverage

* **S1–S51** — Regression sweep copied verbatim from
  [`ai-memory-ai2ai-gate`](https://github.com/alphaonedev/ai-memory-ai2ai-gate)
  v0.6.4 baseline. Banner-tagged (`# v0.7.0 A2A — copied from …`) so updates
  cherry-pick cleanly back into ai2ai-gate. Any scenario needing a v0.7.0
  schema-delta tweak is flagged in [`scenarios/CHANGELOG-v0.7.0.md`](scenarios/CHANGELOG-v0.7.0.md)
  but the file itself stays untouched until that PR lands.
* **S52–S69** — Net-new v0.7.0 coverage:
  attest_level signing (S52), F8 enforce gate cross-agent (S53), identity
  --force flag (S54), smart_load veto (S55), tools_verbose env (S56),
  audit chain A2A (S57), memory_notify cross-droplet (S58), subscription
  webhook A2A (S59), permission inheritance (S60), quota isolation (S61),
  HNSW + reranker A2A (S62), consolidate cross-agent (S63), capabilities
  v1/v2 (S64), find_paths (S65), agent_id immutability (S66), Grok-driven
  dialog loop (S67), reasoning-trace persistence (S68), token budget under
  A2A load (S69).

The full master matrix: [`docs/coverage.md`](docs/coverage.md).

---

## How to reproduce

1. Bring your own DO account + xAI API key. Copy `.env.example` → `.env` and fill in.
2. `cd terraform && terraform init && terraform apply` provisions the two
   droplets + VPC + firewall + dead-man-switch.
3. `./scripts/boot_openclaw.sh <openclaw_ip>` and `./scripts/boot_hermes.sh
   <hermes_ip>` install ai-memory + the agent framework.
4. Set `NODE1_IP`/`NODE2_IP` (and the `_PRIV` variants) and run
   `python3 scenarios/<id>_<name>.py` for any scenario, or use
   `scripts/a2a_harness.py` directly.

Full instructions: [`docs/reproducing.md`](docs/reproducing.md).

---

## Methodology + verdict shape

* Pass aggregator: every scenario emits one stdout JSON line + a non-zero exit
  only on hard crash. `scripts/collect_reports.sh` produces
  `runs/<campaign-id>/a2a-summary.json`. **Round verdict = all-pass.**
* Two consecutive rounds at 100% GREEN are the certification gate. After that,
  `soak.yml` activates (4-hour cron, mirrors ship-gate cadence).

Methodology + per-scenario invariants: [`docs/methodology.md`](docs/methodology.md).
Security model: [`docs/security.md`](docs/security.md).

---

## License

Apache-2.0 — see [LICENSE](LICENSE).
