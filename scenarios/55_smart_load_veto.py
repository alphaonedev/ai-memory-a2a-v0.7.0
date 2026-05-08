#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 55 — smart_load keyword veto on both nodes.

S55 smart_load keyword veto on both nodes: 13-intent benchmark (5 R4
regression verbs + 8 F14 controls); ≥ 12/13 correct; identical results
across openclaw and hermes.
"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log

SCENARIO_ID = "55"

# (intent, expected_family) — 5 R4 regression verbs + 8 F14 controls.
INTENTS = [
    # R4 regression verbs (must veto away from default `meta`/`graph`)
    ("send a notification",                      "other"),     # R4.1
    ("expand a query and find related memories", "power"),     # R4.2
    ("auto-tag this content",                    "power"),     # R4.3
    ("detect contradictions in my notes",        "power"),     # R4.4
    ("consolidate duplicate memories",           "power"),     # R4.5
    # F14 controls (router defaults expected)
    ("store a new memory about my project",      "core"),      # F14.1
    ("recall what I learned yesterday",          "core"),      # F14.2
    ("list all my namespaces",                   "meta"),      # F14.3
    ("query the knowledge graph",                "graph"),     # F14.4
    ("walk the taxonomy",                        "graph"),     # F14.5
    ("subscribe to namespace events",            "other"),     # F14.6
    ("get my quota status",                      "meta"),      # F14.7
    ("verify the audit chain",                   "meta"),      # F14.8
]


def query_node(h: Harness, node_ip: str) -> list[dict]:
    """Hit memory_smart_load on node_ip for each intent. Return per-intent records."""
    out = []
    for intent, expected in INTENTS:
        body = {"intent": intent}
        rc, resp = h.http_on(node_ip, "POST", "/api/v1/smart_load", body=body)
        family = ""
        if isinstance(resp, dict):
            family = resp.get("family") or ""
        out.append({
            "intent": intent, "expected": expected, "got": family,
            "ok": family == expected, "rc": rc,
        })
    return out


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    log("query openclaw")
    open_results = query_node(h, h.node1_ip)
    log("query hermes")
    herm_results = query_node(h, h.node2_ip)

    open_correct = sum(1 for r in open_results if r["ok"])
    herm_correct = sum(1 for r in herm_results if r["ok"])
    total = len(INTENTS)

    # Identical?
    identical = all(o["got"] == h2["got"] for o, h2 in zip(open_results, herm_results))

    reasons: list[str] = []
    passed = True
    if open_correct < 12:
        passed = False
        reasons.append(f"openclaw {open_correct}/{total} (need >=12)")
    if herm_correct < 12:
        passed = False
        reasons.append(f"hermes {herm_correct}/{total} (need >=12)")
    if not identical:
        passed = False
        reasons.append("openclaw and hermes disagree on at least one intent")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw": {"correct": open_correct, "total": total, "results": open_results},
               "hermes":   {"correct": herm_correct, "total": total, "results": herm_results},
           },
           identical=identical, reasons=reasons)


if __name__ == "__main__":
    main()
