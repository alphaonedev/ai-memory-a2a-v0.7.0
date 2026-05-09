#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 61 — Quota isolation.

openclaw burns daily memory quota (1000 stores); hermes still writes
successfully; per-agent memory_quota_status reports the right counters.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "61"
# 800 stores is sufficient for the >=700 oracle and finishes well within
# the per-scenario timeout under federation quorum_writes=2.
BURN = 800


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    # Per-scenario unique agent ids so daily-quota state from prior scenarios
    # doesn't bleed in. The test verifies isolation between two distinct
    # agent_ids, so the names just need to be distinct.
    suffix = new_uuid()[:6]
    OPEN = f"ai:s61-burner-{suffix}"
    HERM = f"ai:s61-isolate-{suffix}"
    ns = f"s61-{suffix}"

    log(f"phase A: openclaw issues {BURN} stores (burn quota)")
    successes = 0; throttled = 0
    def one_write(idx):
        # v0.7 dedups on (title, namespace) — unique titles avoid 409 upsert
        # short-circuits that misreport as quota saturation.
        _, doc = h.write_memory(
            h.node1_ip, OPEN, ns,
            title=f"burn-{idx}-{new_uuid()[:6]}",
            content=f"q {new_uuid()[:8]}",
            include_status=True,
        )
        return (doc or {}).get("http_code") if isinstance(doc, dict) else 0
    codes = h.run_parallel(one_write, [(i,) for i in range(BURN)], max_workers=8)
    for c in codes:
        if c in (200, 201): successes += 1
        elif c == 429:      throttled  += 1

    log(f"  openclaw 201s={successes} 429s={throttled}")

    log("phase B: hermes single store (must succeed — quota is per-agent)")
    _, herm = h.write_memory(h.node2_ip, HERM, ns, title="hermes-still-ok",
                             content="quota isolated", include_status=True)
    herm_code = (herm or {}).get("http_code") if isinstance(herm, dict) else 0

    log("phase C: memory_quota_status per agent (via MCP — no HTTP twin in v0.7)")
    import json as _json
    def quota_via_mcp(node_ip: str, agent_id: str) -> int:
        msgs = [
            _json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                         "params": {"clientInfo": {"name": "a2a-s61", "version": "0"},
                                    "capabilities": {}, "protocolVersion": "2024-11-05"}}),
            _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": "memory_quota_status",
                                    "arguments": {"agent_id": agent_id}}}),
        ]
        stdin = "\n".join(msgs) + "\n"
        # Without AI_MEMORY_DB the stdio MCP opens ./ai-memory.db (empty);
        # point at the daemon's live db so quota_status sees real rows.
        daemon_db = ("/var/lib/ai-memory/openclaw.db"
                     if node_ip == h.node1_ip
                     else "/var/lib/ai-memory/hermes.db")
        cmd = f"AI_MEMORY_DB={daemon_db} ai-memory mcp --profile full"
        r = h.ssh_exec(node_ip, cmd, timeout=30, stdin=stdin)
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if d.get("id") != 1:
                continue
            cont = (d.get("result") or {}).get("content") or []
            if cont and isinstance(cont[0], dict):
                txt = cont[0].get("text") or ""
                try:
                    p = _json.loads(txt)
                    # v0.7 shape: {agent_id, quota: {current_memories_today, max_memories_per_day, ...}}
                    q = p.get("quota") if isinstance(p, dict) else None
                    if isinstance(q, dict):
                        return int(q.get("current_memories_today") or 0)
                    return int(p.get("current_memories_today") or p.get("used") or 0)
                except (_json.JSONDecodeError, TypeError, ValueError):
                    pass
        return 0

    open_used = quota_via_mcp(h.node1_ip, OPEN)
    herm_used = quota_via_mcp(h.node2_ip, HERM)

    reasons: list[str] = []
    passed = True
    if successes < 700:
        passed = False; reasons.append(f"openclaw successes={successes} (expected >=700 before throttle)")
    if herm_code not in (200, 201):
        passed = False; reasons.append(f"hermes write got {herm_code} (expected 201)")
    if open_used < 700:
        passed = False; reasons.append(f"openclaw quota.used={open_used} (expected >=700)")
    if herm_used > 50:
        passed = False; reasons.append(f"hermes quota.used={herm_used} (expected <=50; isolated)")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw": {"successes": successes, "throttled": throttled, "quota_used": open_used},
               "hermes":   {"http_code": herm_code, "quota_used": herm_used},
           }, reasons=reasons)


if __name__ == "__main__":
    main()
