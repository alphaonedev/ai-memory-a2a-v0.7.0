#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 31 — memory_gc quiescence.

alice writes 4 memories; forgets 2; bob triggers gc. After settle, the
remaining 2 memories must be readable on all peers. GC must not corrupt
or delete live rows.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "31"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    ns = "scenario31-gc"
    markers: list[tuple[str, str]] = []  # (id, marker)

    log("alice writes 4 memories")
    for i in range(4):
        marker = new_uuid(f"gc-{i}-")
        _, resp = h.write_memory(h.node1_ip, "ai:alice", ns,
                                 title=f"gc-{i}",
                                 content=f"gc-marker={marker}")
        mid = (resp or {}).get("id") or (resp or {}).get("memory_id") or "" if isinstance(resp, dict) else ""
        markers.append((mid, marker))
    h.settle(6, reason="pre-gc replication")

    # Forget the first 2.
    log("alice forgets 2 via /api/v1/forget")
    forget_ids = [mid for mid, _ in markers[:2] if mid]
    _, forget_doc = h.http_on(h.node1_ip, "POST", "/api/v1/forget",
                              body={"ids": forget_ids}, agent_id="ai:alice",
                              include_status=True)
    forget_code = (forget_doc or {}).get("http_code", 0) if isinstance(forget_doc, dict) else 0
    log(f"  forget returned HTTP {forget_code}")
    h.settle(5, reason="forget propagation")

    log("bob triggers /api/v1/gc on node-2")
    _, gc_doc = h.http_on(h.node2_ip, "POST", "/api/v1/gc",
                          body={}, agent_id="ai:bob", include_status=True)
    gc_code = (gc_doc or {}).get("http_code", 0) if isinstance(gc_doc, dict) else 0
    log(f"  gc returned HTTP {gc_code}")
    h.settle(8, reason="post-gc settle")

    log("verify remaining 2 markers are still readable on every peer")
    expected_alive = [marker for _, marker in markers[2:]]
    per_peer: dict[str, int] = {}
    reasons: list[str] = []
    passed = True
    for name, ip in (("node-1", h.node1_ip), ("node-2", h.node2_ip),
                     ("node-3", h.node3_ip), ("node-4", h.node4_ip)):
        hits = 0
        for marker in expected_alive:
            hits += 1 if h.count_matching(ip, ns, content_contains=marker, limit=20) >= 1 else 0
        per_peer[name] = hits
        log(f"  {name} sees {hits}/{len(expected_alive)} live markers")
        if hits != len(expected_alive):
            passed = False
            reasons.append(f"{name}: only {hits}/{len(expected_alive)} live markers survived gc")

    if gc_code not in (200, 202, 204):
        passed = False
        reasons.append(f"gc returned HTTP {gc_code}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        forget_http_code=forget_code,
        gc_http_code=gc_code,
        live_markers_per_peer={n.replace("-", "_"): v for n, v in per_peer.items()},
        expected_live=len(expected_alive),
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
