#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 82 — memory_kg_query over HTTP returns AGE-Cypher results
identical to the sqlite-CTE baseline (Wave 4).

Stream B claims that with `--store-url postgres://...` + Apache AGE
extension installed, the daemon's `memory_kg_query` MCP/HTTP surface
routes find_paths / timeline queries through AGE Cypher rather than the
sqlite recursive-CTE path, and that results are semantically identical
on a shared seed graph (top-N node-set equality, not SQL plan equality).

PASS criteria:
  - openclaw seeds a 10-entity / 10-edge directed graph via HTTP.
  - HTTP `POST /api/v1/kg/query` (or `memory_kg_query` MCP) on
    openclaw returns N>=1 path between two known endpoints.
  - The returned node-id set for the depth-3 path query matches the
    expected lexical-CTE oracle (computed in-Python from the seed
    edge-list — both backends should converge on the same shortest
    path through the seeded chain).

Self-skips when A2A_BACKEND_KIND=sqlite.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "82"


def _seed_chain(h: Harness, agent: str, ns: str, n: int) -> list[str]:
    """Seed n linear chain memories with `next` links between adjacent ids."""
    ids: list[str] = []
    for i in range(n):
        rc, doc = h.write_memory(
            h.node1_ip, agent, ns,
            title=f"s82-node-{i}",
            content=f"chain node {i}",
            include_status=True,
        )
        if isinstance(doc, dict):
            mid = (doc.get("body") or {}).get("id")
            if mid:
                ids.append(mid)
    if len(ids) < 2:
        return ids
    for i in range(len(ids) - 1):
        h.http_on(
            h.node1_ip, "POST", "/api/v1/links",
            body={"from": ids[i], "to": ids[i + 1], "rel_type": "next"},
            agent_id=agent, include_status=True,
        )
    return ids


def _kg_query_via_http(h: Harness, node_ip: str, agent: str,
                       src: str, dst: str, max_depth: int) -> tuple[int, list[str]]:
    """POST to /api/v1/kg/query (or /api/v1/find_paths) and parse node ids.

    Returns (http_code, list_of_node_ids_on_first_path).
    """
    body = {
        "from": src,
        "to": dst,
        "max_depth": max_depth,
        "rel_types": ["next"],
    }
    for path in ("/api/v1/kg/query", "/api/v1/find_paths", "/api/v1/kg/find_paths"):
        rc, resp = h.http_on(node_ip, "POST", path, body=body,
                             agent_id=agent, include_status=True)
        if isinstance(resp, dict) and resp.get("http_code") == 200:
            body_resp = resp.get("body")
            if not isinstance(body_resp, dict):
                continue
            paths = body_resp.get("paths") or body_resp.get("results") or []
            if isinstance(paths, list) and paths:
                first = paths[0]
                # path may be {"nodes": [...]} or [id1, id2, ...]
                if isinstance(first, dict):
                    nodes = first.get("nodes") or first.get("ids") or []
                else:
                    nodes = first
                if isinstance(nodes, list):
                    out: list[str] = []
                    for n in nodes:
                        if isinstance(n, dict):
                            nid = n.get("id") or n.get("memory_id")
                            if nid:
                                out.append(str(nid))
                        elif isinstance(n, str):
                            out.append(n)
                    return 200, out
            return 200, []
    return 0, []


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    h.skip_if_backend_sqlite(
        "S82 verifies AGE-Cypher routing on postgres-backed daemon; "
        "skipping on sqlite baseline (S45/S46/S65 cover the CTE path)."
    )

    suffix = new_uuid()[:6]
    AGENT = f"ai:s82-{suffix}"
    ns = f"s82-{suffix}"

    log("phase A: seed 10-node chain on openclaw (postgres-backed)")
    ids = _seed_chain(h, AGENT, ns, 10)
    log(f"  seeded {len(ids)} chain nodes")
    if len(ids) < 10:
        h.emit(
            passed=False,
            reason=f"phase A seeded {len(ids)}/10 chain nodes — cannot validate KG routing",
            per_agent={"openclaw": {"seeded": len(ids)}},
        )
        return

    src, dst = ids[0], ids[3]
    expected = ids[0:4]  # depth-3 path through chain[0]→chain[3]

    log(f"phase B: kg_query find_paths from={src[:8]}... to={dst[:8]}... max_depth=4")
    code, actual = _kg_query_via_http(h, h.node1_ip, AGENT, src, dst, max_depth=4)
    log(f"  http_code={code} actual_path_nodes={[n[:8] + '...' for n in actual]}")

    reasons: list[str] = []
    passed = True
    if code != 200:
        passed = False
        reasons.append(f"kg_query returned http_code={code}")
    if not actual:
        passed = False
        reasons.append("kg_query returned no path between chain endpoints")
    else:
        actual_set = set(actual)
        expected_set = set(expected)
        # The CTE oracle is the linear chain; AGE may return same nodes in
        # potentially different order — node-set equality is the parity
        # contract. (S65 covers ordered-path semantics on sqlite.)
        if actual_set != expected_set:
            missing = expected_set - actual_set
            extra = actual_set - expected_set
            passed = False
            reasons.append(
                f"AGE node-set mismatch: missing={list(missing)[:3]} "
                f"extra={list(extra)[:3]}"
            )

    h.emit(
        passed=passed,
        reason="; ".join(reasons),
        per_agent={
            "openclaw": {
                "seeded": len(ids),
                "expected_path_len": len(expected),
                "actual_path_len": len(actual),
                "node_set_match": (
                    set(actual) == set(expected) if actual else False
                ),
            }
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
