# Methodology

This page is the authoritative description of how each scenario in the v0.7.0
A2A campaign is measured. If a scenario script and this page disagree,
**the script wins** — file an issue pointing at the discrepancy.

## Hardware per campaign

| Role | Droplet | Why that size |
|---|---|---|
| openclaw-node | `s-4vcpu-16gb-amd` | 16 GB headroom for ai-memory + openclaw + Grok-bound async work + HNSW index |
| hermes-node   | `s-4vcpu-16gb-amd` | same, mirrored for the peer |
| Region        | `nyc3` (default)   | low operator latency; configurable via terraform variable |

Both droplets run Ubuntu 24.04 LTS. The orchestrator scp's a pre-built
`ai-memory` binary from `target/release/` (≥ `dfb184f`) to each droplet — no
on-droplet `cargo build`. The droplets each pull their respective agent
framework (openclaw or hermes) via the boot script.

## Driver model

Every scenario runs FROM the GitHub Actions runner. The runner ssh's into the
droplets and either:

* drives `curl` against `http://127.0.0.1:19077` (or `https://localhost:19077`
  with mTLS material under `/etc/ai-memory-a2a/tls/`), OR
* invokes `ai-memory mcp` over stdio with a JSON-RPC envelope, OR
* invokes the agent framework's CLI (openclaw/hermes) which itself drives MCP.

S67 + S68 additionally use Grok 4.2 reasoning (`grok-4.20-0309-reasoning`) via
xAI directly — the harness asks the model a question, persists the reply +
reasoning trace into ai-memory, and asserts retrievable round-trip.

## Pass/fail aggregation

Every scenario emits a single JSON line on stdout — the per-scenario report:

```json
{
  "scenario": "52",
  "pass": true,
  "skipped": false,
  "agent_group": "mixed",
  "tls_mode": "mtls",
  "reason": "",
  "per_agent": { ... },
  "reasons": []
}
```

`scripts/collect_reports.sh` aggregates the per-scenario reports into one
`runs/<campaign-id>/a2a-summary.json` with shape:

```json
{
  "campaign_id": "v0.7.0-r1",
  "round": "Round 1",
  "total": 67,
  "passed": 67,
  "failed": 0,
  "skipped": 0,
  "overall_pass": true,
  "audit_chain_length": <int>,
  "started_at": "...", "completed_at": "..."
}
```

**Round verdict** = `overall_pass` is `true` AND `failed == 0` AND
`skipped == 0` (skipped scenarios fail-closed under v0.7.0 — every scenario
must run).

**Certification gate** = TWO consecutive rounds with `overall_pass == true`
on the same `ai-memory` binary SHA. Round 1 dispatches; Round 2 auto-dispatches
on Round 1 GREEN.

## Per-scenario invariant summary

Full per-scenario assertions live in the scenario file's docstring header. The
[coverage matrix](coverage.md) tracks which v0.7.0 capability each scenario
exercises.

## Per-scenario timeouts

Scenarios are governed by a 6-minute soft cap per scenario (the harness's
ssh helpers already convert hangs into `returncode=124`). The workflow's
job-level cap is 50 min for the full round (matches ai2ai-gate's runner-driven
ceiling).

## What this methodology does NOT cover

* **Single-process kernel-level chaos** — that's `ai-memory-ship-gate`'s
  Phase 4. This campaign assumes a healthy kernel + healthy DO infra.
* **Backend migration / SAL switches** — covered by `ship-gate` Phase 3.
* **Long-tail soak.** Activated by `soak.yml` only after both rounds are
  GREEN, then runs at the same 4-hour cron cadence as ship-gate.

## Dead-man switch

Both droplets carry an 8-hour systemd-timer auto-destroy via cloud-init
(mirrors ship-gate). The terraform module additionally tags both droplets
`auto-destroy` so a sweeper can prune any stragglers.
