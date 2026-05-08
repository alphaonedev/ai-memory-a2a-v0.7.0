#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 4 — Federation-aware concurrent writes (burst).

Three agents concurrently write 30 memories each (90 total) in a fast
burst. After settle, node-4 aggregator must see all 30 per namespace
with correct metadata.agent_id.  A2A-level regression for PR #309.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "4"
PER_AGENT = 30


def burst_writer(h: Harness, aid: str, ip: str) -> int:
    """Write PER_AGENT memories from `aid` on `ip`. Returns write-success count."""
    ns = f"scenario4-fed-{aid}"
    ok = 0
    # Fire writes in small bursts of 10, without waiting for each one.
    for i in range(1, PER_AGENT + 1):
        rc, _ = h.write_memory(ip, aid, ns,
                               title=f"fed-{i}",
                               content=f"fed-{aid}-{i}-{new_uuid()}")
        if rc == 0:
            ok += 1
    return ok


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    agents = [("ai:alice", h.node1_ip), ("ai:bob", h.node2_ip), ("ai:charlie", h.node3_ip)]

    log(f"phase A: launching concurrent {PER_AGENT}-row bursts from 3 agents")
    results = h.run_parallel(burst_writer, [(h, aid, ip) for aid, ip in agents], max_workers=3)
    for (aid, _), got in zip(agents, results):
        log(f"  {aid} burst ok={got}/{PER_AGENT}")

    h.settle(20, reason="W=2 fanout convergence")

    log("phase B: querying node-4 aggregator for per-agent counts")
    per_agent: dict[str, dict[str, int]] = {}
    reasons: list[str] = []
    passed = True
    for aid, _ in agents:
        ns = f"scenario4-fed-{aid}"
        total, wrong = h.count_wrong_agent_id(h.node4_ip, ns, aid, limit=200)
        per_agent[aid] = {"count": total, "wrong_agent_id": wrong}
        log(f"  {aid}: count={total} (expected {PER_AGENT}) wrong_agent_id={wrong}")
        if total != PER_AGENT:
            passed = False
            reasons.append(f"{aid}: node-4 saw {total} rows, expected {PER_AGENT}")
        if wrong != 0:
            passed = False
            reasons.append(f"{aid}: {wrong} rows have wrong agent_id")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        expected_per_agent=PER_AGENT,
        per_agent=per_agent,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
