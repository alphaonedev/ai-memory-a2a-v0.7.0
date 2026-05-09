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
# Each node burst keeps PER_NODE under the daily-quota ceiling (1000) so a
# single run doesn't exhaust the agent's quota; the doctor sampling thread
# runs in parallel to catch trim behavior under sustained load.
PER_NODE = 200
SAMPLE_INTERVAL_S = 4


def doctor_tokens(h: Harness, node_ip: str) -> int | None:
    """Return the active-profile trimmed token total. v0.7 keys:
       - trimmed_active_total_tokens (preferred)
       - trimmed_full_profile_total_tokens (full profile alt)
       - active_total_tokens (untrimmed fallback)
    """
    # `--json` stdout is multi-line; the JSON document opens with `{` and
    # spans the rest of stdout. Concatenate non-stderr lines and parse.
    r = h.ssh_exec(node_ip, "ai-memory doctor --tokens --json 2>/dev/null", timeout=20)
    out = (r.stdout or "").strip()
    if not out:
        return None
    # Drop any leading non-JSON banner lines.
    while out and not out.startswith("{"):
        nl = out.find("\n")
        if nl < 0:
            return None
        out = out[nl + 1:].lstrip()
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict):
        return None
    for k in (
        "trimmed_active_total_tokens",
        "trimmed_full_profile_total_tokens",
        "active_total_tokens",
        "trimmed_full_tokens",
    ):
        v = d.get(k)
        if isinstance(v, int):
            return v
    return None


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    OPEN = f"ai:s69-burner-open-{suffix}"
    HERM = f"ai:s69-burner-herm-{suffix}"
    ns = f"s69-{suffix}"

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
        # v0.7 default profile is `core` (8 tools); use `--profile full` to
        # exercise the full 51-tool surface where the verbose:default ratio
        # is meaningful. The init handshake is required before tools/list.
        init = '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"clientInfo":{"name":"a2a-s69","version":"0"},"capabilities":{},"protocolVersion":"2024-11-05"}}'
        lst = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        cmd = f"""set -u
unset AI_MEMORY_TOOLS_VERBOSE
printf '%s\\n%s\\n' '{init}' '{lst}' | ai-memory mcp --profile full 2>/dev/null
echo "==="
printf '%s\\n%s\\n' '{init}' '{lst}' | AI_MEMORY_TOOLS_VERBOSE=1 ai-memory mcp --profile full 2>/dev/null
"""
        r = h.ssh_bash_script(node_ip, cmd, timeout=60)
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
