#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 26 — Mixed-framework A2A (ironclaw + hermes on same VPC).

Requires `agent_group=mixed` dispatch — skipped otherwise. The
provisioning side (terraform + setup_node.sh) is responsible for
giving us a heterogeneous topology where at least one node runs
ironclaw and at least one runs hermes. This scenario only verifies
the A2A invariant: a memory written by an ironclaw-driven agent on
one node must be readable by a hermes-driven agent on another, with
metadata.agent_id preserved in both directions.

Pass criteria:
  * alice's ironclaw-side write visible on the hermes peer within
    quorum settle, with metadata.agent_id == ai:alice
  * bob's hermes-side write visible on the ironclaw peer within
    quorum settle, with metadata.agent_id == ai:bob
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log


SCENARIO_ID = "26"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)

    if h.agent_group != "mixed":
        h.skip(reason=f"scenario 26 only runs under agent_group=mixed (actual: {h.agent_group})")

    ns = "scenario26-mixed-framework"
    alice_content = h.new_uuid("alice-")
    bob_content = h.new_uuid("bob-")

    # Phase A: alice (on node-1, ironclaw-driven in the mixed topology)
    # writes a memory.
    log("phase A: ai:alice writes on node-1")
    rc, resp = h.write_memory(
        h.node1_ip, "ai:alice", ns,
        title="alice-writes-from-ironclaw",
        content=alice_content,
        metadata={"origin_framework": "ironclaw"},
    )
    if rc != 0:
        h.emit(passed=False, reason=f"alice write failed rc={rc}", write_resp=resp)

    # Phase B: bob (on node-2, hermes-driven) writes.
    log("phase B: ai:bob writes on node-2")
    rc, resp = h.write_memory(
        h.node2_ip, "ai:bob", ns,
        title="bob-writes-from-hermes",
        content=bob_content,
        metadata={"origin_framework": "hermes"},
    )
    if rc != 0:
        h.emit(passed=False, reason=f"bob write failed rc={rc}", write_resp=resp)

    h.settle(8, reason="mixed-framework quorum fanout")

    # Phase C: verify cross-visibility.
    # bob (hermes-driven) on node-2 must see alice's ironclaw-written row.
    log("phase C: ai:bob on node-2 lists namespace, expects alice's row")
    rc, bob_sees = h.list_memories(h.node2_ip, ns)
    if rc != 0 or not isinstance(bob_sees, dict):
        h.emit(passed=False, reason=f"bob list failed rc={rc}", response=bob_sees)
    alice_rows = [
        m for m in bob_sees.get("memories", [])
        if m.get("content") == alice_content
        and (m.get("metadata") or {}).get("agent_id") == "ai:alice"
    ]

    # alice (ironclaw-driven) on node-1 must see bob's hermes-written row.
    log("phase C: ai:alice on node-1 lists namespace, expects bob's row")
    rc, alice_sees = h.list_memories(h.node1_ip, ns)
    if rc != 0 or not isinstance(alice_sees, dict):
        h.emit(passed=False, reason=f"alice list failed rc={rc}", response=alice_sees)
    bob_rows = [
        m for m in alice_sees.get("memories", [])
        if m.get("content") == bob_content
        and (m.get("metadata") or {}).get("agent_id") == "ai:bob"
    ]

    passed = len(alice_rows) == 1 and len(bob_rows) == 1
    reason = ""
    if not passed:
        reason = (
            f"mixed-framework A2A incomplete: alice->bob visible={len(alice_rows)}, "
            f"bob->alice visible={len(bob_rows)}"
        )
    h.emit(
        passed=passed,
        reason=reason,
        alice_seen_by_hermes=len(alice_rows),
        bob_seen_by_ironclaw=len(bob_rows),
        namespace=ns,
    )


if __name__ == "__main__":
    main()
