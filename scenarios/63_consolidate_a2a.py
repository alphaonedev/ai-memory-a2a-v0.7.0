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
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    ns = f"s63-{new_uuid()[:6]}"
    body = "Project Apollo's launch escape system was tested at White Sands."

    log("phase A: openclaw + hermes both store the same fact")
    _, d1 = h.write_memory(h.node1_ip, OPEN, ns, title="apollo-1",
                           content=body, include_status=True)
    _, d2 = h.write_memory(h.node2_ip, HERM, ns, title="apollo-2",
                           content=body, include_status=True)
    id1 = (d1 or {}).get("body", {}).get("id") if isinstance(d1, dict) else None
    id2 = (d2 or {}).get("body", {}).get("id") if isinstance(d2, dict) else None
    h.settle(6, "replication")

    log("phase B: hermes calls memory_consolidate on namespace")
    rc, resp = h.http_on(h.node2_ip, "POST", "/api/v1/consolidate",
                         body={"namespace": ns, "min_similarity": 0.85},
                         agent_id=HERM, include_status=True)
    body = (resp or {}).get("body") if isinstance(resp, dict) else None
    log(f"  consolidate -> rc={rc} body={body}")

    consolidated_from = []
    if isinstance(body, dict):
        for grp in (body.get("groups") or body.get("clusters") or []):
            if isinstance(grp, dict):
                consolidated_from.extend(grp.get("consolidated_from_agents") or [])

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
