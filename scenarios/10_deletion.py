#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 10 — Deletion propagation.

alice writes M1, confirms visible on all peers, then DELETEs it. After
settle, bob / charlie / node-4 aggregator must NOT find M1. Tombstone
propagation test.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "10"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    ns = "scenario10-deletion"
    marker = new_uuid("d-")

    log(f"alice writes M1 content={marker} on node-1")
    rc, resp = h.write_memory(h.node1_ip, "ai:alice", ns, title="m1", content=marker)
    m1_id = ""
    if isinstance(resp, dict):
        m1_id = resp.get("id") or resp.get("memory_id") or ""
    log(f"  created memory id={m1_id}")
    h.settle(8, reason="pre-delete fanout")

    log("pre-delete: verifying M1 is visible on all peers")
    peer_list = [("node-2", h.node2_ip), ("node-3", h.node3_ip), ("node-4", h.node4_ip)]
    pre_visible = 0
    for name, ip in peer_list:
        n = h.count_matching(ip, ns, content_equals=marker, limit=20)
        log(f"  pre-delete {name} sees {n}")
        if n >= 1:
            pre_visible += 1

    log("alice deletes M1 on node-1")
    rc_d, del_doc = h.delete_memory(h.node1_ip, m1_id, "ai:alice")
    delete_code = (del_doc or {}).get("http_code", 0) if isinstance(del_doc, dict) else 0
    log(f"  DELETE returned HTTP {delete_code}")
    h.settle(15, reason="tombstone propagation")

    log("post-delete: verifying M1 is GONE from all peers")
    post_hit: dict[str, int] = {}
    post_visible = 0
    for name, ip in peer_list:
        n = h.count_matching(ip, ns, content_equals=marker, limit=20)
        post_hit[name] = n
        log(f"  post-delete {name} sees {n} (expected 0)")
        if n >= 1:
            post_visible += 1

    reasons: list[str] = []
    passed = True
    if pre_visible < 3:
        passed = False
        reasons.append(f"only {pre_visible}/3 peers saw M1 before delete — fanout issue")
    if post_visible > 0:
        passed = False
        reasons.append(f"{post_visible}/3 peers still see M1 after delete — tombstone not propagated")
    if delete_code not in (200, 204):
        passed = False
        reasons.append(f"DELETE returned HTTP {delete_code} — expected 200/204")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m1_id=m1_id,
        uuid=marker,
        delete_http_code=delete_code,
        pre_delete_visible_peers=pre_visible,
        post_delete_still_visible_peers=post_visible,
        post_delete_hits=post_hit,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
