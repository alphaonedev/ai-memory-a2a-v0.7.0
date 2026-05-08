#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 5 — Consolidation + curation.

All 3 agents write 3 related memories each. A /api/v1/consolidate call is
issued against the namespace. The consolidated memory's
metadata.consolidated_from_agents must list all 3 contributing writers.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "5"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    ns = "scenario5-consolidate"
    agents = [("ai:alice", h.node1_ip), ("ai:bob", h.node2_ip), ("ai:charlie", h.node3_ip)]

    log("phase A: each agent writes 3 related memories")
    for aid, ip in agents:
        log(f"  {aid} on {ip}")
        for i in (1, 2, 3):
            u = new_uuid(f"c-{aid}-{i}-")
            h.write_memory(ip, aid, ns,
                           title=f"c-{aid}-{i}",
                           content=f"observation {i} from {aid}: {u}")
    h.settle(8, reason="quorum fanout")

    log("phase B: collect source ids on node-1, then trigger consolidate")
    rc, resp = h.list_memories(h.node1_ip, ns, limit=100)
    ids: list[str] = []
    if isinstance(resp, dict):
        ids = [m.get("id") for m in (resp.get("memories") or []) if m.get("id")]
    log(f"  source ids (count={len(ids)}): {ids[:5]}...")

    rc2, cons_doc = h.http_on(h.node1_ip, "POST", "/api/v1/consolidate",
                              body={"ids": ids, "title": "c-consolidated",
                                    "summary": "scenario-5 consolidation across alice, bob, charlie",
                                    "namespace": ns},
                              agent_id="ai:alice",
                              include_status=True)
    cons_code = (cons_doc or {}).get("http_code", 0) if isinstance(cons_doc, dict) else 0
    cons_body = (cons_doc or {}).get("body") if isinstance(cons_doc, dict) else None
    consolidated_id = ""
    if isinstance(cons_body, dict):
        consolidated_id = cons_body.get("id") or cons_body.get("memory_id") or cons_body.get("consolidated_memory_id") or ""
    log(f"  consolidate HTTP {cons_code}, consolidated_id={consolidated_id}")
    h.settle(10, reason="consolidation fanout")

    log("phase C: verifying consolidated_from_agents on node-4")
    agents_field: list[str] = []
    if consolidated_id:
        rc3, doc = h.get_memory(h.node4_ip, consolidated_id)
        if isinstance(doc, dict):
            mem = doc.get("memory") or {}
            md = mem.get("metadata") or {}
            agents_field = md.get("consolidated_from_agents") or md.get("consolidated_from") or []
            if not isinstance(agents_field, list):
                agents_field = []
    log(f"  consolidated_from_agents={agents_field}")

    reasons: list[str] = []
    passed = True
    if cons_code not in (200, 201):
        passed = False
        reasons.append(f"consolidate endpoint returned HTTP {cons_code} — may not exist in this ai-memory version")
    if not consolidated_id:
        passed = False
        reasons.append("consolidate did not return a new memory id")
    for required in ("ai:alice", "ai:bob", "ai:charlie"):
        if required not in agents_field:
            passed = False
            reasons.append(f"consolidated_from_agents missing {required}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        consolidated_id=consolidated_id,
        consolidate_http_code=cons_code,
        consolidated_from_agents=agents_field,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
