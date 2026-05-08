#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 17 — Stats consistency across peers.

All 3 agents write 5 memories each to a dedicated namespace. After
settle, the 4 peers (alice/bob/charlie + node-4 aggregator) must all
return the SAME count.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "17"
PER_AGENT = 5


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    ns = "scenario17-stats"
    expected = 3 * PER_AGENT
    agents = [("ai:alice", h.node1_ip), ("ai:bob", h.node2_ip), ("ai:charlie", h.node3_ip)]

    log(f"phase A: each of 3 agents writes {PER_AGENT} memories to {ns}")
    for aid, ip in agents:
        log(f"  {aid} on {ip}")
        for i in range(1, PER_AGENT + 1):
            u = new_uuid(f"stats-{aid}-{i}-")
            # Title includes AID so UPSERT on (title, namespace) doesn't dedup.
            h.write_memory(ip, aid, ns, title=f"stats-{aid}-{i}", content=u)
    h.settle(15, reason="W=2 fanout")

    log("phase B: querying count on every peer")
    counts: dict[str, int] = {}
    for name, ip in (("node-1", h.node1_ip), ("node-2", h.node2_ip),
                     ("node-3", h.node3_ip), ("node-4", h.node4_ip)):
        _, resp = h.list_memories(ip, ns, limit=200)
        n = len((resp or {}).get("memories") or []) if isinstance(resp, dict) else 0
        counts[name] = n
        log(f"  {name} count={n} (expected {expected})")

    reasons: list[str] = []
    passed = True
    for name in ("node-1", "node-2", "node-3", "node-4"):
        if counts.get(name, 0) != expected:
            passed = False
            reasons.append(f"{name} count={counts.get(name, 0)} != expected {expected}")
    if len(set(counts.values())) != 1:
        passed = False
        reasons.append(f"peer counts diverge — {counts}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        expected_count=expected,
        per_peer={n.replace("-", "_"): c for n, c in counts.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
