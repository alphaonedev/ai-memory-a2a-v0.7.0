#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 36 — memory_session_start lifecycle.

alice calls /api/v1/session/start and gets a session_id. alice writes
two memories inside the session; bob reads by session_id on node-2 and
must see exactly those two, tagged with the session metadata.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "36"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario36-session"

    log("alice starts a session on node-1")
    _, start_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/session/start",
        body={"agent_id": "ai:alice", "namespace": ns},
        agent_id="ai:alice", include_status=True,
    )
    start_code = (start_doc or {}).get("http_code", 0) if isinstance(start_doc, dict) else 0
    body = (start_doc or {}).get("body") if isinstance(start_doc, dict) else None
    session_id = ""
    if isinstance(body, dict):
        session_id = body.get("session_id") or body.get("id") or ""
    log(f"  session_start returned HTTP {start_code}, session_id={session_id}")

    if start_code not in (200, 201):
        h.emit(
            passed=False, reason=f"session_start returned HTTP {start_code}",
            start_http_code=start_code, reasons=[f"session_start returned HTTP {start_code}"],
        )

    if not session_id:
        h.emit(
            passed=False, reason="session_start did not return a session_id",
            start_http_code=start_code, reasons=["session_start did not return a session_id"],
        )

    log("alice writes 2 memories tagged with session_id")
    for i in (1, 2):
        h.write_memory(
            h.node1_ip, "ai:alice", ns,
            title=f"session-{i}",
            content=f"in-session row {i} marker={new_uuid()}",
            metadata={"session_id": session_id},
        )
    h.settle(6, reason="session-tagged fanout")

    log(f"bob lists on node-2 filtered by session_id={session_id}")
    _, resp = h.http_on(h.node2_ip, "GET",
                        f"/api/v1/memories?namespace={ns}&session_id={session_id}&limit=20")
    session_rows = 0
    if isinstance(resp, dict):
        for m in resp.get("memories") or []:
            if isinstance(m, dict) and ((m.get("metadata") or {}).get("session_id") == session_id):
                session_rows += 1
    log(f"  bob sees {session_rows} rows tagged session_id={session_id} (expected 2)")

    reasons: list[str] = []
    passed = True
    if session_rows != 2:
        passed = False
        reasons.append(f"bob saw {session_rows} session-tagged rows, expected 2")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        start_http_code=start_code,
        session_id=session_id,
        session_tagged_rows_on_bob=session_rows,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
