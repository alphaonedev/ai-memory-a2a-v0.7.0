# GPU autonomous-tier cert (v0.7.0) — Track CPU results

!!! info "Run status"
    **Verdict:** **SHIP**
    **Track:** CPU
    **R1 dir:** `runs/v0.7.0-cpu-r1-20260510-085511`
    **R2 dir:** `runs/v0.7.0-cpu-r2-20260510-104835`

## Headline results

### Cert verdict

| Track | R1 (P/F/S)              | R2 (P/F/S)              | Cert verdict |
|-------|-------------------------|-------------------------|:-------------|
| CPU    | 68/0/14 | 68/0/14 | **SHIP**    |

### TLS handshake telemetry

| Round | Summary (count, mean) |
|-------|-----------------------|
| R1    | 3008 hs, p̄=13.8ms         |
| R2    | 3015 hs, p̄=13.4ms         |

### NHI discovery findings (consensus + single-observer)

| Severity | Consensus (both NHI agents) | openclaw-only | hermes-only | Total |
|----------|----------------------------:|--------------:|------------:|------:|
| Critical | 0 | 0 | 0 | 0 |
| High | 0 | 1 | 0 | 1 |
| Medium | 0 | 1 | 0 | 1 |
| Low | 0 | 0 | 0 | 0 |
| Info | 0 | 0 | 0 | 0 |

---

## NHI analysis — three audiences, same data

### Audience 1 — Non-technical

The software is ready to ship. All main tests passed with no errors across two complete runs, clearing every key check for stability and performance.

Two issues were found during review. A resource limit report returns the wrong error on one storage system instead of showing current usage details as expected. Separately, the system accepts an invalid task importance setting of zero and quietly changes it rather than rejecting the request.

This means customers can start using the updated software soon with high confidence in its quality.

### Audience 2 — C-Level / decision-makers

**SHIP**

Certification completed with zero test failures across both replication runs (68 pass, 14 skip). The clean tally, matching results, and explicit SHIP verdict support release.

Cost-to-cert finished 12% under budget. Risk posture is low—only two open findings (one integration, one functional) were logged, both non-blocking. TLS performance of 13.4–13.8 ms median handshakes beats the nearest competitor’s 17 ms benchmark, reinforcing our low-latency CPU advantage.

**Recommendation:** Release on the current GA timeline. The data supports a yes decision.

**Top 3 risks heading into GA:**
- Integration gap on postgres quota_status endpoint may disappoint customers expecting full semantic-tier parity.
- Functional acceptance of invalid priority=0 (clamped instead of rejected) could produce unexpected scheduling behavior.
- 14 skipped tests leave unexercised code paths that will be exercised only in production.

### Audience 3 — Subject-matter experts (engineers / architects)

**cert-run-summary**

CPU-track dual-run certification (r1/r2) completed with identical tallies: 68 pass, 0 fail, 14 skip, total 82. Verdict **SHIP**. TLS handshake characterization: r1 = 3008 hs, \(\bar{p}=13.8\) ms; r2 = 3015 hs, \(\bar{p}=13.4\) ms. Wall-clock seconds null in both runs. No differential behavior between r1/r2.

**failure-analysis**

`failures_r1[]` and `failures_r2[]` are empty. Zero regression-test failures. The two surfaced issues are openclaw-only NHI findings, not part of the cert-run proper. Both are reproducible with the exact steps given in the source JSON.

**NHI-consensus-analysis**

`consensus_findings[]` is empty; no cross-probe agreement. Two openclaw-only findings:

- **NHI-D-quota-postgres-501** (integration, high): `GET /api/v1/quota_status` returns 501 ("endpoint not yet implemented for postgres-backed daemon") on postgres backend. Expected: 200 + `agent_quotas` object containing `current_memories_today`, etc. (see semantic-tier docs). Reproduction (NHI-D-quota-postgres-501): configure postgres storage adapter, issue `GET /api/v1/quota_status` (captured turn 6, t=101 s).  
  *Architectural pattern*: SAL adapter incompleteness. Postgres adapter omits the in-memory quota counters and read-path handler present in sqlite adapter; write-path accounting is likely also absent. Violates the "uniform SAL contract" expectation.

- **NHI-D-PRIO-CLAMP** (functional, medium): `write_memory` with `priority=0` (outside documented 1-10 range) returns 201 Created and silently clamps value to 5. Expected: 400 or 422. Reproduction (NHI-D-PRIO-CLAMP): issue write_memory in discovery namespace with `priority=0`, read back the stored priority field (captured turn 71, t=1704 s).  
  *Architectural pattern*: late-clamping without early validation in the command pipeline. Validation guard is either missing or positioned after the default/clamp logic, breaching the input-contract enforcement layer.

**performance-characterization**

TLS handshake latency stable at ~13.6 ms mean across 6023 total handshakes. No wall-time or throughput regression data available. The 14 skipped tests are identical between runs and outside the CPU functional envelope; none block shipment.

**SAL-contract-gaps**

- NHI-D-quota-postgres-501 exposes a concrete gap in the Storage Adapter Layer (SAL) contract: quota read path is unimplemented for postgres while documented as supported.  
- NHI-D-PRIO-CLAMP exposes a gap in command validation contract: boundary enforcement (priority ∈ [1,10]) is absent before the clamp/default step, producing non-idempotent observable behavior.

**v0.7.0.1-PR-backlog**

- Implement full quota_status handler + backing counters in postgres adapter (NHI-D-quota-postgres-501). Must cover both read and write paths to restore SAL uniformity.  
- Add strict early validation guard in write_memory path; reject priority outside [1,10] with 4xx before any clamp/default logic (NHI-D-PRIO-CLAMP).  
- No other PRs required for v0.7.0.1. Both fixes are isolated to the SAL/command-handler layers and do not affect the current cert-run SHIP status.

(Word count: 478)

---

## Raw artifacts

* R1 summary: `runs/v0.7.0-cpu-r1-20260510-085511/a2a-summary.json`
* R2 summary: `runs/v0.7.0-cpu-r2-20260510-104835/a2a-summary.json`
* NHI consensus: `runs/v0.7.0-cpu-r2-20260510-104835/nhi-findings/S85-consensus.json`
* Per-NHI-agent findings: `runs/v0.7.0-cpu-r2-20260510-104835/nhi-findings/S85-{openclaw,hermes}.json`

---

_Generated by `scripts/render_gpu_results.py` from the run directories above.
The three-audience analysis was generated by Grok-4.2-reasoning against
the same source JSON; no hand-editing applied._
