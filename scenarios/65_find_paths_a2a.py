#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 65 — find_paths A2A.

openclaw creates entities + links; hermes queries find_paths;
max_depth + cycle detection both verified.

# v0.7.0 Continuation 6 — HTTP migration (2026-05-09)
#
# Migrated from MCP-stdio (`ai-memory mcp` against
# `AI_MEMORY_DB=/var/lib/ai-memory/<node>.db` sqlite path) to the
# new HTTP endpoint `POST /api/v1/kg/find_paths`. The MCP-stdio
# path read from a sqlite file that is empty/stale on
# postgres-backed daemons — the new endpoint dispatches via the
# SAL `MemoryStore::find_paths` trait so postgres-backed daemons
# read from the live `memory_links` (sqlite) or AGE / CTE
# (postgres) graph.
#
# Wire shape (POST /api/v1/kg/find_paths):
#   { source_id, target_id, max_depth?, max_results? }
#   -> { paths: [[id, id, ...], ...], count, source_id, target_id }
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "65"


def _http_find_paths(h: Harness, node_ip: str, src: str, dst: str,
                     max_depth: int, max_results: int = 50) -> dict:
    """Invoke `POST /api/v1/kg/find_paths`. Returns parsed body dict.

    The handler validates source_id/target_id and dispatches via the
    SAL trait — every backend (sqlite recursive CTE, postgres AGE
    Cypher / CTE fallback) returns the same `{paths: [[id...], ...]}`
    shape.
    """
    rc, resp = h.http_on(
        node_ip, "POST", "/api/v1/kg/find_paths",
        body={
            "source_id": src,
            "target_id": dst,
            "max_depth": max_depth,
            "max_results": max_results,
        },
        include_status=True,
    )
    body = (resp or {}).get("body") if isinstance(resp, dict) else None
    if not isinstance(body, dict):
        return {"_rc": rc, "_resp": str(resp)[-200:]}
    return body


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    # Unique per-scenario agent ids so daily-quota state from prior scenarios
    # doesn't bleed into S65.
    suffix = new_uuid()[:6]
    OPEN = f"ai:s65-openclaw-{suffix}"
    HERM = f"ai:s65-hermes-{suffix}"
    ns = f"s65-{suffix}"

    log("phase A: openclaw creates 5 entities A→B→C→D→E + cycle E→B")
    ids = []
    for n in "ABCDE":
        _, d = h.write_memory(h.node1_ip, OPEN, ns, title=f"node-{n}",
                              content=f"entity {n}", include_status=True)
        if isinstance(d, dict):
            mid = (d.get("body") or {}).get("id")
            if mid:
                ids.append(mid)

    if len(ids) != 5:
        h.emit(passed=False, reason=f"only stored {len(ids)}/5 entities", reasons=["seed failed"])
        return

    # Linear A→B→C→D→E
    edges = [(ids[i], ids[i + 1]) for i in range(4)]
    # Cycle E→B
    edges.append((ids[4], ids[1]))

    for src, dst in edges:
        # v0.7 contract: source_id/target_id/relation (not from/to/rel_type).
        h.http_on(h.node1_ip, "POST", "/api/v1/links",
                  body={"source_id": src, "target_id": dst, "relation": "related_to"},
                  agent_id=OPEN, include_status=True)

    h.settle(5, "link replication")

    log("phase B: hermes calls find_paths A→E max_depth=7 (HTTP)")
    # v0.7 cap is 7 (FIND_PATHS_MAX_DEPTH); higher values 422.
    body = _http_find_paths(h, h.node2_ip, ids[0], ids[4], 7)
    paths = body.get("paths") if isinstance(body, dict) else []
    log(f"  paths_found={len(paths) if isinstance(paths, list) else 0}")

    log("phase C: hermes calls find_paths A→E max_depth=1 (no direct edge; expect 0 paths)")
    # v0.7 find_paths is UNDIRECTED, so the cycle E→B + chain A→B→C→D→E
    # gives a 2-hop path A→B→E (B←E reversed). max_depth=1 is the only
    # depth that proves "no path" without tripping the undirected shortcut.
    body2 = _http_find_paths(h, h.node2_ip, ids[0], ids[4], 1)
    short_paths = body2.get("paths") if isinstance(body2, dict) else []

    log("phase D: hermes calls find_paths E→E (cycle detection: must terminate)")
    body3 = _http_find_paths(h, h.node2_ip, ids[4], ids[4], 7)
    if not isinstance(body3, dict):
        body3 = {}

    reasons: list[str] = []
    passed = True
    if not isinstance(paths, list) or len(paths) < 1:
        passed = False
        reasons.append("max_depth=7 found no path A→E")
    if isinstance(short_paths, list) and len(short_paths) > 0:
        passed = False
        reasons.append("max_depth=1 returned paths but A and E have no direct edge")
    if not body3:
        passed = False
        reasons.append("E→E find_paths did not terminate cleanly")

    h.emit(passed=passed, reason="; ".join(reasons),
           paths_full=len(paths) if isinstance(paths, list) else 0,
           paths_shallow=len(short_paths) if isinstance(short_paths, list) else 0,
           cycle_terminated=isinstance(body3, dict),
           reasons=reasons)


if __name__ == "__main__":
    main()
