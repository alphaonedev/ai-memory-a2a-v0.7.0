#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 56 — AI_MEMORY_TOOLS_VERBOSE on both nodes.

tools/list with env vs without; verbose >= 1.4x default size; tool count
identical (51).
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log

SCENARIO_ID = "56"


def measure(h: Harness, node_ip: str) -> dict:
    """Run two `ai-memory mcp tools/list` invocations, with and without env."""
    # v0.7 ships profile=core by default (8 tools). To assert the full
    # 51-tool surface, invoke `mcp --profile full` explicitly.
    init_msg = '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"clientInfo":{"name":"a2a-s56","version":"0"},"capabilities":{},"protocolVersion":"2024-11-05"}}'
    list_msg = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
    script = f"""set -u
echo "==DEFAULT=="
unset AI_MEMORY_TOOLS_VERBOSE
printf '%s\\n%s\\n' '{init_msg}' '{list_msg}' | ai-memory mcp --profile full 2>/dev/null
echo "==VERBOSE=="
printf '%s\\n%s\\n' '{init_msg}' '{list_msg}' | AI_MEMORY_TOOLS_VERBOSE=1 ai-memory mcp --profile full 2>/dev/null
"""
    r = h.ssh_bash_script(node_ip, script, timeout=60)
    out = r.stdout or ""
    parts = out.split("==VERBOSE==", 1)
    default_blob = parts[0].split("==DEFAULT==", 1)[-1].strip() if "==DEFAULT==" in parts[0] else ""
    verbose_blob = parts[1].strip() if len(parts) > 1 else ""

    import json
    def count_tools(blob: str) -> int:
        for line in blob.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            tools = (d.get("result") or {}).get("tools") if isinstance(d, dict) else None
            if isinstance(tools, list):
                return len(tools)
        return -1

    return {
        "default_bytes": len(default_blob),
        "verbose_bytes": len(verbose_blob),
        "default_tools": count_tools(default_blob),
        "verbose_tools": count_tools(verbose_blob),
    }


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    log("measure openclaw")
    o = measure(h, h.node1_ip)
    log(f"  {o}")
    log("measure hermes")
    he = measure(h, h.node2_ip)
    log(f"  {he}")

    reasons: list[str] = []
    passed = True
    for label, m in [("openclaw", o), ("hermes", he)]:
        if m["default_tools"] != 51 or m["verbose_tools"] != 51:
            passed = False
            reasons.append(f"{label}: tool count default={m['default_tools']} verbose={m['verbose_tools']} (need 51/51)")
        if m["default_bytes"] <= 0:
            passed = False
            reasons.append(f"{label}: default tools/list returned 0 bytes")
        else:
            ratio = m["verbose_bytes"] / max(m["default_bytes"], 1)
            if ratio < 1.4:
                passed = False
                reasons.append(f"{label}: verbose/default size ratio = {ratio:.2f} (need >=1.4)")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={"openclaw": o, "hermes": he}, reasons=reasons)


if __name__ == "__main__":
    main()
