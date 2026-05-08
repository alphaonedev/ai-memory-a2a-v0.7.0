#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 34 — memory_pending governance flow.

alice sets a namespace standard with governance.write=approve. alice
writes two memories (they go to pending). bob (the approver) lists
pending, approves one, rejects the other. charlie reads the namespace
and must see ONLY the approved row.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "34"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = f"scenario34-pending-{new_uuid()[:6]}"
    approved_marker = new_uuid("approved-")
    rejected_marker = new_uuid("rejected-")

    log(f"alice sets namespace standard on {ns}: write=approve, approver=ai:bob")
    # ai-memory-mcp GovernancePolicy requires all 4 fields (write/promote/
    # delete/approver). No serde defaults yet — until that product-side fix
    # lands, send the full policy with sensible defaults for the fields
    # this scenario doesn't exercise.
    _, std_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/namespaces",
        body={
            "namespace": ns,
            "standard": {
                "governance": {
                    "write": "approve",
                    "promote": "any",
                    "delete": "owner",
                    "approver": {"agent": "ai:bob"},
                },
            },
        },
        agent_id="ai:alice", include_status=True,
    )
    std_code = (std_doc or {}).get("http_code", 0) if isinstance(std_doc, dict) else 0
    log(f"  set-standard returned HTTP {std_code}")
    h.settle(2, reason="standard settle")

    log("alice writes two memories into the governed namespace (should land in pending)")
    _, r1 = h.write_memory(h.node1_ip, "ai:alice", ns,
                           title="to-approve",
                           content=f"token={approved_marker}")
    _, r2 = h.write_memory(h.node1_ip, "ai:alice", ns,
                           title="to-reject",
                           content=f"token={rejected_marker}")
    p1 = (r1 or {}).get("id") or (r1 or {}).get("pending_id") or (r1 or {}).get("memory_id") or "" if isinstance(r1, dict) else ""
    p2 = (r2 or {}).get("id") or (r2 or {}).get("pending_id") or (r2 or {}).get("memory_id") or "" if isinstance(r2, dict) else ""
    log(f"  p1={p1} p2={p2}")
    h.settle(4, reason="pending queue settle")

    log("bob lists pending on node-2")
    _, pend = h.http_on(h.node2_ip, "GET", f"/api/v1/pending?namespace={ns}&limit=50")
    pending_count = 0
    if isinstance(pend, dict):
        pending_count = len(pend.get("pending") or pend.get("memories") or [])
    log(f"  pending queue has {pending_count} entries")

    log("bob approves p1, rejects p2")
    _, a_doc = h.http_on(h.node2_ip, "POST", f"/api/v1/pending/{p1}/approve",
                         body={}, agent_id="ai:bob", include_status=True)
    _, r_doc = h.http_on(h.node2_ip, "POST", f"/api/v1/pending/{p2}/reject",
                         body={"reason": "scenario-34 reject"},
                         agent_id="ai:bob", include_status=True)
    a_code = (a_doc or {}).get("http_code", 0) if isinstance(a_doc, dict) else 0
    r_code = (r_doc or {}).get("http_code", 0) if isinstance(r_doc, dict) else 0
    log(f"  approve HTTP {a_code}; reject HTTP {r_code}")
    h.settle(5, reason="decision fanout")

    log("charlie reads the namespace — expects ONLY approved marker")
    sees_approved = h.count_matching(h.node3_ip, ns, content_contains=approved_marker, limit=20)
    sees_rejected = h.count_matching(h.node3_ip, ns, content_contains=rejected_marker, limit=20)
    log(f"  charlie sees approved={sees_approved} rejected={sees_rejected}")

    reasons: list[str] = []
    passed = True
    if std_code not in (200, 201, 204):
        passed = False
        reasons.append(f"set-standard returned HTTP {std_code}")
    if a_code not in (200, 204):
        passed = False
        reasons.append(f"approve returned HTTP {a_code}")
    if r_code not in (200, 204):
        passed = False
        reasons.append(f"reject returned HTTP {r_code}")
    if sees_approved < 1:
        passed = False
        reasons.append("charlie did not see approved row")
    if sees_rejected > 0:
        passed = False
        reasons.append("charlie saw rejected row — reject didn't prevent publication")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        namespace=ns,
        set_standard_http_code=std_code,
        approve_http_code=a_code,
        reject_http_code=r_code,
        pending_queue_count=pending_count,
        charlie_sees={"approved": sees_approved, "rejected": sees_rejected},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
