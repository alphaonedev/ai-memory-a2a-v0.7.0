#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 45 — memory_kg_query temporal slice (v0.6.3).

The knowledge-graph layer added in v0.6.3 carries valid_from/valid_until
on every edge. memory_kg_query takes an `as_of` timestamp and must
return the edge set that was logically valid at that instant.

Setup:
  * Write a source memory M0 plus 3 targets T1, T2, T3.
  * Create 3 edges M0->T{1,2,3} with valid_from in the past (t_past).
  * Invalidate the M0->T2 edge via /api/v1/kg/invalidate, which sets
    valid_until = now.

Assertions:
  * /api/v1/kg/query with as_of=t_past returns ALL 3 edges.
  * /api/v1/kg/query with as_of=now returns only 2 edges (T2 dropped).

This nails down the temporal-slice contract: invalidation can't be a
simple delete (would lose history); it has to be an end-stamp visible
to past-as-of queries.
"""

import sys
import pathlib
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "45"


def _id_of(resp: object) -> str:
    if isinstance(resp, dict):
        return resp.get("id") or resp.get("memory_id") or ""
    return ""


def _query_edges(h: Harness, ip: str, source_id: str, as_of_iso: str) -> tuple[int, list[dict]]:
    """Returns (http_code, edges_list)."""
    body = {"source_id": source_id, "as_of": as_of_iso}
    _, doc = h.http_on(ip, "POST", "/api/v1/kg/query",
                       body=body, include_status=True, timeout=30)
    code = (doc or {}).get("http_code", 0) if isinstance(doc, dict) else 0
    payload = (doc or {}).get("body") if isinstance(doc, dict) else None
    edges: list[dict] = []
    if isinstance(payload, dict):
        edges = payload.get("edges") or payload.get("links") or payload.get("results") or []
    elif isinstance(payload, list):
        edges = payload
    return code, [e for e in edges if isinstance(e, dict)]


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = f"scenario45-kg-{new_uuid()[:6]}"

    log("seed: write M0 + T1/T2/T3 on node-1")
    _, r0 = h.write_memory(h.node1_ip, "ai:alice", ns, title="kg-source", content="kg-M0")
    m0 = _id_of(r0)
    targets = []
    for i in (1, 2, 3):
        _, r = h.write_memory(h.node1_ip, "ai:alice", ns,
                              title=f"kg-target-{i}", content=f"kg-T{i}")
        targets.append(_id_of(r))
    log(f"  M0={m0} targets={targets}")
    if not m0 or not all(targets):
        h.emit(passed=False, reason="failed to write seed memories",
               m0_id=m0, target_ids=targets, reasons=["seed write failed"])

    h.settle(4, reason="seed propagation")

    # Build valid_from in the past so as_of=now still places these edges
    # within their validity window.
    t_past = int(time.time()) - 3600  # 1h ago
    t_past_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_past))
    log(f"creating 3 edges with valid_from={t_past_iso}")

    for i, tid in enumerate(targets, start=1):
        body = {
            "source_id": m0,
            "target_id": tid,
            "relation": "kg_related_to",
            "valid_from": t_past_iso,
        }
        _, link_doc = h.http_on(h.node1_ip, "POST", "/api/v1/links",
                                body=body, agent_id="ai:alice", include_status=True)
        code = (link_doc or {}).get("http_code", 0) if isinstance(link_doc, dict) else 0
        log(f"  edge M0->T{i} HTTP {code}")

    h.settle(4, reason="edge fanout")

    # Invalidate the M0->T2 edge. /api/v1/kg/invalidate is the v0.6.3
    # surface; sets valid_until = now without deleting the row.
    log(f"invalidating edge M0->T2 (target {targets[1]})")
    _, inv_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/kg/invalidate",
        body={"source_id": m0, "target_id": targets[1], "relation": "kg_related_to"},
        agent_id="ai:alice", include_status=True,
    )
    inv_code = (inv_doc or {}).get("http_code", 0) if isinstance(inv_doc, dict) else 0
    log(f"  invalidate HTTP {inv_code}")
    h.settle(4, reason="invalidation fanout")

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log(f"kg_query as_of={t_past_iso} (past) — expecting 3 edges")
    past_code, past_edges = _query_edges(h, h.node1_ip, m0, t_past_iso)
    log(f"  HTTP {past_code} edge count={len(past_edges)}")

    log(f"kg_query as_of={now_iso} (now) — expecting 2 edges (T2 invalidated)")
    now_code, now_edges = _query_edges(h, h.node1_ip, m0, now_iso)
    log(f"  HTTP {now_code} edge count={len(now_edges)}")

    def _targets_seen(edges: list[dict]) -> set[str]:
        out: set[str] = set()
        for e in edges:
            t = e.get("target_id") or e.get("target") or e.get("to") or ""
            if t:
                out.add(t)
        return out

    past_set = _targets_seen(past_edges)
    now_set = _targets_seen(now_edges)

    reasons: list[str] = []
    passed = True
    if past_code != 200:
        passed = False
        reasons.append(f"kg_query(past) returned HTTP {past_code}")
    if now_code != 200:
        passed = False
        reasons.append(f"kg_query(now) returned HTTP {now_code}")
    if inv_code not in (200, 201, 204):
        passed = False
        reasons.append(f"kg_invalidate returned HTTP {inv_code}")
    if len(past_edges) < 3 or not all(t in past_set for t in targets):
        passed = False
        reasons.append(
            f"as_of=past missing edges; expected all 3 targets, got {len(past_edges)}"
        )
    if len(now_edges) != 2:
        passed = False
        reasons.append(f"as_of=now expected 2 edges, got {len(now_edges)}")
    if targets[1] in now_set:
        passed = False
        reasons.append("invalidated target T2 still visible at as_of=now")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m0_id=m0,
        target_ids=targets,
        invalidate_http_code=inv_code,
        as_of_past=t_past_iso,
        as_of_now=now_iso,
        past_query_http_code=past_code,
        now_query_http_code=now_code,
        past_edges=len(past_edges),
        now_edges=len(now_edges),
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
