#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 9 — Mutation round-trip.

alice writes M1 content=v1. bob updates M1 content=v2. charlie reads M1
and verifies content is v2 AND metadata.agent_id is still ai:alice
(Task 1.2 immutability — original writer identity is never overwritten).
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "9"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario9-mutation"
    v1 = new_uuid("v1-")
    v2 = new_uuid("v2-")

    log(f"alice writes M1 content={v1} on node-1")
    rc, resp = h.write_memory(h.node1_ip, "ai:alice", ns, title="m1", content=v1)
    m1_id = ""
    if isinstance(resp, dict):
        m1_id = resp.get("id") or resp.get("memory_id") or ""
    log(f"  M1 id={m1_id}")
    h.settle(5, reason="initial replication")

    log(f"bob updates M1 content={v2} on node-2 via PUT")
    rc_u, update_doc = h.update_memory(h.node2_ip, m1_id, "ai:bob", updates={"content": v2})
    update_code = (update_doc or {}).get("http_code", 0) if isinstance(update_doc, dict) else 0
    log(f"  PUT returned HTTP {update_code}")
    h.settle(8, reason="update fanout")

    log("charlie reads M1 on node-3 and checks content + provenance")
    rc_g, doc = h.get_memory(h.node3_ip, m1_id)
    mem = (doc or {}).get("memory") if isinstance(doc, dict) else {}
    charlie_content = (mem or {}).get("content") or ""
    charlie_agent_id = ((mem or {}).get("metadata") or {}).get("agent_id") or ""
    log(f"  charlie sees content=\"{charlie_content}\" agent_id=\"{charlie_agent_id}\"")

    reasons: list[str] = []
    passed = True
    if charlie_content != v2:
        passed = False
        reasons.append(f"charlie expected content={v2} got \"{charlie_content}\"")
    if charlie_agent_id != "ai:alice":
        passed = False
        reasons.append(f"metadata.agent_id changed from ai:alice to \"{charlie_agent_id}\" — Task 1.2 breach")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m1_id=m1_id,
        v1_uuid=v1,
        v2_uuid=v2,
        put_http_code=update_code,
        charlie_view={"content": charlie_content, "agent_id": charlie_agent_id},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
