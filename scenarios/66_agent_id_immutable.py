#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 66 — agent_id immutability.

openclaw stores M with agent_id A; hermes calls memory_update; verify
M.metadata.agent_id stays A.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "66"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    ns = f"s66-{new_uuid()[:6]}"

    log("phase A: openclaw stores M with agent_id A")
    _, d = h.write_memory(h.node1_ip, OPEN, ns, title="immutable",
                          content="agent_id immutability test", include_status=True)
    mid = (d or {}).get("body", {}).get("id") if isinstance(d, dict) else None
    if not mid:
        h.emit(passed=False, reason="store failed", reasons=["seed failed"])
        return

    h.settle(4, "replication")

    log(f"phase B: hermes attempts memory_update on {mid} with metadata.agent_id=HERM")
    upd = {"metadata": {"agent_id": HERM, "tampered": True},
           "content": "post-tamper content"}
    _, r = h.update_memory(h.node2_ip, mid, HERM, updates=upd, include_status=True)
    upd_code = (r or {}).get("http_code") if isinstance(r, dict) else 0

    log("phase C: re-fetch and verify metadata.agent_id is still A")
    _, fetched = h.get_memory(h.node1_ip, mid)
    final_agent = ""
    if isinstance(fetched, dict):
        final_agent = (fetched.get("metadata") or {}).get("agent_id") or ""

    reasons: list[str] = []
    passed = True
    if final_agent != OPEN:
        passed = False
        reasons.append(f"metadata.agent_id={final_agent!r} (expected {OPEN!r})")
    # An update that tries to overwrite agent_id should either: (a) reject
    # the update entirely (4xx) or (b) accept but silently drop the agent_id.
    # Either is fine — only the final state matters.

    h.emit(passed=passed, reason="; ".join(reasons),
           memory_id=mid, update_http_code=upd_code,
           final_agent_id=final_agent, reasons=reasons)


if __name__ == "__main__":
    main()
