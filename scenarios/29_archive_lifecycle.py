#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 29 — memory_archive lifecycle round-trip.

Exercises the full archive surface A2A:
  1. alice writes M1, then archives it (removed from active listing,
     preserved in archive).
  2. bob queries /api/v1/archive and sees M1.
  3. charlie restores M1 via /api/v1/archive/{id}/restore.
  4. node-4 aggregator sees M1 active again; /api/v1/archive/stats
     reflects the round-trip.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "29"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    ns = "scenario29-archive"
    marker = new_uuid("arc-")

    log("alice writes M1 on node-1")
    _, resp = h.write_memory(h.node1_ip, "ai:alice", ns, title="archive-me", content=marker)
    m1_id = (resp or {}).get("id") or (resp or {}).get("memory_id") or "" if isinstance(resp, dict) else ""
    log(f"  M1 id={m1_id}")
    h.settle(5, reason="pre-archive replication")

    log("alice archives M1 via POST /api/v1/archive (ai-memory-mcp PR #361)")
    # PR alphaonedev/ai-memory-mcp#361 added POST /api/v1/archive for
    # explicit archive-by-id with cross-cluster fanout via sync_push.archives.
    # DELETE /memories/{id} is a HARD delete (no archive) per product design.
    _, arc_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/archive",
        body={"ids": [m1_id], "reason": "scenario-29 archive probe"},
        agent_id="ai:alice", include_status=True,
    )
    archive_code = (arc_doc or {}).get("http_code", 0) if isinstance(arc_doc, dict) else 0
    log(f"  archive (POST) returned HTTP {archive_code}")
    h.settle(5, reason="archive propagation")

    log("bob queries /api/v1/archive on node-2")
    _, arc_list = h.http_on(h.node2_ip, "GET", "/api/v1/archive?limit=50")
    bob_sees_archived = False
    if isinstance(arc_list, dict):
        for m in (arc_list.get("memories") or arc_list.get("archived") or []):
            if isinstance(m, dict) and (m.get("id") == m1_id or marker in (m.get("content") or "")):
                bob_sees_archived = True
                break
    log(f"  bob sees M1 in archive: {bob_sees_archived}")

    log("charlie restores M1 via /api/v1/archive/{id}/restore on node-3")
    _, restore_doc = h.http_on(
        h.node3_ip, "POST", f"/api/v1/archive/{m1_id}/restore",
        agent_id="ai:charlie", include_status=True,
    )
    restore_code = (restore_doc or {}).get("http_code", 0) if isinstance(restore_doc, dict) else 0
    log(f"  restore returned HTTP {restore_code}")
    h.settle(5, reason="restore propagation")

    log("node-4 aggregator: M1 must be active again")
    n4_hit = h.count_matching(h.node4_ip, ns, content_equals=marker, limit=20)
    log(f"  node-4 active rows matching marker: {n4_hit}")

    log("fetch /api/v1/archive/stats on node-4")
    _, stats = h.http_on(h.node4_ip, "GET", "/api/v1/archive/stats")
    stats_shape_ok = isinstance(stats, dict) and len(stats) > 0

    reasons: list[str] = []
    passed = True
    if archive_code not in (200, 201, 202, 204):
        passed = False
        reasons.append(f"archive POST returned HTTP {archive_code}")
    if not bob_sees_archived:
        passed = False
        reasons.append("bob did not see M1 in /api/v1/archive")
    if restore_code not in (200, 204):
        passed = False
        reasons.append(f"restore returned HTTP {restore_code}")
    if n4_hit < 1:
        passed = False
        reasons.append("node-4 did not see M1 restored into active set")
    if not stats_shape_ok:
        passed = False
        reasons.append("/api/v1/archive/stats returned empty / unparseable response")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m1_id=m1_id,
        archive_http_code=archive_code,
        bob_sees_archived=bob_sees_archived,
        restore_http_code=restore_code,
        node4_active_rows=n4_hit,
        stats_shape_ok=stats_shape_ok,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
