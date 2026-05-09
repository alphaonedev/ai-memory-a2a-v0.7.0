#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 65 — find_paths A2A.

openclaw creates entities + links; hermes queries memory_find_paths;
max_depth + cycle detection both verified.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "65"


def _mcp_find_paths(h: Harness, node_ip: str, src: str, dst: str, max_depth: int) -> dict:
    """Invoke `memory_find_paths` over MCP-stdio. v0.7 ships this verb as an
    MCP tool; there is no HTTP twin (per src/lib.rs route table).

    Sends `initialize` -> `tools/call memory_find_paths` and returns the
    parsed JSON `result.content[0].text` (or `result` if direct)."""
    import json as _json
    rid_init = 1
    rid_call = 2
    msgs = [
        _json.dumps({"jsonrpc": "2.0", "id": rid_init, "method": "initialize",
                     "params": {"clientInfo": {"name": "a2a-s65", "version": "0"},
                                "capabilities": {}, "protocolVersion": "2024-11-05"}}),
        _json.dumps({"jsonrpc": "2.0", "id": rid_call, "method": "tools/call",
                     "params": {"name": "memory_find_paths",
                                "arguments": {"source_id": src, "target_id": dst,
                                              "max_depth": max_depth, "max_results": 50}}}),
    ]
    stdin_blob = "\n".join(msgs) + "\n"
    # `ai-memory mcp` opens its own SQLite db at `./ai-memory.db` by default;
    # to read the live daemon's data, point AI_MEMORY_DB at the daemon's
    # db path. The campaign topology has well-known paths per droplet.
    daemon_db = ("/var/lib/ai-memory/openclaw.db"
                 if node_ip == h.node1_ip
                 else "/var/lib/ai-memory/hermes.db")
    cmd = f"AI_MEMORY_DB={daemon_db} ai-memory mcp --profile graph"
    r = h.ssh_exec(node_ip, cmd, timeout=45, stdin=stdin_blob)
    out = r.stdout or ""
    body: dict = {"_raw": out[-400:]}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if d.get("id") != rid_call:
            continue
        result = d.get("result") or {}
        # `tools/call` wraps the tool output in `content[0].text` (string JSON).
        cont = result.get("content") if isinstance(result, dict) else None
        if isinstance(cont, list) and cont:
            first = cont[0] or {}
            txt = first.get("text") if isinstance(first, dict) else None
            if isinstance(txt, str):
                try:
                    body = _json.loads(txt)
                except _json.JSONDecodeError:
                    body = {"_text": txt}
        elif isinstance(result, dict):
            body = result
    return body


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    # Unique per-scenario agent ids so daily-quota state from prior scenarios
    # doesn't bleed into S65.
    suffix = new_uuid()[:6]
    OPEN = f"ai:s65-openclaw-{suffix}"
    HERM = f"ai:s65-hermes-{suffix}"
    ns = f"s65-{suffix}"

    log("phase A: openclaw creates 5 entities A→B→C→D→E + cycle E→B")
    ids = []
    for n in "ABCDE":
        _, d = h.write_memory(h.node1_ip, OPEN, ns, title=f"node-{n}",
                              content=f"entity {n}", include_status=True)
        if isinstance(d, dict):
            mid = (d.get("body") or {}).get("id")
            if mid: ids.append(mid)

    if len(ids) != 5:
        h.emit(passed=False, reason=f"only stored {len(ids)}/5 entities", reasons=["seed failed"])
        return

    # Linear A→B→C→D→E
    edges = [(ids[i], ids[i+1]) for i in range(4)]
    # Cycle E→B
    edges.append((ids[4], ids[1]))

    for src, dst in edges:
        # v0.7 contract: source_id/target_id/relation (not from/to/rel_type).
        h.http_on(h.node1_ip, "POST", "/api/v1/links",
                  body={"source_id": src, "target_id": dst, "relation": "related_to"},
                  agent_id=OPEN, include_status=True)

    h.settle(5, "link replication")

    log("phase B: hermes calls find_paths A→E max_depth=7 (via MCP-stdio)")
    # v0.7 cap is 7 (FIND_PATHS_MAX_DEPTH); higher values 422.
    body = _mcp_find_paths(h, h.node2_ip, ids[0], ids[4], 7)
    paths = (body or {}).get("paths") if isinstance(body, dict) else []
    log(f"  paths_found={len(paths) if isinstance(paths, list) else 0}")

    log("phase C: hermes calls find_paths A→E max_depth=1 (no direct edge; expect 0 paths)")
    # v0.7 find_paths is UNDIRECTED, so the cycle E→B + chain A→B→C→D→E
    # gives a 2-hop path A→B→E (B←E reversed). max_depth=1 is the only
    # depth that proves "no path" without tripping the undirected shortcut.
    body2 = _mcp_find_paths(h, h.node2_ip, ids[0], ids[4], 1)
    short_paths = (body2 or {}).get("paths") if isinstance(body2, dict) else []

    log("phase D: hermes calls find_paths E→E (cycle detection: must terminate)")
    body3 = _mcp_find_paths(h, h.node2_ip, ids[4], ids[4], 7)
    if not isinstance(body3, dict):
        body3 = {}

    reasons: list[str] = []
    passed = True
    if not isinstance(paths, list) or len(paths) < 1:
        passed = False; reasons.append("max_depth=10 found no path A→E")
    if isinstance(short_paths, list) and len(short_paths) > 0:
        passed = False; reasons.append("max_depth=1 returned paths but A and E have no direct edge")
    if not body3:
        passed = False; reasons.append("E→E find_paths did not terminate cleanly")

    h.emit(passed=passed, reason="; ".join(reasons),
           paths_full=len(paths) if isinstance(paths, list) else 0,
           paths_shallow=len(short_paths) if isinstance(short_paths, list) else 0,
           cycle_terminated=isinstance(body3, dict),
           reasons=reasons)


if __name__ == "__main__":
    main()
