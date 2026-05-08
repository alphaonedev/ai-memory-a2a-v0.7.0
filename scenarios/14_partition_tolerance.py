#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 14 — Partition tolerance.

Suspend node-3's ai-memory serve (SIGSTOP). While partitioned, alice and
bob write memories — W=2 quorum of (alice+bob) still satisfies. Resume
node-3 (SIGCONT). After settle, node-3 must see all writes made during
its outage.  A2A-level regression for PR #309.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "14"
WRITES = 10


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario14-partition"

    log("suspending ai-memory on node-3 (SIGSTOP)")
    h.ssh_exec(h.node3_ip, "pgrep -f 'ai-memory serve' | xargs -r kill -STOP", timeout=15)
    h.settle(2, reason="process-suspend observe")

    log(f"writing {WRITES} memories each from alice + bob during node-3 outage")
    uuids: list[str] = []
    for i in range(1, WRITES + 1):
        u_alice = new_uuid(f"pa-{i}-")
        u_bob = new_uuid(f"pb-{i}-")
        uuids.append(u_alice)
        uuids.append(u_bob)
        h.run_parallel(
            lambda ip, aid, title, content: h.write_memory(
                ip, aid, ns, title=title, content=content),
            [
                (h.node1_ip, "ai:alice", f"p-alice-{i}", u_alice),
                (h.node2_ip, "ai:bob", f"p-bob-{i}", u_bob),
            ],
            max_workers=2,
        )

    log("resuming ai-memory on node-3 (SIGCONT)")
    h.ssh_exec(h.node3_ip, "pgrep -f 'ai-memory serve' | xargs -r kill -CONT", timeout=15)
    h.settle(20, reason="post-partition catchup")

    log("checking node-3 caught up")
    _, resp = h.list_memories(h.node3_ip, ns, limit=200)
    n3_count = 0
    if isinstance(resp, dict):
        n3_count = len(resp.get("memories") or [])
    expected = 2 * WRITES
    log(f"  node-3 sees {n3_count} memories in {ns} (expected {expected})")

    reasons: list[str] = []
    passed = True
    if n3_count < expected:
        passed = False
        reasons.append(
            f"node-3 only saw {n3_count}/{expected} writes after partition recovery"
        )

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        partition_target="node-3",
        expected_post_recovery=expected,
        node3_saw=n3_count,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
