#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 32 — memory_inbox + memory_notify A2A messaging.

alice calls memory_notify targeting ai:bob. bob queries memory_inbox
and must see the notification. charlie's inbox must NOT see it
(notification scoping).
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "32"


def _inbox(h: Harness, ip: str, agent_id: str) -> list[dict]:
    _, resp = h.http_on(ip, "GET", f"/api/v1/inbox?agent_id={agent_id}&limit=50",
                        agent_id=agent_id)
    if isinstance(resp, dict):
        return resp.get("messages") or resp.get("notifications") or resp.get("inbox") or []
    if isinstance(resp, list):
        return resp
    return []


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    marker = new_uuid("inb-")

    log("alice calls /api/v1/notify → target=ai:bob")
    _, notify_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/notify",
        body={
            "target_agent_id": "ai:bob",
            "title": "scenario-32 ping",
            "content": f"hello bob, token={marker}",
        },
        agent_id="ai:alice",
        include_status=True,
    )
    notify_code = (notify_doc or {}).get("http_code", 0) if isinstance(notify_doc, dict) else 0
    log(f"  notify returned HTTP {notify_code}")
    h.settle(6, reason="notification fanout")

    log("bob queries his inbox on node-2")
    # Inbox response shape (ai-memory-mcp mcp.rs:2211): each message is
    # {id, from, title, payload, priority, tier, created_at, read, ...}
    # The notify `content` becomes the inbox `payload` — not `content`.
    bob_inbox = _inbox(h, h.node2_ip, "ai:bob")
    bob_sees = any(
        marker in (m.get("payload") or m.get("content") or "")
        for m in bob_inbox if isinstance(m, dict)
    )
    log(f"  bob inbox has {len(bob_inbox)} messages; sees marker: {bob_sees}")

    log("charlie queries his inbox on node-3 (must NOT see it)")
    charlie_inbox = _inbox(h, h.node3_ip, "ai:charlie")
    charlie_sees = any(
        marker in (m.get("payload") or m.get("content") or "")
        for m in charlie_inbox if isinstance(m, dict)
    )
    log(f"  charlie inbox has {len(charlie_inbox)} messages; sees marker: {charlie_sees}")

    reasons: list[str] = []
    passed = True
    if notify_code not in (200, 201, 202):
        passed = False
        reasons.append(f"notify returned HTTP {notify_code}")
    if not bob_sees:
        passed = False
        reasons.append("bob's inbox did not deliver alice's notify")
    if charlie_sees:
        passed = False
        reasons.append("charlie's inbox received a notification intended for bob — scope breach")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        marker=marker,
        notify_http_code=notify_code,
        bob_inbox_count=len(bob_inbox),
        charlie_inbox_count=len(charlie_inbox),
        bob_sees_marker=bob_sees,
        charlie_sees_marker=charlie_sees,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
