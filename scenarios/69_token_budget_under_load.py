#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 69 — Token-budget under A2A load.

500 stores from each node concurrent; doctor --tokens <= 3500 trimmed
throughout; verbose env grows >= 1.4x.
"""
import sys, pathlib, json, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "69"
PER_NODE = 500
SAMPLE_INTERVAL_S = 4


def doctor_tokens(h: Harness, node_ip: str) -> int | None:
    r = h.ssh_exec(node_ip, "ai-memory doctor --tokens --json", timeout=20)
    out = (r.stdout or "").strip()
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        # accept top-level trimmed_full_tokens or nested
        if isinstance(d, dict):
            for k in ("trimmed_full_tokens", "trimmed", "tokens_full_trimmed"):
                v = d.get(k)
                if isinstance(v, int):
                    return v
            full = d.get("full") or d.get("profile_full") or {}
            if isinstance(full, dict):
                v = full.get("trimmed") or full.get("tokens")
                if isinstance(v, int):
                    return v
    return None


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    ns = f"s69-{new_uuid()[:6]}"

    samples: list[dict] = []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            ts = time.time()
            t1 = doctor_tokens(h, h.node1_ip)
            t2 = doctor_tokens(h, h.node2_ip)
            samples.append({"ts": ts, "openclaw": t1, "hermes": t2})
            time.sleep(SAMPLE_INTERVAL_S)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()

    def burst(node_ip: str, agent: str, tag: str):
        for i in range(PER_NODE):
            h.write_memory(node_ip, agent, ns, title=f"{tag}-{i}",
                           content=f"{tag} {i} {new_uuid()[:8]}",
                           include_status=False)

    log(f"phase A: 2x{PER_NODE} concurrent stores (one burst per node)")
    t1 = threading.Thread(target=burst, args=(h.node1_ip, OPEN, "open"))
    t2 = threading.Thread(target=burst, args=(h.node2_ip, HERM, "herm"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    h.settle(8, "let final samples land")
    stop.set(); th.join(timeout=8)

    log(f"  collected {len(samples)} doctor samples")

    log("phase B: tools_verbose env ratio at end of load")
    def measure(node_ip: str) -> dict:
        cmd = """unset AI_MEMORY_TOOLS_VERBOSE
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ai-memory mcp 2>/dev/null
echo "==="
AI_MEMORY_TOOLS_VERBOSE=1 echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
    | AI_MEMORY_TOOLS_VERBOSE=1 ai-memory mcp 2>/dev/null
"""
        r = h.ssh_bash_script(node_ip, cmd, timeout=30)
        out = r.stdout or ""
        a, _, b = out.partition("===")
        return {"default_bytes": len(a.strip()), "verbose_bytes": len(b.strip())}

    o_size = measure(h.node1_ip)
    h_size = measure(h.node2_ip)

    reasons: list[str] = []
    passed = True
    over = [s for s in samples
            if (isinstance(s["openclaw"], int) and s["openclaw"] > 3500)
            or (isinstance(s["hermes"], int) and s["hermes"] > 3500)]
    if over:
        passed = False
        reasons.append(f"doctor --tokens trimmed > 3500 on {len(over)} samples")
    for label, m in (("openclaw", o_size), ("hermes", h_size)):
        if m["default_bytes"] <= 0:
            passed = False; reasons.append(f"{label} default tools/list empty")
        else:
            ratio = m["verbose_bytes"] / max(m["default_bytes"], 1)
            if ratio < 1.4:
                passed = False; reasons.append(f"{label} verbose ratio={ratio:.2f}")

    max_open = max((s["openclaw"] for s in samples if isinstance(s["openclaw"], int)), default=0)
    max_herm = max((s["hermes"] for s in samples if isinstance(s["hermes"], int)), default=0)

    h.emit(passed=passed, reason="; ".join(reasons),
           samples_collected=len(samples),
           max_trimmed={"openclaw": max_open, "hermes": max_herm},
           verbose_ratios={
               "openclaw": (o_size["verbose_bytes"] / max(o_size["default_bytes"], 1)),
               "hermes":   (h_size["verbose_bytes"] / max(h_size["default_bytes"], 1)),
           },
           reasons=reasons)


if __name__ == "__main__":
    main()
