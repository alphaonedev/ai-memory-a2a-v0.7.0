#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 16 — Tier promotion across peers.

alice writes M1 at tier=short; promotes to tier=long via
/api/v1/memories/{id}/promote; bob reads M1 on node-2 and must see
tier=long.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "16"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario16-tier"
    marker = new_uuid("t-")

    log("alice writes M1 tier=short on node-1")
    _, resp = h.write_memory(h.node1_ip, "ai:alice", ns, title="t1", content=marker, tier="short")
    m1_id = (resp or {}).get("id") or (resp or {}).get("memory_id") or "" if isinstance(resp, dict) else ""
    log(f"  M1 id={m1_id}")
    h.settle(5, reason="pre-promote replication")

    log("alice promotes M1 to tier=long")
    _, promo_doc = h.http_on(h.node1_ip, "POST", f"/api/v1/memories/{m1_id}/promote",
                             body={"tier": "long"}, agent_id="ai:alice", include_status=True)
    promote_code = (promo_doc or {}).get("http_code", 0) if isinstance(promo_doc, dict) else 0
    log(f"  promote returned HTTP {promote_code}")
    h.settle(8, reason="promotion fanout")

    _, doc = h.get_memory(h.node2_ip, m1_id)
    bob_tier = ""
    if isinstance(doc, dict):
        bob_tier = ((doc.get("memory") or {}).get("tier")) or "(missing)"
    log(f"  bob sees tier={bob_tier} (expected long)")

    reasons: list[str] = []
    passed = True
    if promote_code not in (200, 204):
        passed = False
        reasons.append(f"promote endpoint returned HTTP {promote_code}")
    if bob_tier != "long":
        passed = False
        reasons.append(f"bob sees tier=\"{bob_tier}\", expected \"long\"")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m1_id=m1_id,
        promote_http_code=promote_code,
        bob_sees_tier=bob_tier,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
