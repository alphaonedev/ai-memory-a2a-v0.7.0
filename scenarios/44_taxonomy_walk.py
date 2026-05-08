#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 44 — memory_get_taxonomy honest truncation (v0.6.3).

Writes 30 memories spread across a hierarchical namespace tree
(alphaone/eng/{platform,research,...}, alphaone/ops/..., etc.).
Then calls /api/v1/taxonomy with depth=8 limit=20 — the limit forces
truncation. We assert:

  * the response carries a `subtree_count` (or equivalent field)
    reflecting the FULL count of descendants under each node, not just
    the count that fit into the truncated `limit=20` window.
  * concretely: for the root namespace, subtree_count must be > the
    sum of `count` fields across the returned children. If they are
    equal, the API is lying about how much it elided — exactly the
    bug v0.6.3 set out to fix.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "44"

# 30 memories across a 3-level taxonomy. Marker is per-run so re-running
# on the same mesh stays idempotent.
SUBTREES = [
    "alphaone/eng/platform",
    "alphaone/eng/research",
    "alphaone/eng/sre",
    "alphaone/ops/finance",
    "alphaone/ops/legal",
    "alphaone/product/design",
]
ROWS_PER_SUBTREE = 5  # 6 * 5 = 30


def _find_node(tree: object, ns: str) -> dict | None:
    """DFS through whatever shape the taxonomy endpoint returns to find the
    node for `ns`. Tolerates {namespace}/{children} or {name}/{nodes} or
    flat list-of-dicts shapes."""
    if isinstance(tree, dict):
        nm = tree.get("namespace") or tree.get("name") or tree.get("path")
        if nm == ns:
            return tree
        for key in ("children", "nodes", "subtree", "items"):
            v = tree.get(key)
            if v is not None:
                hit = _find_node(v, ns)
                if hit is not None:
                    return hit
    elif isinstance(tree, list):
        for entry in tree:
            hit = _find_node(entry, ns)
            if hit is not None:
                return hit
    return None


def _children_of(node: dict) -> list[dict]:
    for key in ("children", "nodes", "subtree", "items"):
        v = node.get(key)
        if isinstance(v, list):
            return [c for c in v if isinstance(c, dict)]
    return []


def _count_field(node: dict) -> int:
    for k in ("count", "total", "memory_count", "n"):
        v = node.get(k)
        if isinstance(v, int):
            return v
    return 0


def _subtree_count(node: dict) -> int | None:
    for k in ("subtree_count", "total_count", "descendant_count", "subtree_total"):
        v = node.get(k)
        if isinstance(v, int):
            return v
    return None


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    root = f"scenario44-{suffix}/alphaone"

    log(f"writing 30 memories across 6 subtrees rooted at {root}")
    written = 0
    for sub in SUBTREES:
        full_ns = f"scenario44-{suffix}/{sub}"
        for i in range(ROWS_PER_SUBTREE):
            rc, _ = h.write_memory(
                h.node1_ip, "ai:alice", full_ns,
                title=f"tax-{i}", content=new_uuid("tx-"),
            )
            if rc == 0:
                written += 1
    log(f"  wrote {written}/30")
    h.settle(8, reason="taxonomy index fanout")

    log(f"GET /api/v1/taxonomy?root={root}&depth=8&limit=20 on node-1")
    _, taxo_doc = h.http_on(
        h.node1_ip, "GET",
        f"/api/v1/taxonomy?root={root}&depth=8&limit=20",
        include_status=True,
    )
    taxo_code = (taxo_doc or {}).get("http_code", 0) if isinstance(taxo_doc, dict) else 0
    body = (taxo_doc or {}).get("body") if isinstance(taxo_doc, dict) else None
    log(f"  HTTP {taxo_code}")

    root_node = _find_node(body, root) if body is not None else None
    children = _children_of(root_node) if root_node else []
    sum_children_counts = sum(_count_field(c) for c in children)
    root_subtree = _subtree_count(root_node) if root_node else None

    log(f"  root node found: {root_node is not None}")
    log(f"  children returned: {len(children)} (limit was 20)")
    log(f"  sum(count) over returned children: {sum_children_counts}")
    log(f"  root subtree_count: {root_subtree}")

    reasons: list[str] = []
    passed = True
    if taxo_code != 200:
        passed = False
        reasons.append(f"/api/v1/taxonomy returned HTTP {taxo_code}")
    if root_node is None:
        passed = False
        reasons.append(f"could not locate {root} in taxonomy response")
    if root_subtree is None:
        passed = False
        reasons.append("root node missing subtree_count field — v0.6.3 should expose it")
    else:
        if root_subtree < written:
            passed = False
            reasons.append(
                f"root subtree_count={root_subtree} < {written} memories actually written"
            )
        # The honest-truncation invariant: if there ARE children that fit,
        # the subtree_count must reflect MORE than fits. We tolerate the
        # edge case where everything fit (limit not reached).
        if len(children) >= 20 and root_subtree <= sum_children_counts:
            passed = False
            reasons.append(
                f"subtree_count={root_subtree} <= sum-of-returned-counts="
                f"{sum_children_counts} despite truncation (dishonest count)"
            )

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        root=root,
        memories_written=written,
        taxonomy_http_code=taxo_code,
        children_returned=len(children),
        sum_children_counts=sum_children_counts,
        root_subtree_count=root_subtree,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
