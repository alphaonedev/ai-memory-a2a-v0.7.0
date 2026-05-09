#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 63 — Consolidate A2A.

openclaw stores duplicates; hermes calls memory_consolidate; verify
consolidated_from_agents array preserves both ids.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "63"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    OPEN = f"ai:s63-openclaw-{suffix}"
    HERM = f"ai:s63-hermes-{suffix}"
    ns = f"s63-{suffix}"
    body = "Project Apollo's launch escape system was tested at White Sands."

    log("phase A: openclaw + hermes both store the same fact")
    _, d1 = h.write_memory(h.node1_ip, OPEN, ns, title="apollo-1",
                           content=body, include_status=True)
    _, d2 = h.write_memory(h.node2_ip, HERM, ns, title="apollo-2",
                           content=body, include_status=True)
    id1 = (d1 or {}).get("body", {}).get("id") if isinstance(d1, dict) else None
    id2 = (d2 or {}).get("body", {}).get("id") if isinstance(d2, dict) else None
    h.settle(6, "replication")

    log("phase B: hermes calls memory_consolidate on the two stored ids")
    # v0.7 contract: consolidate requires {ids:[...], title, summary, namespace?}.
    # The "consolidate-all-near-dupes-in-a-namespace" verb is not in the v0.7
    # HTTP surface; consolidate explicitly merges the listed ids 1->1.
    if not (id1 and id2):
        h.emit(passed=False, reason="store ids missing for consolidation",
               id1=id1, id2=id2, reasons=["seed failed"])
        return
    rc, resp = h.http_on(h.node2_ip, "POST", "/api/v1/consolidate",
                         body={
                             "ids": [id1, id2],
                             "title": "apollo-consolidated",
                             "summary": "Apollo LES tested at White Sands.",
                             "namespace": ns,
                         },
                         agent_id=HERM, include_status=True)
    body = (resp or {}).get("body") if isinstance(resp, dict) else None
    log(f"  consolidate -> rc={rc} body={body}")

    consolidated_from = []
    cons_id = ""
    if isinstance(body, dict):
        cons_id = body.get("id") or body.get("consolidated_memory_id") or ""
    if cons_id:
        # v0.7: fetch the merged memory and read metadata.consolidated_from_agents.
        _, fetched = h.http_on(h.node2_ip, "GET", f"/api/v1/memories/{cons_id}")
        mem = (fetched or {}).get("memory") if isinstance(fetched, dict) else None
        if isinstance(mem, dict):
            md = mem.get("metadata") or {}
            cfa = md.get("consolidated_from_agents")
            if isinstance(cfa, list):
                consolidated_from.extend(cfa)

    reasons: list[str] = []
    passed = True
    if not isinstance(body, dict):
        passed = False; reasons.append(f"consolidate returned non-dict: {body}")
    if not (id1 and id2):
        passed = False; reasons.append(f"store ids missing: id1={id1} id2={id2}")
    s = set(consolidated_from)
    if not (any(OPEN in a for a in s) and any(HERM in a for a in s)):
        passed = False; reasons.append(f"consolidated_from_agents missing one side: {s}")

    h.emit(passed=passed, reason="; ".join(reasons),
           id1=id1, id2=id2, consolidated_from_agents=list(s),
           reasons=reasons)


if __name__ == "__main__":
    main()
