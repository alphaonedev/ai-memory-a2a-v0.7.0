#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 1b — Per-agent write + read via LOCAL SERVE HTTP.

Federation-path variant of S1. Same product-level invariant (agents on
distinct nodes read each other's writes across the quorum mesh), but
takes the HTTP path (which triggers fanout) instead of the MCP stdio
path (which in v0.6.0 bypasses fanout — that's why S1 may stay RED
while 1b proves federation itself works).
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log


SCENARIO_ID = "1b"
WRITES_PER_AGENT = 10


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    agents = [("ai:alice", h.node1_ip), ("ai:bob", h.node2_ip), ("ai:charlie", h.node3_ip)]

    log(f"phase A: each agent POSTs {WRITES_PER_AGENT} memories to local serve")
    for aid, ip in agents:
        log(f"  {aid} on {ip}")
        for i in range(1, WRITES_PER_AGENT + 1):
            rc, _ = h.write_memory(
                ip, aid, f"scenario1b-{aid}",
                title=f"w{i}-{aid}",
                content=f"scenario1b write {i} from {aid} via HTTP",
            )
            if rc != 0:
                log(f"  !! write failed {aid} i={i}")

    h.settle(15, reason="W=2/N=4 convergence")

    log("phase B: count rows in other two namespaces via local serve HTTP")
    recall: dict[str, int] = {}
    for reader_aid, reader_ip in agents:
        total = 0
        for writer_aid, _ in agents:
            if writer_aid == reader_aid:
                continue
            total += h.count_matching(reader_ip, f"scenario1b-{writer_aid}", limit=100)
        recall[reader_aid] = total
        log(f"  {reader_aid} sees {total} rows from the other two namespaces")

    log("phase C: cross-cluster identity check on node-4")
    all_ok = True
    per_ns: dict[str, dict[str, int]] = {}
    for writer_aid, _ in agents:
        ns = f"scenario1b-{writer_aid}"
        total, wrong = h.count_wrong_agent_id(h.node4_ip, ns, writer_aid, limit=100)
        per_ns[ns] = {"count": total, "wrong_agent_id": wrong}
        log(f"  ns={ns} count={total} wrong_agent_id={wrong}")
        if total != WRITES_PER_AGENT or wrong != 0:
            all_ok = False

    expected = 2 * WRITES_PER_AGENT
    reasons: list[str] = []
    passed = True
    for aid in ("ai:alice", "ai:bob", "ai:charlie"):
        if recall.get(aid, 0) < expected:
            passed = False
            reasons.append(f"{aid} sees {recall.get(aid, 0)} < {expected} via serve HTTP")
    if not all_ok:
        passed = False
        reasons.append("cross-cluster identity check failed at node-4")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        path="serve-http",
        expected_per_reader=expected,
        per_agent={aid: {"recall": recall.get(aid, 0)} for aid, _ in agents},
        per_namespace_node4=per_ns,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
