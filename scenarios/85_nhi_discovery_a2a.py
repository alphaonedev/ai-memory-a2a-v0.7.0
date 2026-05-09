#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 85 — NHI discovery (A2A bilateral).

Runs S83 and S84 discovery loops in parallel against their respective
nodes, then cross-validates the findings: any finding observed by BOTH
agents (consensus) is upgraded to severity max(self, peer); single-agent
findings remain at their reported severity but are flagged
`single_observer=true`.

This is the highest-signal NHI mode — consensus across two independent
NHI runs is much stronger evidence than a single-run claim.

Cost note: 2× the Grok spend of S83 alone, but the harness fans out so
wall-clock is the same (~30 min if both budgets max out).

NON-GATING discovery mode. See S83 for protocol details.
"""
import concurrent.futures
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log  # noqa: E402
from nhi_discovery import (  # noqa: E402
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_TIME_BUDGET_S,
    discovery_verdict,
    run_discovery,
)


SCENARIO_ID = "85"


def _resolve_tier(h: Harness, node_ip: str) -> str:
    explicit = os.environ.get("NHI_TIER", "").strip().lower()
    if explicit in ("semantic", "autonomous"):
        return explicit
    rc, resp = h.http_on(node_ip, "GET", "/api/v1/capabilities",
                         include_status=True, timeout=10)
    body = (resp or {}).get("body") if isinstance(resp, dict) else {}
    if isinstance(body, dict):
        t = (body.get("tier") or body.get("memory_tier") or "").lower()
        return "autonomous" if t == "autonomous" else "semantic"
    return "semantic"


def _consensus_score(findings_a: list[dict], findings_b: list[dict]) -> dict:
    """Cross-validate findings between the two agents.

    Two findings are considered to agree if their `summary` fields share
    >= 4 normalized tokens (lowercased, alpha-only, len > 3) AND their
    `category` matches. This is a coarse heuristic — the human triage
    review reads both and confirms.
    """
    def normalize(s: str) -> set[str]:
        return {tok for tok in (
            "".join(c if c.isalnum() else " " for c in (s or "")).lower().split()
        ) if len(tok) > 3}

    consensus = []
    a_only = []
    b_only_idxs: set[int] = set(range(len(findings_b)))

    for fa in findings_a:
        ta = normalize(fa.get("summary") or "")
        ca = (fa.get("category") or "").lower()
        match_idx = None
        for j, fb in enumerate(findings_b):
            if j not in b_only_idxs:
                continue
            tb = normalize(fb.get("summary") or "")
            cb = (fb.get("category") or "").lower()
            if ca == cb and len(ta & tb) >= 4:
                match_idx = j
                break
        if match_idx is not None:
            fb = findings_b[match_idx]
            consensus.append({
                "category": fa.get("category"),
                "openclaw_summary": fa.get("summary"),
                "hermes_summary": fb.get("summary"),
                "openclaw_severity": fa.get("severity"),
                "hermes_severity": fb.get("severity"),
                "openclaw_id": fa.get("id"),
                "hermes_id": fb.get("id"),
            })
            b_only_idxs.discard(match_idx)
        else:
            a_only.append(fa)

    b_only = [findings_b[j] for j in b_only_idxs]
    return {
        "consensus": consensus,
        "openclaw_only": a_only,
        "hermes_only": b_only,
        "consensus_count": len(consensus),
        "single_observer_count": len(a_only) + len(b_only),
    }


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)

    if not os.environ.get("XAI_API_KEY"):
        h.skip("S85 requires XAI_API_KEY (Grok 4.2 reasoning driver)")

    tier_oc = _resolve_tier(h, h.node1_ip)
    tier_hm = _resolve_tier(h, h.node2_ip)
    log(f"  S85 tiers: openclaw={tier_oc} hermes={tier_hm}")

    run_dir = os.environ.get("RUN_DIR") or "."
    suffix = h.new_uuid()[:6]
    agent_oc = f"ai:s85-openclaw-{suffix}"
    agent_hm = f"ai:s85-hermes-{suffix}"
    findings_oc = pathlib.Path(run_dir) / "nhi-findings" / f"S{SCENARIO_ID}-openclaw.json"
    findings_hm = pathlib.Path(run_dir) / "nhi-findings" / f"S{SCENARIO_ID}-hermes.json"

    # Each agent gets its own Harness instance because http_on writes
    # TLS samples to a per-instance list and the consensus run is
    # bilateral — we want clean per-agent telemetry.
    h_oc = Harness.from_env(SCENARIO_ID)
    h_hm = Harness.from_env(SCENARIO_ID)

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_oc = ex.submit(
            run_discovery,
            h=h_oc, focus="openclaw", tier=tier_oc,
            agent_id=agent_oc, peer_label=agent_hm,
            findings_path=findings_oc,
            max_tool_calls=DEFAULT_MAX_TOOL_CALLS,
            time_budget_s=DEFAULT_TIME_BUDGET_S,
        )
        f_hm = ex.submit(
            run_discovery,
            h=h_hm, focus="hermes", tier=tier_hm,
            agent_id=agent_hm, peer_label=agent_oc,
            findings_path=findings_hm,
            max_tool_calls=DEFAULT_MAX_TOOL_CALLS,
            time_budget_s=DEFAULT_TIME_BUDGET_S,
        )
        sum_oc = f_oc.result()
        sum_hm = f_hm.result()
    duration = int(time.time() - t0)

    consensus = _consensus_score(
        sum_oc.get("findings") or [],
        sum_hm.get("findings") or [],
    )

    consensus_path = pathlib.Path(run_dir) / "nhi-findings" / f"S{SCENARIO_ID}-consensus.json"
    consensus_path.parent.mkdir(parents=True, exist_ok=True)
    consensus_path.write_text(json.dumps({
        "scenario": SCENARIO_ID,
        "duration_seconds": duration,
        "tiers": {"openclaw": tier_oc, "hermes": tier_hm},
        "openclaw_summary_path": str(findings_oc),
        "hermes_summary_path": str(findings_hm),
        **consensus,
    }, indent=2, default=str))

    pass_oc, _ = discovery_verdict(sum_oc)
    pass_hm, _ = discovery_verdict(sum_hm)
    passed = pass_oc and pass_hm
    reason = (
        f"oc_findings={len(sum_oc.get('findings') or [])} "
        f"hm_findings={len(sum_hm.get('findings') or [])} "
        f"consensus={consensus['consensus_count']} "
        f"single_observer={consensus['single_observer_count']}"
    )

    h.emit(
        passed=passed,
        reason=reason,
        nhi_consensus_count=consensus["consensus_count"],
        nhi_single_observer_count=consensus["single_observer_count"],
        nhi_openclaw_findings_count=len(sum_oc.get("findings") or []),
        nhi_hermes_findings_count=len(sum_hm.get("findings") or []),
        nhi_tool_calls_total=int(sum_oc.get("tool_calls") or 0)
                              + int(sum_hm.get("tool_calls") or 0),
        nhi_duration_seconds=duration,
        nhi_consensus_path=str(consensus_path),
        nhi_openclaw_findings_path=str(findings_oc),
        nhi_hermes_findings_path=str(findings_hm),
        nhi_openclaw_tier=tier_oc,
        nhi_hermes_tier=tier_hm,
    )


if __name__ == "__main__":
    main()
