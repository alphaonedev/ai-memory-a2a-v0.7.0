#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Render docs/gpu-cert.md from a finished GPU cert run.

Reads:
  runs/<campaign-r1>/a2a-summary.json
  runs/<campaign-r2>/a2a-summary.json
  runs/<campaign-r2>/nhi-findings/S85-consensus.json
  (optional) runs/<campaign-r2>/perf-vs-baseline.md

Generates:
  - Headline tables (verdict, scenario tally, perf, NHI findings)
  - Three-audience NHI analysis (non-technical, C-Level, SME) by calling
    Grok-4.2-reasoning three times with audience-specific system prompts
    against the SAME findings JSON

Usage:
  ./scripts/render_gpu_results.py \
      --track A1 \
      --r1-dir runs/v0.7.0-gpu-A1-r1-20260510-1200 \
      --r2-dir runs/v0.7.0-gpu-A1-r2-20260510-1500 \
      --out docs/gpu-cert.md \
      [--no-llm]   # skip the 3-audience generation (template stays as-is)
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from grok_driver import grok_chat  # noqa: E402


AUDIENCE_PROMPTS = {
    "non_technical": """You are summarizing a software release test result for a NON-TECHNICAL audience.

Constraints:
* No jargon. If a term must appear, define it inline in 4 words or fewer.
* Maximum 200 words total.
* Open with the verdict in plain English.
* Close with one sentence on what this means for users / customers.
* Do NOT cite finding IDs. Reference findings by their summary in plain English.
* Do not use code blocks, tables, or formatting beyond paragraphs.

Write 3 short paragraphs. The reader has 60 seconds.""",

    "c_level": """You are summarizing a software release test result for a C-LEVEL DECISION-MAKER audience.

Constraints:
* Risk, cost, timeline, competitive posture. Light technical framing OK.
* Maximum 350 words total.
* Open with the explicit verdict: SHIP / HOLD / BLOCK.
* Include: cost-to-cert vs budget, risk posture summary, competitive read on perf, recommendation, top 3 risks heading into GA.
* Use short paragraphs and bulleted top-3 risks.
* Cite finding categories (e.g. "two integration findings") not finding IDs.
* The reader is making a yes/no shipping decision in 5 minutes.""",

    "sme": """You are summarizing a software release test result for SUBJECT-MATTER EXPERT software engineers and architects.

Constraints:
* Technical, specific, anchored in the data. No hand-waving.
* Reproduction details for every cited bug.
* Cite finding IDs (NHI-D-*, S<id>) and link to their reproduction steps.
* Maximum 800 words.
* Sections: cert-run-summary, failure-analysis, NHI-consensus-analysis, performance-characterization, SAL-contract-gaps, v0.7.0.1-PR-backlog.
* The reader will read the raw JSON; your job is the guided tour.
* Use markdown headers and bullet lists.
* Architectural implications matter — name the pattern, not just the symptom.""",
}


def load_summary(run_dir: pathlib.Path) -> dict[str, Any]:
    p = run_dir / "a2a-summary.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text())


def load_consensus(run_dir: pathlib.Path) -> dict[str, Any]:
    for candidate in (
        run_dir / "nhi-findings" / "S85-consensus.json",
        run_dir / "nhi-findings" / "85-consensus.json",
    ):
        if candidate.is_file():
            return json.loads(candidate.read_text())
    return {}


def tally(summary: dict[str, Any]) -> dict[str, int]:
    scenarios = summary.get("scenarios") or summary.get("results") or []
    p = f = s = 0
    for sc in scenarios:
        if sc.get("skipped"):
            s += 1
        elif sc.get("pass") is True:
            p += 1
        elif sc.get("pass") is False:
            f += 1
    return {"pass": p, "fail": f, "skip": s, "total": p + f + s}


def fmt_tls(summary: dict[str, Any]) -> str:
    scenarios = summary.get("scenarios") or summary.get("results") or []
    handshakes = []
    for sc in scenarios:
        h = sc.get("tls_handshake") or {}
        if h.get("count"):
            handshakes.append((h["count"], h.get("mean_seconds", 0)))
    if not handshakes:
        return "—"
    total = sum(c for c, _ in handshakes)
    weighted_mean = sum(c * m for c, m in handshakes) / max(total, 1)
    return f"{total} hs, p̄={weighted_mean*1000:.1f}ms"


def render_verdict(r1: dict, r2: dict) -> str:
    t1 = tally(r1); t2 = tally(r2)
    if t1["fail"] == 0 and t2["fail"] == 0 and t1["pass"] > 0 and t2["pass"] > 0:
        return "**SHIP**"
    if t2["fail"] == 0 and t1["fail"] > 0:
        return "**HOLD** (R1 had failures, R2 GREEN — re-run for stability)"
    return "**BLOCK** (failures present)"


def generate_audience(audience: str, payload: dict) -> str:
    """Call Grok 4.2 reasoning to generate one audience narrative."""
    if not os.environ.get("XAI_API_KEY"):
        return f"_(skipped — XAI_API_KEY not set; audience='{audience}')_"
    sys_prompt = AUDIENCE_PROMPTS[audience]
    user_prompt = (
        "Source data (cert run results + NHI findings):\n\n"
        + json.dumps(payload, indent=2, default=str)[:30000]
        + "\n\nWrite the narrative now, following your audience constraints exactly."
    )
    out = grok_chat(prompt=user_prompt, system_msg=sys_prompt,
                    max_tokens=2048, temperature=0.4)
    if out.get("error"):
        return f"_(generation failed: {out['error']})_"
    return out.get("text") or "_(empty response)_"


