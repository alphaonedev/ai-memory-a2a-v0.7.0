#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 1 — Per-agent write + read via MCP stdio (through the framework).

Each of 3 agents writes 10 memories via its local MCP path (drive_agent.sh),
then each agent counts rows from the OTHER two namespaces via local HTTP.
Node-4 aggregator independently verifies per-namespace totals and Task 1.2
metadata.agent_id immutability.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log


SCENARIO_ID = "1"
WRITES_PER_AGENT = 10


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    agents = [("ai:alice", h.node1_ip), ("ai:bob", h.node2_ip), ("ai:charlie", h.node3_ip)]

    # Phase A — each agent writes via MCP (drive_agent.sh store).
    log(f"phase A: each agent writes {WRITES_PER_AGENT} memories via MCP")
    for aid, ip in agents:
        log(f"  {aid} on {ip}")
        for i in range(1, WRITES_PER_AGENT + 1):
            r = h.drive_agent(ip, "store",
                              f"w{i}-{aid}",
                              f"scenario1 write {i} from {aid}",
                              f"scenario1-{aid}")
            if r.returncode != 0:
                log(f"  !! drive_agent store failed for {aid} i={i}: {r.stderr[:200]}")

    h.settle(15, reason="W=2/N=4 convergence")

    # Phase B — each reader counts rows in the OTHER two namespaces via local HTTP.
    log("phase B: each agent counts rows in the OTHER two namespaces")
    recall: dict[str, int] = {}
    for reader_aid, reader_ip in agents:
        total = 0
        for writer_aid, _ in agents:
            if writer_aid == reader_aid:
                continue
            total += h.count_matching(reader_ip, f"scenario1-{writer_aid}", limit=100)
        recall[reader_aid] = total
        log(f"  {reader_aid} recalled {total} rows from the other two namespaces")

    # Phase C — cross-cluster identity verification on node-4.
    log("phase C: cross-cluster identity check on node-4")
    all_ok = True
    per_ns: dict[str, dict[str, int]] = {}
    for writer_aid, _ in agents:
        ns = f"scenario1-{writer_aid}"
        total, wrong = h.count_wrong_agent_id(h.node4_ip, ns, writer_aid, limit=100)
        per_ns[ns] = {"count": total, "wrong_agent_id": wrong}
        log(f"  ns={ns} count={total} wrong_agent_id={wrong}")
        if total != WRITES_PER_AGENT:
            all_ok = False
            log(f"  !! expected {WRITES_PER_AGENT} rows, got {total}")
        if wrong != 0:
            all_ok = False

    # Verdict.
    expected_per_reader = 2 * WRITES_PER_AGENT
    reasons: list[str] = []
    passed = True
    for aid in ("ai:alice", "ai:bob", "ai:charlie"):
        got = recall.get(aid, 0)
        if got < expected_per_reader:
            passed = False
            reasons.append(f"{aid} recalled {got} < {expected_per_reader} via MCP")
    if not all_ok:
        passed = False
        reasons.append("cross-cluster identity check failed — see per_ns")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        expected_per_reader=expected_per_reader,
        per_agent={aid: {"recall": recall.get(aid, 0)} for aid, _ in agents},
        per_namespace_node4=per_ns,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
