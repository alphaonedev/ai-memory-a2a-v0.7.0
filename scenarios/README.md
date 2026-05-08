# Scenarios

Each `<id>_<slug>.py` is a self-contained Python 3 stdlib script implementing
one scenario in the v0.7.0 A2A testbook.

## Tagging conventions

| Range  | Origin | Banner |
|--------|--------|--------|
| S1–S51 | copied from `ai-memory-ai2ai-gate` v0.6.4 | `# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.` |
| S52–S69 | net-new for v0.7.0 (this repo) | scenario-specific docstring header carrying the intent spec verbatim |

If a copied S1–S51 scenario needs to be tweaked for a v0.7.0 schema delta, the
delta is described in [`CHANGELOG-v0.7.0.md`](CHANGELOG-v0.7.0.md) and the file
itself stays untouched — the workflow drops the scenario from the round and the
delta gets cherry-picked back upstream into ai2ai-gate.

## Contract

All scenarios share `scripts/a2a_harness.py`:

* `Harness.from_env(scenario_id)` reads `NODE1_IP`, `NODE2_IP`, `NODE3_IP`,
  `AGENT_GROUP`, `TLS_MODE` (+ optional `NODE4_IP`).
* For the v0.7.0 two-droplet topology, `NODE1_IP` = openclaw,
  `NODE2_IP` = hermes. `NODE3_IP` is set to `NODE1_IP` (legacy harness ergonomics —
  never written by S52–S69).
* Each scenario:
  * stdout ⇒ exactly one JSON line, the scenario report
  * stderr ⇒ human-readable log lines
  * exit 0 on a clean run (pass / fail / skip); non-zero only on hard crash

S67 + S68 also import `scripts/grok_driver.py` for xAI/Grok 4.2 reasoning calls.

## Running one scenario locally

```bash
export NODE1_IP=<openclaw-public>     NODE1_PRIV=<openclaw-private>
export NODE2_IP=<hermes-public>       NODE2_PRIV=<hermes-private>
export NODE3_IP=$NODE1_IP             NODE3_PRIV=$NODE1_PRIV
export AGENT_GROUP=mixed              TLS_MODE=mtls
export XAI_API_KEY=...                XAI_MODEL=grok-4.20-0309-reasoning

python3 scenarios/52_a2a_link_signed.py | jq .
```

## Running a full round

The campaign workflow (`.github/workflows/campaign.yml`) iterates the full
manifest and writes per-scenario JSON + an aggregated `a2a-summary.json` to
`runs/<campaign-id>/`.
