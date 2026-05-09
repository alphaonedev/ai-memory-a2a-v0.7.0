# GPU autonomous-tier cert (v0.7.0) — results

!!! info "Run status"
    **Status:** awaiting first GPU cert run.
    **Track:** A.1 (homogeneous autonomous tier) and/or A.2 (mixed-tier
    federation), per [v0.7.0-gpu-autonomous-track](v0.7.0-gpu-autonomous-track.md).
    **Gates on:** v0.7.0.1 daemon fixes for G1/G2/G3 ([HALT
    finding](runs/v0.7.0-a2a-cont6-cert-r1b-20260509-2148/findings/HALT.md)).

This page publishes the results of running the v0.7.0 cert campaign on
GPU droplets in autonomous-tier-full mode against postgres + Apache AGE.
It also publishes the AI-NHI-generated analysis of those results,
written to three different audiences from the **same source data**.

---

## What's being tested

* **Topology:** 1× or 2× `gpu-4000adax1-20gb` running `ai-memory serve
  --tier autonomous --store-url postgres://...` against a dedicated
  postgres + AGE droplet, with HTTPS+mTLS between peers.
* **Coverage:** the full v0.7.0 campaign (77 in-scope scenarios) PLUS
  scenarios that have been on SKIP for 5+ rounds because they require
  a local LLM:
    * `auto_tag` (LLM tagger)
    * `consolidate` (LLM merger)
    * `expand_query` LLM mode
    * `detect_contradiction` LLM
    * `smart_load`
* **NHI exploratory layer:** S83/S84/S85 — Grok-4.2-reasoning drives a
  bug-discovery loop with `tier_autonomous.md` overlay, hunting for
  G1/G2/G3-class integration bugs that fixed scenarios miss.

The full topology + methodology is in
[v0.7.0-gpu-autonomous-track](v0.7.0-gpu-autonomous-track.md).

---

## Headline results

!!! warning "Awaiting first run"
    Tables below are populated by `scripts/render_gpu_results.py` after
    the first GPU cert run completes. The shapes shown are the data
    contracts the renderer expects.

### Cert verdict

| Track | Topology                          | Round 1 | Round 2 | Cert verdict |
|-------|-----------------------------------|--------:|--------:|:-------------|
| A.1   | 2× RTX-4000-Ada + postgres+AGE    | _pend_  | _pend_  | _pending_    |
| A.2   | 1× RTX-4000-Ada + 1× CPU + pg+AGE | _pend_  | _pend_  | _pending_    |

### Scenario tally (per round)

| Round | PASS | FAIL | SKIP | Wall (min) | TLS handshakes | NHI findings |
|-------|-----:|-----:|-----:|-----------:|---------------:|-------------:|
| A.1 R1 |  —  |  —  |  —  |     —      |       —        |      —       |
| A.1 R2 |  —  |  —  |  —  |     —      |       —        |      —       |
| A.2 R1 |  —  |  —  |  —  |     —      |       —        |      —       |
| A.2 R2 |  —  |  —  |  —  |     —      |       —        |      —       |

### Performance headline (autonomous tier)

| Operation                   | p50 (ms) | p99 (ms) | Δ vs semantic baseline |
|-----------------------------|---------:|---------:|-----------------------:|
| `auto_tag` LLM round-trip   |    —     |    —     |          —             |
| `consolidate` LLM merge     |    —     |    —     |          —             |
| `expand_query` LLM rewrite  |    —     |    —     |          —             |
| `detect_contradiction`      |    —     |    —     |          —             |
| `recall` (semantic+rerank)  |    —     |    —     |          —             |
| TLS handshake (median)      |    —     |    —     |          —             |

### NHI discovery findings (consensus + single-observer)

| Severity | Consensus (both NHI agents) | openclaw-only | hermes-only | Total |
|----------|----------------------------:|--------------:|------------:|------:|
| Critical |             —               |       —       |     —       |   —   |
| High     |             —               |       —       |     —       |   —   |
| Medium   |             —               |       —       |     —       |   —   |
| Low      |             —               |       —       |     —       |   —   |
| Info     |             —               |       —       |     —       |   —   |

---

## NHI analysis — three audiences, same data

The NHI discovery layer (S85 bilateral) emits a structured JSON
findings file at `runs/<id>/nhi-findings/S85-consensus.json`. From that
single source-of-truth, the harness asks Grok-4.2-reasoning to write
**three audience-specific narratives**. Each is grounded in the same
findings — they differ in framing, vocabulary, and what's elided.

The methodology, for transparency:

* Same source JSON for all three.
* Each narrative is generated with a distinct system prompt
  (audience role, vocabulary constraints, success criteria).
* No hand-editing post-generation; only formatting cleanup.
* Each narrative cites the specific finding IDs it draws from, so a
  reader who wants to verify can drop down to the raw JSON.

### Audience 1 — Non-technical (executive summary, plain English)

!!! note "What this audience cares about"
    "Did it work? Should I be worried? What does this mean for
    customers and users?" — readable in 60 seconds, no jargon, no
    acronyms without one-line definitions.

**Generated post-run.** Skeleton:

> _The v0.7.0 release candidate of `ai-memory` was put through a
> comprehensive automated test campaign on GPU servers running its
> most advanced configuration (Gemma 4-class language model + Postgres
> with graph extensions). [N] tests ran, [P] passed and [F] failed.
> [If F=0:] No failures means the system is ready for release. [If
> F>0:] The failures fell into [N] categories, the most serious being
> [headline]. The team has [closed/reopened] the release decision._
>
> _Two AI agents (named openclaw and hermes) also ran in parallel as
> "explorers" — they tried things the scripted tests don't, looking
> for bugs nobody anticipated. They found [N] issues both agents
> agreed on, plus [N] issues only one agent saw. The agreed-on findings
> are the more credible ones._
>
> _Bottom line: [SHIP / HOLD / BLOCK]._

