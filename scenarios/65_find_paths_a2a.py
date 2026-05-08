#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 65 — find_paths A2A.

openclaw creates entities + links; hermes queries memory_find_paths;
max_depth + cycle detection both verified.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "65"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    ns = f"s65-{new_uuid()[:6]}"

    log("phase A: openclaw creates 5 entities A→B→C→D→E + cycle E→B")
    ids = []
    for n in "ABCDE":
        _, d = h.write_memory(h.node1_ip, OPEN, ns, title=f"node-{n}",
                              content=f"entity {n}", include_status=True)
        if isinstance(d, dict):
            mid = (d.get("body") or {}).get("id")
            if mid: ids.append(mid)

    if len(ids) != 5:
        h.emit(passed=False, reason=f"only stored {len(ids)}/5 entities", reasons=["seed failed"])
        return

    # Linear A→B→C→D→E
    edges = [(ids[i], ids[i+1]) for i in range(4)]
    # Cycle E→B
    edges.append((ids[4], ids[1]))

    for src, dst in edges:
        h.http_on(h.node1_ip, "POST", "/api/v1/links",
                  body={"from": src, "to": dst, "rel_type": "next"},
                  agent_id=OPEN, include_status=True)

    h.settle(5, "link replication")

    log("phase B: hermes calls find_paths A→E max_depth=10")
    rc, resp = h.http_on(
        h.node2_ip, "POST", "/api/v1/find_paths",
        body={"from": ids[0], "to": ids[4], "max_depth": 10},
        agent_id=HERM, include_status=True,
    )
    body = (resp or {}).get("body") if isinstance(resp, dict) else None
    paths = (body or {}).get("paths") if isinstance(body, dict) else []
    log(f"  paths_found={len(paths) if isinstance(paths, list) else 0}")

    log("phase C: hermes calls find_paths A→E max_depth=2 (must NOT find anything; chain is len 4)")
    rc2, resp2 = h.http_on(
        h.node2_ip, "POST", "/api/v1/find_paths",
        body={"from": ids[0], "to": ids[4], "max_depth": 2},
        agent_id=HERM, include_status=True,
    )
    body2 = (resp2 or {}).get("body") if isinstance(resp2, dict) else None
    short_paths = (body2 or {}).get("paths") if isinstance(body2, dict) else []

    log("phase D: hermes calls find_paths E→E (cycle detection: must terminate)")
    rc3, resp3 = h.http_on(
        h.node2_ip, "POST", "/api/v1/find_paths",
        body={"from": ids[4], "to": ids[4], "max_depth": 7},
        agent_id=HERM, include_status=True,
    )
    body3 = (resp3 or {}).get("body") if isinstance(resp3, dict) else None

    reasons: list[str] = []
    passed = True
    if not isinstance(paths, list) or len(paths) < 1:
        passed = False; reasons.append("max_depth=10 found no path A→E")
    if isinstance(short_paths, list) and len(short_paths) > 0:
        passed = False; reasons.append("max_depth=2 returned paths but the shortest A→E is length 4")
    if not isinstance(body3, dict):
        passed = False; reasons.append("E→E find_paths did not terminate cleanly")

    h.emit(passed=passed, reason="; ".join(reasons),
           paths_full=len(paths) if isinstance(paths, list) else 0,
           paths_shallow=len(short_paths) if isinstance(short_paths, list) else 0,
           cycle_terminated=isinstance(body3, dict),
           reasons=reasons)


if __name__ == "__main__":
    main()
