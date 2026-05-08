#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 13 — Concurrent write contention.

alice and bob concurrently PUT the same memory M1 with different content.
After settle, all 4 peers must agree on ONE final value. Resolution
strategy (LWW / CRDT / vector-clock) doesn't matter here; what matters
is consistency — no split-brain.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "13"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    ns = "scenario13-contention"
    v0 = new_uuid("v0-")
    vA = new_uuid("va-")
    vB = new_uuid("vb-")

    log(f"alice writes M1 content={v0} on node-1")
    _, resp = h.write_memory(h.node1_ip, "ai:alice", ns, title="m1", content=v0)
    m1_id = (resp or {}).get("id") or (resp or {}).get("memory_id") or "" if isinstance(resp, dict) else ""
    log(f"  M1 id={m1_id}")
    h.settle(5, reason="initial replication")

    log(f"alice + bob issue concurrent PUTs (vA={vA} from alice, vB={vB} from bob)")
    results = h.run_parallel(
        lambda ip, aid, val: h.update_memory(ip, m1_id, aid, updates={"content": val}),
        [(h.node1_ip, "ai:alice", vA), (h.node2_ip, "ai:bob", vB)],
        max_workers=2,
    )
    log(f"  concurrent PUT results: {[r for r in results]}")
    h.settle(10, reason="quorum convergence")

    contents: dict[str, str] = {}
    for name, ip in (("node-1", h.node1_ip), ("node-2", h.node2_ip),
                     ("node-3", h.node3_ip), ("node-4", h.node4_ip)):
        _, doc = h.get_memory(ip, m1_id)
        c = ""
        if isinstance(doc, dict):
            c = ((doc.get("memory") or {}).get("content")) or ""
        contents[name] = c or "(none)"
        log(f"  {name} sees content={contents[name]}")

    unique = set(contents.values())
    reasons: list[str] = []
    passed = True
    if len(unique) != 1:
        passed = False
        reasons.append(f"split-brain: peers disagree — {contents}")
    winning = contents.get("node-1", "")
    if winning in (v0, "", "(none)"):
        passed = False
        reasons.append(f"winning content is not one of the submitted PUT values: got \"{winning}\"")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m1_id=m1_id,
        submitted={"v0": v0, "vA_alice": vA, "vB_bob": vB},
        peer_view={n.replace("-", "_"): c for n, c in contents.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
