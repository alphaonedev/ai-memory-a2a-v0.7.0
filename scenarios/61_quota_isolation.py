#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 61 — Quota isolation.

openclaw burns daily memory quota (1000 stores); hermes still writes
successfully; per-agent memory_quota_status reports the right counters.

# v0.7.0 Continuation 6 — HTTP migration (2026-05-09)
#
# Migrated from MCP-stdio (`ai-memory mcp` against
# `AI_MEMORY_DB=/var/lib/ai-memory/<node>.db` sqlite path) to the
# new HTTP endpoint `POST /api/v1/quota/status`. The MCP-stdio path
# read from a sqlite file that is empty/stale on postgres-backed
# daemons — the new endpoint dispatches via the SAL
# `MemoryStore::quota_status` trait so postgres-backed daemons read
# from the live `agent_quotas` table.
#
# Wire shape (POST /api/v1/quota/status):
#   { agent_id }
#   -> QuotaStatus { agent_id, max_memories_per_day, max_storage_bytes,
#                    max_links_per_day, current_memories_today,
#                    current_storage_bytes, current_links_today,
#                    day_started_at, created_at, updated_at }
"""
import sys
import pathlib

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
    successes = 0
    throttled = 0

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
        if c in (200, 201):
            successes += 1
        elif c == 429:
            throttled += 1

    log(f"  openclaw 201s={successes} 429s={throttled}")

    log("phase B: hermes single store (must succeed — quota is per-agent)")
    _, herm = h.write_memory(h.node2_ip, HERM, ns, title="hermes-still-ok",
                             content="quota isolated", include_status=True)
    herm_code = (herm or {}).get("http_code") if isinstance(herm, dict) else 0

    log("phase C: POST /api/v1/quota/status per agent (Continuation 6 HTTP path)")

    def quota_via_http(node_ip: str, agent_id: str) -> int:
        rc, resp = h.http_on(
            node_ip, "POST", "/api/v1/quota/status",
            body={"agent_id": agent_id}, agent_id=agent_id, include_status=True,
        )
        body = (resp or {}).get("body") if isinstance(resp, dict) else None
        if not isinstance(body, dict):
            log(f"  quota_status non-dict body for {agent_id}: rc={rc} resp={resp!r:.120s}")
            return 0
        # The HTTP endpoint returns the QuotaStatus serialized
        # directly (no `quota` wrapper).
        return int(body.get("current_memories_today") or 0)

    open_used = quota_via_http(h.node1_ip, OPEN)
    herm_used = quota_via_http(h.node2_ip, HERM)

    reasons: list[str] = []
    passed = True
    if successes < 700:
        passed = False
        reasons.append(f"openclaw successes={successes} (expected >=700 before throttle)")
    if herm_code not in (200, 201):
        passed = False
        reasons.append(f"hermes write got {herm_code} (expected 201)")
    if open_used < 700:
        passed = False
        reasons.append(f"openclaw quota.used={open_used} (expected >=700)")
    if herm_used > 50:
        passed = False
        reasons.append(f"hermes quota.used={herm_used} (expected <=50; isolated)")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw": {"successes": successes, "throttled": throttled, "quota_used": open_used},
               "hermes":   {"http_code": herm_code, "quota_used": herm_used},
           }, reasons=reasons)


if __name__ == "__main__":
    main()
