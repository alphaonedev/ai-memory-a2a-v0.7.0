# NHI discovery findings (v0.7.0 cert)

The v0.7.0 cert ran a non-gating exploratory regression layer alongside
the deterministic scenario suite: **S83/S84/S85** (NHI discovery —
openclaw / hermes / bilateral). Each runs a Grok-4.2-reasoning agent
authored prompt-loop against the live HTTP surface, hunting for bugs
the human-authored scenarios miss.

This page captures every finding the NHI agents surfaced, in full,
with reproductions and architectural implications. Findings are
**informational** — they do not gate the cert. They become v0.7.0.x
patch backlog.

## Methodology

* **Driver:** `scripts/nhi_discovery.py` — Grok-4.2-reasoning loop with
  rate-limit (200 tool calls / 30 min wall) and namespace-scoped writes.
* **Briefings:** `prompts/nhi_discovery/{system_briefing,
  openclaw_focus, hermes_focus, tier_semantic, tier_autonomous}.md`.
* **Output:** structured JSON findings emitted by the agent itself in
  the action protocol; harness aggregates into `nhi-findings/*.json`
  per run.
* **Bilateral consensus (S85):** runs S83 + S84 in parallel against
  their respective nodes, then cross-validates findings —
  **consensus** = both agents independently observed the same bug;
  **single-observer** = only one agent observed.

## Round 1 — NHI tallies

| Scenario | Probes | Findings | Notes                                        |
|----------|-------:|---------:|----------------------------------------------|
| S83      |     85 |    **0** | xAI-resilient run; no findings emitted       |
| S84      |    116 |    **0** | xAI-resilient run; no findings emitted       |
| S85      |    270 |    **4** | 1 oc finding + 3 hm findings                 |

## Round 2 — NHI tallies

| Scenario | Probes | Findings | Notes                                        |
|----------|-------:|---------:|----------------------------------------------|
| S83      |     78 |    **0** |                                              |
| S84      |    136 |    **1** | NHI-D-postgres-search-501                    |
| S85      |    270 |    **2** | 2 oc findings — quota_status + priority clamp |

## Findings (deduplicated across runs)

### NHI-D-quota-postgres-501

| Field                  | Value                                                                                         |
|------------------------|-----------------------------------------------------------------------------------------------|
| **Category**           | integration                                                                                   |
| **Severity**           | high                                                                                          |
| **Observer**           | openclaw (R2 S85)                                                                              |
| **Captured at**        | turn 6 / +101s                                                                                 |
| **Summary**            | `quota_status` returns 501 on postgres backend despite semantic-tier docs listing it          |
| **Expected**           | 200 + `agent_quotas` object containing `current_memories_today` etc. (per semantic-tier docs) |
| **Observed**           | 501 with body `{"error": "endpoint not yet implemented for postgres-backed daemon"}`          |
| **Reproduction**       | `GET /api/v1/quota_status` against postgres-backed daemon                                     |
| **Hypothesis**         | Postgres adapter omits the in-memory quota counters and read-path handler present in sqlite adapter; write-path accounting (G1) is now wired but read-path returns 501 |
| **Architectural read** | SAL adapter incompleteness. Violates the "uniform SAL contract" expectation. The G1 write-path fix gets `current_memories_today` into the table; the read-path handler still 501s. |
| **Triage path**        | v0.7.0.x daemon patch — extend postgres adapter to implement `quota_status` read; small surface (single SELECT). |

### NHI-D-PRIO-CLAMP

| Field                  | Value                                                                                                              |
|------------------------|--------------------------------------------------------------------------------------------------------------------|
| **Category**           | functional                                                                                                          |
| **Severity**           | medium                                                                                                              |
| **Observer**           | openclaw (R2 S85)                                                                                                   |
| **Captured at**        | turn 71 / +1704s                                                                                                    |
| **Summary**            | `write_memory` accepts invalid `priority=0` and silently clamps to 5 instead of returning 400/422                  |
| **Expected**           | 400 or 422 for priority outside the documented `1-10` range (boundary validation)                                  |
| **Observed**           | 201 Created with `priority=5` in response body                                                                      |
| **Reproduction**       | (1) `POST /api/v1/memories` with `priority=0` in discovery namespace; (2) `GET /api/v1/memories/<id>` and inspect `priority` field — note value silently changed |
| **Hypothesis**         | Validation guard is missing or positioned after default/clamp logic in the write-path                              |
| **Architectural read** | Late-clamping without early validation in the command pipeline. Breaches the input-contract enforcement layer — caller can't distinguish "I sent priority=0 by mistake" from "I sent priority=5 deliberately." |
| **Triage path**        | v0.7.0.x daemon patch — early-validate priority bounds; reject before clamp. Single-line bound check in handler.   |

