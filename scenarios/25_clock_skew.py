#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 25 — Clock skew tolerance.

Offset node-3's clock by +300s. alice writes from node-1 (normal clock).
Quorum fanout must still converge to node-3; node-3's memory_list must
include the write. Clock is reverted on exit regardless of pass/fail.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "25"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario25-clock"
    marker = new_uuid("ck-")

    # 1. Disable NTP and shift node-3 clock +300s.
    log("shifting node-3 clock +300s (NTP disabled for the duration)")
    shift_cmd = (
        "timedatectl set-ntp false 2>/dev/null || true; "
        "new_ts=$(( $(date +%s) + 300 )); "
        "date -u -s \"@$new_ts\" >/dev/null 2>&1 || true; "
        "date -u"
    )
    r = h.ssh_exec(h.node3_ip, shift_cmd, timeout=15)
    log(f"  node-3 now reports: {(r.stdout or '').strip()}")

    try:
        log("alice writes on node-1 (normal clock); waiting for quorum fanout to skewed node-3")
        h.write_memory(h.node1_ip, "ai:alice", ns,
                       title="ck-alice",
                       content=f"clock-skew-marker={marker}",
                       tier="long")
        h.settle(15, reason="skewed-peer convergence")

        seen_n3 = h.count_matching(h.node3_ip, ns, content_contains=marker, limit=20)
        log(f"  node-3 (+300s clock) sees marker: {seen_n3} (expected >=1)")
        seen_n1 = h.count_matching(h.node1_ip, ns, content_contains=marker, limit=20)
        log(f"  node-1 sees marker: {seen_n1} (expected >=1)")

        reasons: list[str] = []
        passed = True
        if seen_n3 < 1:
            passed = False
            reasons.append("node-3 (+300s clock) did not converge to alice's write")
        if seen_n1 < 1:
            passed = False
            reasons.append("node-1 lost its own write — vector clock regression")
        if seen_n3 > 5:
            passed = False
            reasons.append(f"node-3 saw {seen_n3} copies — duplicate replication from clock skew")

        h.emit(
            passed=passed,
            reason="; ".join(reasons) if reasons else "",
            marker=marker,
            clock_offset_seconds=300,
            target_node="node-3",
            seen_on={"node_1": seen_n1, "node_3": seen_n3},
            reasons=reasons,
        )
    finally:
        log("reverting node-3 clock")
        revert_cmd = (
            "rev_ts=$(( $(date +%s) - 300 )); "
            "date -u -s \"@$rev_ts\" >/dev/null 2>&1 || true; "
            "timedatectl set-ntp true 2>/dev/null || true"
        )
        h.ssh_exec(h.node3_ip, revert_cmd, timeout=15)


if __name__ == "__main__":
    main()
