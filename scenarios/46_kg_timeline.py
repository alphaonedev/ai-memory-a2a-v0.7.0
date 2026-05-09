#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 46 — memory_kg_timeline event ordering (v0.6.3).

Creates and invalidates KG edges over a controlled time window, then
asks /api/v1/kg/timeline for the source memory's event log. The
contract:

  * Events are returned in chronological order (ascending timestamp).
  * Each event carries a `type` from the v0.6.3 enum:
      - `edge_added`         — new edge (links POST)
      - `edge_invalidated`   — kg_invalidate sets valid_until
  * The timeline contains BOTH adds AND invalidations for our source.

Without ordered, typed events the agent has no way to reconstruct
"how did this graph get to its current state?" — this scenario protects
against a regression that returns events out of order or merges them
into a single opaque type.
"""

import sys
import pathlib
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "46"


def _id_of(resp: object) -> str:
    if isinstance(resp, dict):
        return resp.get("id") or resp.get("memory_id") or ""
    return ""


def _ts_of(event: dict) -> float:
    """Pull a sortable timestamp out of a timeline event, tolerating
    multiple shapes (epoch float, ISO string, ms int).

    v0.7 timeline events use `valid_from` as the ordering key; we list it
    first so historical-shape fields don't shadow it."""
    for k in ("valid_from", "timestamp", "ts", "at", "occurred_at", "time"):
        v = event.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and v:
            try:
                # ISO-8601 with trailing Z
                tt = time.strptime(v.rstrip("Z").split(".")[0], "%Y-%m-%dT%H:%M:%S")
                return time.mktime(tt)
            except ValueError:
                continue
    return 0.0


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = f"scenario46-tl-{new_uuid()[:6]}"
    agent = f"ai:s46-{new_uuid()[:6]}"

    log("seed: write source M0 + 2 targets on node-1")
    _, r0 = h.write_memory(h.node1_ip, agent, ns, title="tl-source", content="tl-M0")
    m0 = _id_of(r0)
    _, r1 = h.write_memory(h.node1_ip, agent, ns, title="tl-t1", content="tl-T1")
    _, r2 = h.write_memory(h.node1_ip, agent, ns, title="tl-t2", content="tl-T2")
    t1 = _id_of(r1)
    t2 = _id_of(r2)
    log(f"  M0={m0} T1={t1} T2={t2}")
    if not (m0 and t1 and t2):
        h.emit(passed=False, reason="seed writes failed",
               m0_id=m0, t1_id=t1, t2_id=t2, reasons=["seed write failed"])

    h.settle(3, reason="seed settle")

    log("create edge M0->T1 (event 1: edge_added)")
    h.http_on(h.node1_ip, "POST", "/api/v1/links",
              body={"source_id": m0, "target_id": t1, "relation": "related_to"},
              agent_id=agent, include_status=True)
    time.sleep(2)

    log("create edge M0->T2 (event 2: edge_added)")
    h.http_on(h.node1_ip, "POST", "/api/v1/links",
              body={"source_id": m0, "target_id": t2, "relation": "related_to"},
              agent_id=agent, include_status=True)
    time.sleep(2)

    log("invalidate M0->T1 (event 3: edge_invalidated)")
    h.http_on(h.node1_ip, "POST", "/api/v1/kg/invalidate",
              body={"source_id": m0, "target_id": t1, "relation": "related_to"},
              agent_id=agent, include_status=True)
    h.settle(4, reason="timeline indexer")

    log(f"GET /api/v1/kg/timeline?source_id={m0} on node-1")
    _, doc = h.http_on(h.node1_ip, "GET",
                       f"/api/v1/kg/timeline?source_id={m0}",
                       include_status=True)
    code = (doc or {}).get("http_code", 0) if isinstance(doc, dict) else 0
    body = (doc or {}).get("body") if isinstance(doc, dict) else None
    log(f"  HTTP {code}")

    events: list[dict] = []
    if isinstance(body, dict):
        events = body.get("events") or body.get("timeline") or body.get("items") or []
    elif isinstance(body, list):
        events = body
    events = [e for e in events if isinstance(e, dict)]

    # v0.7 timeline shape: each row = one edge with valid_from (when added) and
    # optional valid_until (set when invalidated). There is no separate `type`
    # field; "added" and "invalidated" are both inferred from these timestamps.
    types_seen = [
        ("invalidated" if e.get("valid_until") else "added")
        for e in events
    ]
    log(f"  {len(events)} edge events, derived_types={types_seen}")

    has_added = any(t == "added" for t in types_seen)
    has_invalidated = any(t == "invalidated" for t in types_seen)

    # Chronological ordering by valid_from.
    timestamps = [_ts_of(e) for e in events]
    monotonic = all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))

    reasons: list[str] = []
    passed = True
    if code != 200:
        passed = False
        reasons.append(f"timeline endpoint returned HTTP {code}")
    # v0.7 emits one row per edge (M0->T1, M0->T2). The "1 invalidate" surfaces
    # as `valid_until` on the M0->T1 row, not a separate event.
    if len(events) < 2:
        passed = False
        reasons.append(f"expected >=2 edge events (M0->T1 invalidated + M0->T2 live), got {len(events)}")
    if not has_added:
        passed = False
        reasons.append("no live edge in timeline")
    if not has_invalidated:
        passed = False
        reasons.append("no invalidated edge in timeline (valid_until missing)")
    if events and not monotonic:
        passed = False
        reasons.append(f"events not in chronological order: ts={timestamps}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m0_id=m0,
        timeline_http_code=code,
        event_count=len(events),
        types=types_seen,
        chronological=monotonic,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
