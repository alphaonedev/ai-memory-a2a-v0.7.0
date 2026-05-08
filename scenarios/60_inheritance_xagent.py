#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 60 — Permission inheritance cross-agent.

openclaw sets inherit=true on parent ns; hermes deep-child write checks parent
rule; verify allow/deny per matrix.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "60"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    parent = f"s60-parent-{new_uuid()[:6]}"
    child  = f"{parent}/sub/deep"

    log(f"phase A: openclaw sets policy on parent={parent} (inherit=true, write=owner)")
    rc_p, resp_p = h.http_on(
        h.node1_ip, "POST", "/api/v1/namespace/set_standard",
        body={"namespace": parent, "policy": {"write": "owner", "inherit": True}},
        agent_id=OPEN, include_status=True,
    )
    log(f"  set_standard rc={rc_p} resp={resp_p}")

    # Matrix:
    #   - openclaw -> child : ALLOW (owner)
    #   - hermes   -> child : DENY  (inherits owner-only from parent)
    log(f"phase B: openclaw writes to deep child {child} (expect 201)")
    _, owner_doc = h.write_memory(h.node1_ip, OPEN, child, title="owner-deep",
                                  content="owner deep", include_status=True)
    owner_code = (owner_doc or {}).get("http_code") if isinstance(owner_doc, dict) else 0

    log(f"phase C: hermes writes to deep child {child} (expect 403)")
    _, herm_doc = h.write_memory(h.node2_ip, HERM, child, title="herm-deep",
                                 content="herm deep", include_status=True)
    herm_code = (herm_doc or {}).get("http_code") if isinstance(herm_doc, dict) else 0

    # Phase D: flip to inherit=false; hermes should now succeed (no policy on child).
    log(f"phase D: flip parent inherit=false; hermes deep-child write expects 201")
    h.http_on(h.node1_ip, "POST", "/api/v1/namespace/set_standard",
              body={"namespace": parent, "policy": {"write": "owner", "inherit": False}},
              agent_id=OPEN, include_status=True)
    h.settle(3, "policy propagate")
    _, herm2 = h.write_memory(h.node2_ip, HERM, child, title="herm-deep-2",
                              content="herm deep noinherit", include_status=True)
    herm2_code = (herm2 or {}).get("http_code") if isinstance(herm2, dict) else 0

    reasons: list[str] = []
    passed = True
    if owner_code not in (200, 201): passed = False; reasons.append(f"owner deep got {owner_code}")
    if herm_code != 403:             passed = False; reasons.append(f"hermes deep (inherit=true) got {herm_code} (expected 403)")
    if herm2_code not in (200, 201): passed = False; reasons.append(f"hermes deep (inherit=false) got {herm2_code} (expected 201)")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw_owner_deep": owner_code,
               "hermes_inherit_true": herm_code,
               "hermes_inherit_false": herm2_code,
           },
           reasons=reasons)


if __name__ == "__main__":
    main()
