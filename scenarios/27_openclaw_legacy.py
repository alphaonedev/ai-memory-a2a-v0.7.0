#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 27 — Legacy OpenClaw regression.

Only runs under agent_group=openclaw. Exercises the v1 A2A happy path
(alice writes via drive_agent.sh, peers count via HTTP) to confirm the
openclaw driver lane still functions after the switch to ironclaw as
primary Rust agent. Guards against silent regression of the legacy code
path while openclaw droplets are still supported.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "27"
WRITES = 5


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    if h.agent_group != "openclaw":
        h.skip(f"scenario 27 only runs under agent_group=openclaw (actual: {h.agent_group})")

    ns = f"scenario27-openclaw-{new_uuid()[:6]}"

    log(f"alice writes {WRITES} memories via drive_agent (openclaw path)")
    for i in range(1, WRITES + 1):
        r = h.drive_agent(h.node1_ip, "store",
                          f"legacy-{i}",
                          f"openclaw regression marker={new_uuid()}",
                          ns)
        if r.returncode != 0:
            log(f"  !! drive_agent store returned rc={r.returncode}")

    h.settle(15, reason="legacy openclaw fanout")

    # Count on bob (node-2) and charlie (node-3).
    peer_views: dict[str, int] = {}
    for name, ip in (("node-2", h.node2_ip), ("node-3", h.node3_ip)):
        n = h.count_matching(ip, ns, limit=50)
        peer_views[name] = n
        log(f"  {name} sees {n} rows in {ns}")

    reasons: list[str] = []
    passed = True
    for name, n in peer_views.items():
        if n < WRITES:
            passed = False
            reasons.append(f"{name} saw {n}/{WRITES} — legacy openclaw fanout regression")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        writes=WRITES,
        namespace=ns,
        peer_views={n.replace("-", "_"): v for n, v in peer_views.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
