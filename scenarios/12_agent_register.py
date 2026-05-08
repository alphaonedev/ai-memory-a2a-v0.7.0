#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 12 — Agent registration (Task 1.3).

alice registers a new probe agent on node-1. After settle, node-2,
node-3, and node-4 must all list the new agent. Validates the
RegisterAgentBody contract (agent_id + agent_type required; capabilities
optional; namespace/scope NOT accepted — prior scenarios sent those and
got HTTP 422).
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "12"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    dave_id = f"ai:dave-probe-{new_uuid()[:8]}"

    # agent_type must pass validate::validate_agent_type() — curated list
    # (human / system / ai:<name>) or the `ai:<name>` open namespace. Prior
    # scenarios sent "probe" and got HTTP 400 ("agent_type not in curated
    # list"). Use `ai:probe-v3` so the probe is self-identifying in the
    # registry while still passing validation.
    log(f"alice registers new agent {dave_id} on node-1")
    _, reg_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/agents",
        body={
            "agent_id": dave_id,
            "agent_type": "ai:probe-v3",
            "capabilities": ["memory_store", "memory_recall"],
        },
        agent_id="ai:alice",
        include_status=True,
    )
    reg_code = (reg_doc or {}).get("http_code", 0) if isinstance(reg_doc, dict) else 0
    log(f"  POST /api/v1/agents returned HTTP {reg_code}")
    h.settle(10, reason="agent-list fanout")

    sees: dict[str, int] = {}
    for name, ip in (("node-2", h.node2_ip), ("node-3", h.node3_ip), ("node-4", h.node4_ip)):
        _, resp = h.http_on(ip, "GET", "/api/v1/agents?limit=100")
        hit = 0
        if isinstance(resp, dict):
            pool = resp.get("agents") or []
        elif isinstance(resp, list):
            pool = resp
        else:
            pool = []
        for a in pool:
            if isinstance(a, dict):
                aid = a.get("agent_id") or a.get("id") or ""
                if aid == dave_id:
                    hit += 1
        sees[name] = hit
        log(f"  {name} sees {dave_id}: {hit} (expected >=1)")

    reasons: list[str] = []
    passed = True
    if reg_code not in (200, 201):
        passed = False
        reasons.append(f"register POST returned HTTP {reg_code}")
    for name in ("node-2", "node-3", "node-4"):
        if sees.get(name, 0) < 1:
            passed = False
            reasons.append(f"{name} did not see registered agent {dave_id}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        registered_agent=dave_id,
        register_http_code=reg_code,
        peers_see={f"node_{n.split('-')[1]}": v for n, v in sees.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
