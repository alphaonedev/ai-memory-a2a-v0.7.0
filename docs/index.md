# v0.7.0 A2A campaign

Two-round consecutive 100% GREEN A2A regression + net-new test campaign for ai-memory v0.7.0.

| Round | Status | Scenarios | Notes |
|---|---|---|---|
| Round 1 | _pending_ | 0 / ~67 | Provisioning + dispatch via `two-rounds.yml` |
| Round 2 | _pending_ | 0 / ~67 | Auto-dispatched after Round 1 lands GREEN |
| Soak | gated | — | `soak.yml` enables only after both rounds are GREEN |

## Topology

* **openclaw-node** — `s-4vcpu-16gb-amd` in `nyc3`, agent_id `ai:openclaw@nyc3:droplet-1`.
* **hermes-node**   — `s-4vcpu-16gb-amd` in `nyc3`, agent_id `ai:hermes@nyc3:droplet-2`.
* Both run **ai-memory v0.7.0** (binary ≥ `dfb184f`) + their respective agent framework.
* Driver: **Grok 4.2 reasoning** (`grok-4.20-0309-reasoning`) via xAI API.
* A2A transport: **mTLS over the VPC**, port `19077`.
* Audit feature **ENABLED** on both nodes via `AI_MEMORY_AUDIT_DIR=/var/log/ai-memory/audit/`.

## Scenario coverage

* **S1–S51** — regression sweep copied verbatim from `ai-memory-ai2ai-gate` v0.6.4 baseline.
* **S52–S69** — net-new v0.7.0 coverage (attest_level signing, F8 enforce gate, smart_load veto, audit chain A2A, memory_notify, subscription webhook, permission inheritance, quota isolation, HNSW + reranker A2A, consolidate, capabilities v1/v2, find_paths, agent_id immutability, Grok dialog loop, reasoning-trace persistence, token budget under A2A load).

See [coverage.md](coverage.md) for the full S1–S95 master matrix.

## How to reproduce

See [reproducing.md](reproducing.md). DIY-friendly with your own DigitalOcean account + xAI API key.

## Security model

See [security.md](security.md). mTLS, dead-man-switch, key custody, audit chain.

## Methodology

See [methodology.md](methodology.md).