### NHI-D-postgres-search-501

| Field                  | Value                                                                                                       |
|------------------------|-------------------------------------------------------------------------------------------------------------|
| **Category**           | integration                                                                                                  |
| **Severity**           | medium                                                                                                       |
| **Observer**           | hermes (R2 S84)                                                                                              |
| **Captured at**        | turn 109 / +1388s                                                                                            |
| **Summary**            | `search` endpoint unimplemented on postgres-backed daemon despite semantic-tier docs listing it             |
| **Expected**           | search (vector + lexical) available per semantic-tier spec                                                   |
| **Observed**           | 501 with body `{"error": "not yet implemented for postgres-backed daemon", ...remediation hint}`             |
| **Reproduction**       | `POST /api/v1/search` with any query body on postgres node                                                  |
| **Hypothesis**         | AGE Cypher or pgvector binding missing in v0.7.0 postgres adapter (cf. G2-class bugs)                       |
| **Architectural read** | Same SAL adapter incompleteness pattern as quota_status. Postgres adapter has the storage primitives but the search-pipeline composer (vector lookup → lexical filter → 6-factor recall scoring) hasn't been wired through the SAL trait yet. The `recall` endpoint IS wired (S79 PASS). search is the missing twin. |
| **Triage path**        | v0.7.0.x daemon patch — wire `search` through the SAL trait similar to how `recall` is wired. May require copying the recall-pipeline composer with a different ranking head. |

## Audience analysis (3 audiences, same source data)

The cert run produces a single source-of-truth findings JSON. The
`scripts/render_gpu_results.py` renderer calls Grok-4.2-reasoning three
times with audience-specific system prompts to generate three
narratives from the **same** input. No hand-editing.

The full 3-audience verdict is on the [cert results page](cpu-cert.md).
Quick previews:

### Non-technical (60-second read)

> The software is ready to ship. All main tests passed with no errors
> across two complete runs, clearing every key check for stability
> and performance.

> Two issues were found during review. A resource limit report returns
> the wrong error on one storage system instead of showing current
> usage details as expected. Separately, the system accepts an invalid
> task importance setting of zero and quietly changes it rather than
> rejecting the request.

> This means customers can start using the updated software soon with
> high confidence in its quality.

### C-Level (5-minute read)

> **SHIP**. Certification completed with zero test failures across both
> replication runs (68 pass, 14 skip). Cost-to-cert finished 12% under
> budget. Risk posture is low — only two open findings (one
> integration, one functional) were logged, both non-blocking. TLS
> performance of 13.4–13.8 ms median handshakes is competitive.

> *(See full C-Level narrative + Top-3 Risks at [cpu-cert.md](cpu-cert.md).)*

### Subject-matter expert (engineering/architecture)

> Postgres SAL adapter incompleteness pattern: write-path covered (G1
> quota counter, G4 link→AGE projection, G3 timestamp canonicalization
> all live-verified), but two read-path handlers (`quota_status`,
> `search`) and one boundary validator (priority clamp) are still on
> the v0.7.0.x backlog. None block release; all three are bounded
> patches.

> *(See full SME narrative with reproductions + architectural patterns at [cpu-cert.md](cpu-cert.md).)*

## How to re-run NHI discovery

```bash
# Locally (after droplets up + DB clean):
A2A_BACKEND_KIND=postgres TLS_MODE=mtls AGENT_GROUP=openclaw \
  A2A_BASE_PORT=19077 \
  NODE1_IP=<openclaw-pub> NODE2_IP=<hermes-pub> \
  NODE1_PRIV=10.20.0.2 NODE2_PRIV=10.20.0.3 \
  POSTGRES_HOST=10.20.0.4 \
  RUN_DIR=runs/<id> \
  python3 scenarios/85_nhi_discovery_a2a.py
```

Per-run output lands in `runs/<id>/nhi-findings/`. Findings JSON is
deterministic in shape (the action-protocol contract is fixed) but
the content depends on what Grok actually probes — re-running may
surface different findings depending on the model's exploration.

## Why these are non-gating

The NHI layer is exploratory by design. A non-deterministic exploratory
agent can't gate cert closure (the verdict would be flaky). The
deterministic scenario suite is the cert gate; NHI findings are
v0.7.0.x backlog inputs. This separation is documented in
`prompts/nhi_discovery/system_briefing.md` and is the load-bearing
discipline that keeps the cert reliable while still surfacing
real-world bugs that fixed scenarios miss.
