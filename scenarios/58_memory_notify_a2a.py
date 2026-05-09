#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 58 — memory_notify cross-droplet.

openclaw calls memory_notify targeting ai:hermes@…; hermes calls memory_inbox;
expect openclaw's notify body present.
"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "58"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    marker = new_uuid("s58-")
    body_text = f"hello hermes from openclaw — marker={marker}"

    log("phase A: openclaw -> memory_notify ai:hermes")
    # v0.7 contract: target_agent_id (not "to"); payload (or content alias) instead of "body".
    notify = {"target_agent_id": HERM, "title": "ping", "payload": body_text,
              "metadata": {"scenario": SCENARIO_ID, "marker": marker}}
    rc_n, resp_n = h.http_on(h.node1_ip, "POST", "/api/v1/notify",
                             body=notify, agent_id=OPEN, include_status=True)
    log(f"  notify rc={rc_n} resp={resp_n}")

    h.settle(6, "notify fanout")

    log("phase B: hermes -> memory_inbox")
    rc_i, resp_i = h.http_on(h.node2_ip, "GET", "/api/v1/inbox?limit=50",
                             agent_id=HERM)
    inbox = resp_i.get("messages") if isinstance(resp_i, dict) else []
    if not isinstance(inbox, list):
        inbox = []
    found = [m for m in inbox if marker in json.dumps(m)]
    log(f"  inbox entries={len(inbox)} matched={len(found)}")

    reasons: list[str] = []
    passed = True
    if not isinstance(resp_n, dict) or (resp_n.get("http_code") not in (200, 201, 202)):
        passed = False; reasons.append(f"notify rc/code={resp_n}")
    if rc_i != 0:
        passed = False; reasons.append(f"inbox rc={rc_i}")
    if len(found) < 1:
        passed = False; reasons.append(f"hermes did not see notify with marker={marker}")

    h.emit(passed=passed, reason="; ".join(reasons),
           marker=marker, inbox_size=len(inbox), matched=len(found),
           reasons=reasons)


if __name__ == "__main__":
    main()
