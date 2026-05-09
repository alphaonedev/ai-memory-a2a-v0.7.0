#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 83 — NHI discovery (openclaw perspective).

Hands the openclaw daemon's HTTP surface to a Grok-4.2-reasoning driven
discovery loop with the openclaw focus prompt. Bug class targets:
storage write path / quota accounting / KG traversal / search & recall /
audit chain / capabilities truthfulness.

This scenario is NON-GATING (discovery mode). It always reports PASS
unless the harness itself broke (zero tool calls executed, or >50%
connectivity failure). Findings are written to the run dir for human
triage.

Default budget: 200 tool calls or 30 minutes wall, whichever first.
Override via NHI_MAX_TOOL_CALLS / NHI_TIME_BUDGET_S.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log  # noqa: E402
from nhi_discovery import (  # noqa: E402
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_TIME_BUDGET_S,
    discovery_verdict,
    run_discovery,
)


SCENARIO_ID = "83"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)

    if not os.environ.get("XAI_API_KEY"):
        h.skip("S83 requires XAI_API_KEY (Grok 4.2 reasoning driver)")

    # Tier auto-detection: capabilities probe can tell us if we're on
    # autonomous vs semantic. Operator override via NHI_TIER env.
    tier = os.environ.get("NHI_TIER", "").strip().lower()
    if tier not in ("semantic", "autonomous"):
        # Probe node1 capabilities; cheap and gives us tier
        rc, resp = h.http_on(h.node1_ip, "GET", "/api/v1/capabilities",
                             include_status=True, timeout=10)
        body = (resp or {}).get("body") if isinstance(resp, dict) else {}
        if isinstance(body, dict):
            t = (body.get("tier") or body.get("memory_tier") or "").lower()
            tier = "autonomous" if t == "autonomous" else "semantic"
        else:
            tier = "semantic"
    log(f"  NHI tier resolved: {tier}")

    # Findings dir under the campaign run
    run_dir = os.environ.get("RUN_DIR") or "."
    findings_path = pathlib.Path(run_dir) / "nhi-findings" / f"S{SCENARIO_ID}-openclaw.json"

    suffix = h.new_uuid()[:6]
    agent_id = f"ai:s83-openclaw-{suffix}"
    peer_label = f"ai:s83-hermes-{suffix}"

    summary = run_discovery(
        h=h, focus="openclaw", tier=tier,
        agent_id=agent_id, peer_label=peer_label,
        findings_path=findings_path,
        max_tool_calls=DEFAULT_MAX_TOOL_CALLS,
        time_budget_s=DEFAULT_TIME_BUDGET_S,
    )

    passed, reason = discovery_verdict(summary)
    h.emit(
        passed=passed,
        reason=reason,
        nhi_findings_count=len(summary.get("findings") or []),
        nhi_tool_calls=summary.get("tool_calls"),
        nhi_duration_seconds=summary.get("duration_seconds"),
        nhi_run_id=summary.get("run_id"),
        nhi_findings_by_severity=summary.get("finding_counts_by_severity"),
        nhi_findings_by_category=summary.get("finding_counts_by_category"),
        nhi_findings_path=str(findings_path),
        nhi_tier=tier,
    )


if __name__ == "__main__":
    main()
