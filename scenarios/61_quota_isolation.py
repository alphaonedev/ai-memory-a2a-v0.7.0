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
BURN = 1000


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    ns = f"s61-{new_uuid()[:6]}"

    log(f"phase A: openclaw issues {BURN} stores (burn quota)")
    successes = 0; throttled = 0
    def one_write(_):
        _, doc = h.write_memory(h.node1_ip, OPEN, ns, title="burn",
                                content=f"q {new_uuid()[:8]}", include_status=True)
        return (doc or {}).get("http_code") if isinstance(doc, dict) else 0
    codes = h.run_parallel(one_write, [(i,) for i in range(BURN)], max_workers=16)
    for c in codes:
        if c in (200, 201): successes += 1
        elif c == 429:      throttled  += 1

    log(f"  openclaw 201s={successes} 429s={throttled}")

    log("phase B: hermes single store (must succeed — quota is per-agent)")
    _, herm = h.write_memory(h.node2_ip, HERM, ns, title="hermes-still-ok",
                             content="quota isolated", include_status=True)
    herm_code = (herm or {}).get("http_code") if isinstance(herm, dict) else 0

    log("phase C: memory_quota_status per agent")
    _, q_open = h.http_on(h.node1_ip, "GET",
                          f"/api/v1/quota/status?agent_id={OPEN}", agent_id=OPEN)
    _, q_herm = h.http_on(h.node2_ip, "GET",
                          f"/api/v1/quota/status?agent_id={HERM}", agent_id=HERM)

    open_used = (q_open or {}).get("used", 0) if isinstance(q_open, dict) else 0
    herm_used = (q_herm or {}).get("used", 0) if isinstance(q_herm, dict) else 0

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