def render(track: str, r1_dir: pathlib.Path, r2_dir: pathlib.Path,
           use_llm: bool) -> str:
    r1 = load_summary(r1_dir)
    r2 = load_summary(r2_dir)
    consensus = load_consensus(r2_dir)
    t1 = tally(r1); t2 = tally(r2)
    verdict = render_verdict(r1, r2)

    payload_for_llm = {
        "track": track,
        "r1_summary": {"tally": t1, "wall_seconds": r1.get("wall_seconds")},
        "r2_summary": {"tally": t2, "wall_seconds": r2.get("wall_seconds")},
        "verdict": verdict,
        "tls": {"r1": fmt_tls(r1), "r2": fmt_tls(r2)},
        "consensus_findings": consensus.get("consensus") or [],
        "openclaw_only_findings": consensus.get("openclaw_only") or [],
        "hermes_only_findings": consensus.get("hermes_only") or [],
        "failures_r1": [
            {"id": s.get("scenario"), "reason": s.get("reason")}
            for s in (r1.get("scenarios") or []) if s.get("pass") is False
        ],
        "failures_r2": [
            {"id": s.get("scenario"), "reason": s.get("reason")}
            for s in (r2.get("scenarios") or []) if s.get("pass") is False
        ],
    }

    if use_llm:
        non_tech = generate_audience("non_technical", payload_for_llm)
        c_level = generate_audience("c_level", payload_for_llm)
        sme = generate_audience("sme", payload_for_llm)
    else:
        non_tech = "_(--no-llm passed; rerun without flag to populate)_"
        c_level = non_tech
        sme = non_tech

    by_sev_consensus = _bucketize(consensus.get("consensus") or [], "openclaw_severity")
    by_sev_oc_only = _bucketize(consensus.get("openclaw_only") or [], "severity")
    by_sev_hm_only = _bucketize(consensus.get("hermes_only") or [], "severity")

    sev_rows = []
    for sev in ("critical", "high", "medium", "low", "info"):
        sev_rows.append(
            f"| {sev.title()} | {by_sev_consensus.get(sev, 0)} | "
            f"{by_sev_oc_only.get(sev, 0)} | {by_sev_hm_only.get(sev, 0)} | "
            f"{by_sev_consensus.get(sev, 0) + by_sev_oc_only.get(sev, 0) + by_sev_hm_only.get(sev, 0)} |"
        )

    md = f"""# GPU autonomous-tier cert (v0.7.0) — Track {track} results

!!! info "Run status"
    **Verdict:** {verdict}
    **Track:** {track}
    **R1 dir:** `{r1_dir}`
    **R2 dir:** `{r2_dir}`

## Headline results

### Cert verdict

| Track | R1 (P/F/S)              | R2 (P/F/S)              | Cert verdict |
|-------|-------------------------|-------------------------|:-------------|
| {track}    | {t1["pass"]}/{t1["fail"]}/{t1["skip"]} | {t2["pass"]}/{t2["fail"]}/{t2["skip"]} | {verdict}    |

### TLS handshake telemetry

| Round | Summary (count, mean) |
|-------|-----------------------|
| R1    | {fmt_tls(r1)}         |
| R2    | {fmt_tls(r2)}         |

### NHI discovery findings (consensus + single-observer)

| Severity | Consensus (both NHI agents) | openclaw-only | hermes-only | Total |
|----------|----------------------------:|--------------:|------------:|------:|
{chr(10).join(sev_rows)}

---

## NHI analysis — three audiences, same data

### Audience 1 — Non-technical

{non_tech}

### Audience 2 — C-Level / decision-makers

{c_level}

### Audience 3 — Subject-matter experts (engineers / architects)

{sme}

---

## Raw artifacts

* R1 summary: `{r1_dir}/a2a-summary.json`
* R2 summary: `{r2_dir}/a2a-summary.json`
* NHI consensus: `{r2_dir}/nhi-findings/S85-consensus.json`
* Per-NHI-agent findings: `{r2_dir}/nhi-findings/S85-{{openclaw,hermes}}.json`

---

_Generated by `scripts/render_gpu_results.py` from the run directories above.
The three-audience analysis was generated by Grok-4.2-reasoning against
the same source JSON; no hand-editing applied._
"""
    return md


def _bucketize(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        v = str(item.get(key) or "unknown").lower()
        out[v] = out.get(v, 0) + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", required=True, choices=("A1", "A2", "CPU", "Q"))
    ap.add_argument("--r1-dir", required=True, type=pathlib.Path)
    ap.add_argument("--r2-dir", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--no-llm", action="store_true",
                    help="skip Grok generation; useful for offline preview")
    args = ap.parse_args()

    md = render(args.track, args.r1_dir, args.r2_dir, use_llm=not args.no_llm)
    args.out.write_text(md)
    print(f"wrote {len(md)} bytes to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
