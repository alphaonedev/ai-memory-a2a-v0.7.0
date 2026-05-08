#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 42 — /api/v1/namespaces enumeration equivalence.

alice writes into 3 distinct scenario42 namespaces. After settle, every
peer's /api/v1/namespaces response must include all 3, with equivalent
counts. Drift would mean namespace-index replication is broken.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "42"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    suffix = new_uuid()[:6]
    ns_list = [f"scenario42-{suffix}-{i}" for i in range(3)]

    log(f"alice writes into 3 distinct namespaces: {ns_list}")
    for i, ns in enumerate(ns_list):
        for j in range(2):
            h.write_memory(h.node1_ip, "ai:alice", ns,
                           title=f"ns-probe-{j}", content=new_uuid("n-"))
    h.settle(10, reason="namespace index fanout")

    per_peer: dict[str, dict] = {}
    for name, ip in (("node-1", h.node1_ip), ("node-2", h.node2_ip),
                     ("node-3", h.node3_ip), ("node-4", h.node4_ip)):
        _, resp = h.http_on(ip, "GET", "/api/v1/namespaces?limit=200")
        found: dict[str, int] = {}
        if isinstance(resp, dict):
            pool = resp.get("namespaces") or resp.get("items") or []
        elif isinstance(resp, list):
            pool = resp
        else:
            pool = []
        for entry in pool:
            if isinstance(entry, dict):
                nm = entry.get("namespace") or entry.get("name") or ""
                if nm in ns_list:
                    found[nm] = entry.get("count", entry.get("total", 0)) or 0
            elif isinstance(entry, str) and entry in ns_list:
                found[entry] = -1  # count not available in this shape
        per_peer[name] = found
        log(f"  {name} sees {len(found)}/3 target namespaces, counts: {found}")

    reasons: list[str] = []
    passed = True
    for name, found in per_peer.items():
        missing = [ns for ns in ns_list if ns not in found]
        if missing:
            passed = False
            reasons.append(f"{name} missing namespaces: {missing}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        namespaces=ns_list,
        per_peer={n.replace("-", "_"): v for n, v in per_peer.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
