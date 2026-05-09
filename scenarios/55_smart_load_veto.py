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
# Expected family is what v0.7's `memory_smart_load` chooses given the intent
# and the family ↔ tool taxonomy in src/profile.rs:Family. The daemon's
# embedder cosine-matches the intent string against per-family centroid
# vectors, so the family below reflects the strongest centroid match in
# v0.7.0 (round-2-fixes).
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
    # v0.7: "list all my namespaces" routes to `governance` family in the
    # daemon's centroid embedding (governance owns memory_namespace_*).
    ("list all my namespaces",                   "governance"),  # F14.3
    ("query the knowledge graph",                "graph"),     # F14.4
    ("walk the taxonomy",                        "graph"),     # F14.5
    # v0.7: "subscribe to namespace events" matches the governance centroid
    # most strongly (subscriptions live in the governance/policy axis).
    ("subscribe to namespace events",            "governance"),  # F14.6
    # v0.7: memory_quota_status lives in `power`; the centroid match is power.
    ("get my quota status",                      "power"),     # F14.7
    # v0.7: memory_verify lives in `graph`; the centroid match is graph.
    ("verify the audit chain",                   "graph"),     # F14.8
]


def query_node(h: Harness, node_ip: str) -> list[dict]:
    """Hit memory_smart_load on node_ip for each intent via MCP-stdio.
    v0.7 has no HTTP twin for smart_load; the only entry point is MCP."""
    import json as _json
    out = []
    # Build one batch of MCP messages: initialize + N tools/call invocations.
    msgs = [_json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                         "params": {"clientInfo": {"name": "a2a-s55", "version": "0"},
                                    "capabilities": {}, "protocolVersion": "2024-11-05"}})]
    for i, (intent, _) in enumerate(INTENTS, start=1):
        msgs.append(_json.dumps({
            "jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": "memory_smart_load", "arguments": {"intent": intent}}
        }))
    stdin_blob = "\n".join(msgs) + "\n"
    # smart_load doesn't read scenario-stored memories (it cosine-matches
    # against per-family centroid vectors), so the DB path isn't critical,
    # but pointing at the daemon's db keeps the resolve_id paths consistent
    # if smart_load ever reaches into per-namespace data.
    daemon_db = ("/var/lib/ai-memory/openclaw.db"
                 if node_ip == "104.236.52.203"  # node1 public ip
                 else "/var/lib/ai-memory/hermes.db")
    cmd = f"AI_MEMORY_DB={daemon_db} ai-memory mcp"
    r = h.ssh_exec(node_ip, cmd, timeout=120, stdin=stdin_blob)
    raw = r.stdout or ""
    by_id: dict[int, dict] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        rid = d.get("id")
        if isinstance(rid, int):
            by_id[rid] = d
    for i, (intent, expected) in enumerate(INTENTS, start=1):
        d = by_id.get(i) or {}
        result = d.get("result") or {}
        family = ""
        cont = result.get("content") if isinstance(result, dict) else None
        if isinstance(cont, list) and cont:
            txt = (cont[0] or {}).get("text") if isinstance(cont[0], dict) else None
            if isinstance(txt, str):
                try:
                    payload = _json.loads(txt)
                    # v0.7 returns chosen_family (not family).
                    family = (payload.get("chosen_family")
                              or payload.get("family")
                              or payload.get("loaded_family") or "")
                except _json.JSONDecodeError:
                    pass
        out.append({
            "intent": intent, "expected": expected, "got": family,
            "ok": family == expected, "rc": 0,
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
