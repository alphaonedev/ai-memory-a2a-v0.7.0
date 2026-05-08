#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 35 — memory_namespace_*_standard rule layering.

alice sets a standard on parent namespace `p` (content: "parent-rule").
alice sets a standard on child namespace `p/c` with parent=`p` (content:
"child-rule"). bob gets the standard for `p/c` and must receive the
merged/layered rules (parent + child). alice clears the standard;
bob's subsequent get must return empty/default.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "35"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    parent = f"scenario35-parent-{suffix}"
    child = f"{parent}/child"

    # First write a parent-standard memory so the standard can reference it.
    log("alice writes parent-standard-memory on node-1")
    _, p_resp = h.write_memory(h.node1_ip, "ai:alice", parent,
                               title="parent-standard", content="parent rule: x > 0",
                               tier="long")
    parent_mid = (p_resp or {}).get("id") or (p_resp or {}).get("memory_id") or "" if isinstance(p_resp, dict) else ""

    log(f"alice sets namespace standard on {parent}")
    _, set_p_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/namespaces",
        body={"namespace": parent, "id": parent_mid},
        agent_id="ai:alice", include_status=True,
    )
    set_p_code = (set_p_doc or {}).get("http_code", 0) if isinstance(set_p_doc, dict) else 0
    log(f"  set-parent returned HTTP {set_p_code}")

    log("alice writes child-standard-memory on node-1")
    _, c_resp = h.write_memory(h.node1_ip, "ai:alice", child,
                               title="child-standard",
                               content="child rule: y < 10",
                               tier="long")
    child_mid = (c_resp or {}).get("id") or (c_resp or {}).get("memory_id") or "" if isinstance(c_resp, dict) else ""

    log(f"alice sets namespace standard on {child} with parent={parent}")
    _, set_c_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/namespaces",
        body={"namespace": child, "id": child_mid, "parent": parent},
        agent_id="ai:alice", include_status=True,
    )
    set_c_code = (set_c_doc or {}).get("http_code", 0) if isinstance(set_c_doc, dict) else 0
    log(f"  set-child returned HTTP {set_c_code}")
    h.settle(4, reason="standard fanout")

    log(f"bob gets standard for {child} on node-2 (expects layered parent+child)")
    # `inherit=true` is required to get the layered parent+child rule
    # chain. Without it the handler returns only the child's own
    # standard (ai-memory-mcp mcp.rs:1912).
    _, std_doc = h.http_on(h.node2_ip, "GET", f"/api/v1/namespaces?namespace={child}&inherit=true",
                           include_status=True)
    std_code = (std_doc or {}).get("http_code", 0) if isinstance(std_doc, dict) else 0
    body = (std_doc or {}).get("body") if isinstance(std_doc, dict) else None
    log(f"  get-standard returned HTTP {std_code}")

    sees_parent_rule = False
    sees_child_rule = False
    if isinstance(body, dict):
        blob = str(body)
        sees_parent_rule = "parent rule" in blob or parent_mid in blob
        sees_child_rule = "child rule" in blob or child_mid in blob

    log(f"  parent-rule visible={sees_parent_rule}; child-rule visible={sees_child_rule}")

    log(f"alice clears standard on {child}")
    _, clr_doc = h.http_on(
        h.node1_ip, "DELETE", f"/api/v1/namespaces?namespace={child}",
        agent_id="ai:alice", include_status=True,
    )
    clr_code = (clr_doc or {}).get("http_code", 0) if isinstance(clr_doc, dict) else 0
    log(f"  clear returned HTTP {clr_code}")
    h.settle(3, reason="clear settle")

    _, post_clear = h.http_on(h.node2_ip, "GET", f"/api/v1/namespaces?namespace={child}",
                              include_status=True)
    post_body = (post_clear or {}).get("body") if isinstance(post_clear, dict) else None
    post_has_child_rule = False
    if isinstance(post_body, dict):
        post_has_child_rule = "child rule" in str(post_body) or (child_mid and child_mid in str(post_body))

    reasons: list[str] = []
    passed = True
    for code, label in ((set_p_code, "set-parent"), (set_c_code, "set-child"),
                        (std_code, "get-standard"), (clr_code, "clear-standard")):
        if code not in (200, 201, 204):
            passed = False
            reasons.append(f"{label} returned HTTP {code}")
    if not sees_parent_rule:
        passed = False
        reasons.append("parent rule not layered into child's standard view")
    if not sees_child_rule:
        passed = False
        reasons.append("child rule missing from standard view")
    if post_has_child_rule:
        passed = False
        reasons.append("child rule still visible after clear")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        parent_ns=parent,
        child_ns=child,
        set_parent_http_code=set_p_code,
        set_child_http_code=set_c_code,
        get_standard_http_code=std_code,
        clear_http_code=clr_code,
        sees_parent_rule=sees_parent_rule,
        sees_child_rule=sees_child_rule,
        post_clear_has_child_rule=post_has_child_rule,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
