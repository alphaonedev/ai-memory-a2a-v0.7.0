#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 22 — Identity spoofing resistance (Task 1.2 transport check).

Verifies:
  1. Header-only X-Agent-Id write stores metadata.agent_id matching header.
  2. Conflicting body.metadata.agent_id vs X-Agent-Id — stored value is
     one of the two declared identities (body or header), never a silent
     third value or swap.

Runs under any tls_mode.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "22"


def _stored_agent_id(h: Harness, node_ip: str, ns: str, marker: str) -> str:
    rc, resp = h.list_memories(node_ip, ns, limit=20)
    if not isinstance(resp, dict):
        return ""
    for m in resp.get("memories") or []:
        if isinstance(m, dict) and marker in (m.get("content") or ""):
            return ((m.get("metadata") or {}).get("agent_id")) or ""
    return ""


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario22-spoof"
    uuid_hdr = new_uuid("h-")
    uuid_body = new_uuid("b-")

    log("test 1: header-only X-Agent-Id=ai:alice")
    h.write_memory(h.node1_ip, "ai:alice", ns,
                   title="t1-header",
                   content=f"marker={uuid_hdr}", tier="long")
    h.settle(2, reason="read-settle")
    stored_hdr = _stored_agent_id(h, h.node1_ip, ns, uuid_hdr)
    log(f"  stored metadata.agent_id for header-only write: {stored_hdr} (expected ai:alice)")

    log("test 2: body.metadata.agent_id=ai:body-wins vs X-Agent-Id=ai:attacker")
    h.write_memory(h.node1_ip, "ai:attacker", ns,
                   title="t2-conflict",
                   content=f"marker={uuid_body}", tier="long",
                   metadata={"agent_id": "ai:body-wins"})
    h.settle(2, reason="read-settle")
    stored_body = _stored_agent_id(h, h.node1_ip, ns, uuid_body)
    log(f"  stored metadata.agent_id for body+header conflict: {stored_body}")

    reasons: list[str] = []
    passed = True
    if stored_hdr != "ai:alice":
        passed = False
        reasons.append(f"test 1: header-only write stored agent_id={stored_hdr} (expected ai:alice)")
    # Either body or header winning is acceptable per precedence contract; any
    # other value (silent swap to third identity) is a breach.
    if stored_body not in ("ai:body-wins", "ai:attacker"):
        passed = False
        reasons.append(f"test 2: conflicting write stored unexpected agent_id={stored_body}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        tests={
            "header_only": {"stored_agent_id": stored_hdr, "expected": "ai:alice"},
            "body_vs_header_conflict": {
                "stored_agent_id": stored_body,
                "acceptable": ["ai:body-wins", "ai:attacker"],
            },
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