### Audience 2 — C-Level / decision-makers

!!! note "What this audience cares about"
    Risk, cost, timeline, competitive posture. Comfortable with light
    technical framing; wants the trade-offs and the recommendation.

**Generated post-run.** Skeleton:

> _**Verdict:** [SHIP / HOLD / BLOCK on G-class fix]._
>
> _**Cost-to-cert:** $[X] of GPU + $[Y] of xAI tokens against a $[Z]
> budget envelope. [Within / over / under]._
>
> _**Risk posture:** the v0.7.0 cert covers [P] / [P+F] scenarios
> green on two consecutive runs, plus an AI-driven exploratory sweep
> that identified [N] issues with [Critical: A] [High: B] [Medium: C]
> [Low: D]. The Critical and High items [are/are not] cert-blocking._
>
> _**Competitive read:** autonomous-tier latency p50 of [X] ms places
> the substrate [ahead of / on par with / behind] the published
> baselines for comparable enterprise-memory products. mTLS overhead
> measured at [X]% — production-grade._
>
> _**Recommendation:** [proceed to v0.7.0 GA / hold for v0.7.0.1
> daemon patch / extend cert window for AGE bench tuning]._
>
> _**Top 3 risks heading into GA:**_
>   1. _[finding-id and one-line risk]_
>   2. _[...]_
>   3. _[...]_

### Audience 3 — Subject-matter expert (engineers / architects)

!!! note "What this audience cares about"
    Reproduction steps, root-cause hypotheses, failure modes,
    architectural implications. Will read the raw JSON; the narrative
    is a guided tour, not a substitute.

**Generated post-run.** Skeleton:

> _**Cert run summary:** [campaign-id], [N] scenarios, [P] PASS / [F]
> FAIL / [S] SKIP. Wall: [W] min. R1+R2 both green: [yes/no]._
>
> _**Failure analysis (per failing scenario):**_
>   - _S[id] — [name]_
>     - _Failure shape: [http_code / fingerprint diff / latency cliff]_
>     - _Reproducer: [exact request body + node]_
>     - _Hypothesis: [root cause speculation, anchored to source files]_
>     - _Suggested fix: [PR-actionable change description]_
>
> _**NHI consensus findings (cross-validated bug-class):**_
>   - _NHI-D-[id] — [summary] — observed by both openclaw and hermes._
>     - _Reproduction (from raw JSON): [steps]_
>     - _Architectural implication: [e.g. "AGE cypher() arg-3 binding is
>       a backend-specific path; the SQLite analog uses cte_recursive
>       and silently passes — this is a SAL contract gap, not a bug
>       per se. v0.7.0.1 fix should add a SAL-level test that binds
>       params on every backend or fails the build."]_
>
> _**Performance characterization:**_
>   - _LLM-bound ops p50/p99: [...]. Compare to ROADMAP2 §4.6 published
>     baselines: [delta]._
>   - _GPU memory pressure under sustained load: [observed peak / ceiling]._
>   - _Cross-encoder Mutex contention (8 vCPU floor): [throughput
>     plateau evidence, if any]._
>   - _AGE bench gate (≥30% over CTE at depth=5): [pass/fail with
>     numbers]._
>
> _**SAL contract gaps surfaced:**_ _[per-finding]_
>
> _**v0.7.0.1 PR backlog (ranked by cert-impact):**_ _[ordered list]_

---

## Reproducing the GPU cert

```bash
# Provision (idempotent — re-running is safe)
./scripts/provision_gpu_droplets.sh --topology=A1 --region=nyc3
# → spins 2× gpu-rtx-4000-ada-1 + 1× s-4vcpu-16gb-amd postgres node
# → runs Ollama bake (gemma3:4b + nomic-embed-text-v1.5)
# → installs ai-memory daemon (binary baked from current campaign HEAD)
# → wires HTTPS+mTLS allowlist

# Round 1
A2A_BACKEND_KIND=postgres TLS_MODE=mtls NHI_TIME_BUDGET_S=1800 \
  ./scripts/run_round.sh --campaign=v0.7.0-gpu-A1-r1 --in-scope-from-manifest

# Round 2 (must be 100% GREEN to certify)
A2A_BACKEND_KIND=postgres TLS_MODE=mtls NHI_TIME_BUDGET_S=1800 \
  ./scripts/run_round.sh --campaign=v0.7.0-gpu-A1-r2 --in-scope-from-manifest

# Render this page from the run dirs
./scripts/render_gpu_results.py \
  --track=A1 \
  --r1-dir=runs/v0.7.0-gpu-A1-r1-<ts> \
  --r2-dir=runs/v0.7.0-gpu-A1-r2-<ts> \
  --out=docs/gpu-cert.md
```

`render_gpu_results.py` (1) populates the result tables from
`a2a-summary.json` + `nhi-findings/*.json` and (2) calls
Grok-4.2-reasoning three times with the audience prompts to generate
the analysis sections.

---

## Per-run history

_Populated post-run with links to each `runs/<campaign-id>/`
directory and a one-line verdict per row._

| Campaign id | Track | R1 result | R2 result | NHI consensus | Cert verdict | Date |
|-------------|-------|-----------|-----------|---------------|--------------|------|
| _none yet_  |   —   |     —     |     —     |       —       |      —       |  —   |

---

## Raw artifacts

* Run JSON: `runs/<campaign-id>/a2a-summary.json`
* NHI findings: `runs/<campaign-id>/nhi-findings/S85-consensus.json`
* Per-scenario reports: `runs/<campaign-id>/scenarios/*.json`
* Audit chain dumps: `runs/<campaign-id>/audit/*.log`
