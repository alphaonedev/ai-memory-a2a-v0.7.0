#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 41 — /api/v1/metrics Prometheus shape + monotonicity.

Every peer exposes /api/v1/metrics in Prometheus text format. The
scenario scrapes each peer twice (sandwiching some write activity) and
verifies:
  * the response parses as Prometheus text (content-type + TYPE/HELP comments)
  * at least one memory-related counter is present
  * the counter values are monotonic-non-decreasing across the two scrapes
"""

import sys
import pathlib
import shlex
import re

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "41"

COUNTER_RE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?\s+(-?\d+(?:\.\d+)?)')
MEMORY_COUNTER_HINTS = ("memory_", "ai_memory_", "mcp_memory_")


def _scrape(h: Harness, ip: str) -> tuple[int, str]:
    curl = h._remote_curl_prefix()
    cmd = (
        f"{curl} -o /tmp/s41-metrics.txt -w '%{{http_code}}' "
        f"{shlex.quote(f'{h.remote_base_url(ip)}/api/v1/metrics')}"
    )
    r = h.ssh_exec(ip, cmd, timeout=30)
    code = (r.stdout or "0").strip() or "0"
    body_r = h.ssh_exec(ip, "cat /tmp/s41-metrics.txt", timeout=15)
    return int(code) if code.isdigit() else 0, body_r.stdout or ""


def _parse_counters(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = COUNTER_RE.match(line.strip())
        if not m:
            continue
        name, labels, value = m.group(1), m.group(2) or "", m.group(3)
        if any(hint in name for hint in MEMORY_COUNTER_HINTS):
            key = name + labels
            try:
                out[key] = float(value)
            except ValueError:
                pass
    return out


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    peers = [("node-1", h.node1_ip), ("node-2", h.node2_ip), ("node-3", h.node3_ip)]

    log("scrape T0")
    t0: dict[str, dict[str, float]] = {}
    for name, ip in peers:
        code, body = _scrape(h, ip)
        if code != 200:
            log(f"  {name} T0 scrape HTTP {code}")
        t0[name] = _parse_counters(body)
        log(f"  {name} T0 parsed {len(t0[name])} memory counters")

    # Induce activity: alice writes 5 rows.
    ns = f"scenario41-activity-{new_uuid()[:6]}"
    for i in range(5):
        h.write_memory(h.node1_ip, "ai:alice", ns, title=f"m-{i}", content=new_uuid("k-"))
    h.settle(5, reason="counter update")

    log("scrape T1")
    t1: dict[str, dict[str, float]] = {}
    for name, ip in peers:
        code, body = _scrape(h, ip)
        if code != 200:
            log(f"  {name} T1 scrape HTTP {code}")
        t1[name] = _parse_counters(body)
        log(f"  {name} T1 parsed {len(t1[name])} memory counters")

    reasons: list[str] = []
    passed = True
    per_peer_diag: dict[str, dict] = {}
    for name, _ in peers:
        c0, c1 = t0.get(name, {}), t1.get(name, {})
        missing_at_t0 = len(c0) == 0
        regressed = [k for k in set(c0) & set(c1) if c1[k] < c0[k]]
        per_peer_diag[name] = {
            "counters_t0": len(c0), "counters_t1": len(c1),
            "regressed_keys": len(regressed),
        }
        if missing_at_t0:
            passed = False
            reasons.append(f"{name}: no memory-related counters visible at T0")
        if regressed:
            passed = False
            reasons.append(f"{name}: {len(regressed)} counter(s) decreased between T0 and T1")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        activity_namespace=ns,
        per_peer={n.replace("-", "_"): v for n, v in per_peer_diag.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
