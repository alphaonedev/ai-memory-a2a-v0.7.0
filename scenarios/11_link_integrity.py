#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 11 — Link integrity.

alice writes M1 on node-1; bob writes M2 on node-2; alice links M1→M2
with relation=related_to; charlie on node-3 queries links for M1 and
must see M2.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "11"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario11-link"
    m1_content = new_uuid("l1-")
    m2_content = new_uuid("l2-")

    log("alice writes M1 on node-1")
    _, resp_a = h.write_memory(h.node1_ip, "ai:alice", ns, title="m1", content=m1_content)
    m1_id = (resp_a or {}).get("id") or (resp_a or {}).get("memory_id") or "" if isinstance(resp_a, dict) else ""

    log("bob writes M2 on node-2")
    _, resp_b = h.write_memory(h.node2_ip, "ai:bob", ns, title="m2", content=m2_content)
    m2_id = (resp_b or {}).get("id") or (resp_b or {}).get("memory_id") or "" if isinstance(resp_b, dict) else ""
    log(f"  M1={m1_id} M2={m2_id}")
    h.settle(5, reason="pre-link replication")

    log("alice links M1 -> M2 with relation=related_to")
    _, link_doc = h.http_on(h.node1_ip, "POST", "/api/v1/links",
                            body={"source_id": m1_id, "target_id": m2_id, "relation": "related_to"},
                            agent_id="ai:alice", include_status=True)
    link_code = (link_doc or {}).get("http_code", 0) if isinstance(link_doc, dict) else 0
    log(f"  link POST returned HTTP {link_code}")
    h.settle(8, reason="link fanout")

    log("charlie queries links of M1 on node-3")
    _, links_resp = h.http_on(h.node3_ip, "GET", f"/api/v1/links/{m1_id}")
    sees_m2 = 0
    if isinstance(links_resp, dict):
        pool = (links_resp.get("links") or [])
        if not pool and isinstance(links_resp, list):
            pool = links_resp
        for lk in pool:
            if isinstance(lk, dict):
                tgt = lk.get("target_id") or lk.get("to") or lk.get("target") or ""
                if tgt == m2_id:
                    sees_m2 += 1
    log(f"  charlie sees M1->M2 link: {sees_m2} (expected >=1)")

    reasons: list[str] = []
    passed = True
    if link_code not in (200, 201):
        passed = False
        reasons.append(f"link POST returned HTTP {link_code} — endpoint may not exist")
    if sees_m2 < 1:
        passed = False
        reasons.append("charlie could not see M1->M2 link after settle")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m1_id=m1_id,
        m2_id=m2_id,
        relation="related_to",
        link_http_code=link_code,
        charlie_sees_link=sees_m2,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
