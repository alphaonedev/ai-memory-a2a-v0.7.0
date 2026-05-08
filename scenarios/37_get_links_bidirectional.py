#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 37 — memory_get_links explicit bidirectional traversal.

Distinct from S11 which tested forward traversal (link M1→M2, see M2
from M1). Here we confirm the reverse direction also resolves:
get_links(M2) must return M1 with relation "related_to" (or its
inverse).
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "37"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario37-links-bidir"

    log("alice writes M1 + M2 + links M1→M2")
    _, r1 = h.write_memory(h.node1_ip, "ai:alice", ns, title="m1", content=new_uuid("m1-"))
    m1 = (r1 or {}).get("id") or (r1 or {}).get("memory_id") or "" if isinstance(r1, dict) else ""
    _, r2 = h.write_memory(h.node2_ip, "ai:bob", ns, title="m2", content=new_uuid("m2-"))
    m2 = (r2 or {}).get("id") or (r2 or {}).get("memory_id") or "" if isinstance(r2, dict) else ""
    log(f"  M1={m1} M2={m2}")

    h.http_on(h.node1_ip, "POST", "/api/v1/links",
              body={"source_id": m1, "target_id": m2, "relation": "related_to"},
              agent_id="ai:alice")
    h.settle(6, reason="link fanout")

    log("charlie queries /api/v1/links/M1 (forward)")
    _, fwd = h.http_on(h.node3_ip, "GET", f"/api/v1/links/{m1}")
    fwd_has_m2 = False
    if isinstance(fwd, dict):
        for lk in (fwd.get("links") or []):
            if isinstance(lk, dict) and (lk.get("target_id") or lk.get("to") or "") == m2:
                fwd_has_m2 = True
                break

    log("charlie queries /api/v1/links/M2 (reverse)")
    _, rev = h.http_on(h.node3_ip, "GET", f"/api/v1/links/{m2}")
    rev_has_m1 = False
    if isinstance(rev, dict):
        for lk in (rev.get("links") or []):
            if isinstance(lk, dict):
                src = lk.get("source_id") or lk.get("from") or ""
                tgt = lk.get("target_id") or lk.get("to") or ""
                # Either direction of the pair is acceptable — the point
                # is that the link is discoverable from M2's side.
                if m1 in (src, tgt):
                    rev_has_m1 = True
                    break

    reasons: list[str] = []
    passed = True
    if not fwd_has_m2:
        passed = False
        reasons.append("forward get_links(M1) did not include M2")
    if not rev_has_m1:
        passed = False
        reasons.append("reverse get_links(M2) did not include M1")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m1=m1, m2=m2,
        forward_has_target=fwd_has_m2,
        reverse_has_source=rev_has_m1,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
