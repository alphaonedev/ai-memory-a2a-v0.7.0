#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 2 — Shared-context handoff.

alice drops a handoff memory on node-1; bob picks it up on node-2 within
quorum settle and writes an ack; alice reads bob's ack back. Minimum
viable multi-agent coordination through shared memory.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "2"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario2-handoff"
    handoff_uuid = new_uuid("h-")
    ack_uuid = new_uuid("a-")

    log(f"phase A: ai:alice writes handoff to ai:bob (uuid={handoff_uuid})")
    h.write_memory(h.node1_ip, "ai:alice", ns,
                   title="handoff-to-bob",
                   content=handoff_uuid,
                   priority=7,
                   metadata={"role": "handoff", "target": "ai:bob"})

    h.settle(8, reason="quorum fanout")

    log("phase B: ai:bob reads handoff on node-2")
    bob_sees = h.count_matching(h.node2_ip, ns,
                                content_equals=handoff_uuid,
                                agent_id="ai:alice", limit=20)
    log(f"  ai:bob sees {bob_sees} handoff memories from ai:alice")

    log(f"phase C: ai:bob writes acknowledgement (uuid={ack_uuid})")
    h.write_memory(h.node2_ip, "ai:bob", ns,
                   title="ack-from-bob",
                   content=ack_uuid,
                   priority=7,
                   metadata={"role": "ack", "target": "ai:alice"})

    h.settle(8, reason="reverse-direction fanout")

    log("phase D: ai:alice reads ack on node-1")
    alice_sees = h.count_matching(h.node1_ip, ns,
                                  content_equals=ack_uuid,
                                  agent_id="ai:bob", limit=20)
    log(f"  ai:alice sees {alice_sees} ack memories from ai:bob")

    reasons: list[str] = []
    passed = True
    if bob_sees < 1:
        passed = False
        reasons.append("ai:bob did not see handoff from ai:alice after 8s settle")
    if alice_sees < 1:
        passed = False
        reasons.append("ai:alice did not see ack from ai:bob after 8s settle")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        path="serve-http",
        per_agent={
            "ai:bob": {"sees_handoff": bob_sees},
            "ai:alice": {"sees_ack": alice_sees},
        },
        handoff_uuid=handoff_uuid,
        ack_uuid=ack_uuid,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
