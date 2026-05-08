#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 38 — /api/v1/export + /api/v1/import backup/restore round-trip.

alice writes 5 memories on node-1. alice exports the namespace. bob
imports the exported data into a fresh namespace on node-2. count /
stats / content must match.
"""

import sys
import urllib.parse
import pathlib
import json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "38"
ROWS = 5


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    src_ns = f"scenario38-src-{suffix}"
    dst_ns = f"scenario38-dst-{suffix}"

    log(f"alice writes {ROWS} rows into {src_ns}")
    markers: list[str] = []
    for i in range(ROWS):
        m = new_uuid(f"exp-{i}-")
        markers.append(m)
        h.write_memory(h.node1_ip, "ai:alice", src_ns,
                       title=f"exp-{i}", content=f"marker={m}")
    h.settle(4, reason="pre-export replication")

    log(f"alice exports on node-1 (endpoint has no namespace filter; filter client-side)")
    _, export_doc = h.http_on(h.node1_ip, "GET", "/api/v1/export", include_status=True)
    export_code = (export_doc or {}).get("http_code", 0) if isinstance(export_doc, dict) else 0
    export_body = (export_doc or {}).get("body") if isinstance(export_doc, dict) else None
    total_rows = 0
    if isinstance(export_body, dict):
        total_rows = len(export_body.get("memories") or [])
    log(f"  export returned HTTP {export_code}, total_rows={total_rows}")

    # Filter to src_ns, rewrite namespace + id, import as ImportBody.
    # ImportBody { memories: Vec<Memory>, links: Option<Vec<MemoryLink>> }
    # — no namespace field; memories carry their own namespace field.
    # Regenerate ids so imported rows don't collide with the originals.
    rewrote: list[dict] = []
    if isinstance(export_body, dict):
        for m in (export_body.get("memories") or []):
            if isinstance(m, dict) and m.get("namespace") == src_ns:
                m2 = dict(m)
                m2["namespace"] = dst_ns
                m2["id"] = h.new_uuid("imp-")
                rewrote.append(m2)
    log(f"  rewrote {len(rewrote)} memories from {src_ns} -> {dst_ns}")
    rows_exported = len(rewrote)
    payload = {"memories": rewrote}

    log(f"bob imports the payload into {dst_ns} on node-2")
    _, import_doc = h.http_on(h.node2_ip, "POST", "/api/v1/import",
                              body=payload if isinstance(payload, dict) else {},
                              agent_id="ai:bob", include_status=True)
    import_code = (import_doc or {}).get("http_code", 0) if isinstance(import_doc, dict) else 0
    log(f"  import returned HTTP {import_code}")
    h.settle(6, reason="import + fanout")

    log("verify row counts match on destination")
    dst_count = 0
    _, resp = h.list_memories(h.node2_ip, dst_ns, limit=200)
    if isinstance(resp, dict):
        dst_count = len(resp.get("memories") or [])
    log(f"  {dst_ns} has {dst_count} rows (expected {ROWS})")

    # Verify marker content round-trip
    preserved = 0
    for m in markers:
        if h.count_matching(h.node2_ip, dst_ns, content_contains=m, limit=50) >= 1:
            preserved += 1
    log(f"  markers preserved in destination: {preserved}/{ROWS}")

    reasons: list[str] = []
    passed = True
    if export_code not in (200, 201):
        passed = False
        reasons.append(f"export returned HTTP {export_code}")
    if rows_exported < ROWS:
        passed = False
        reasons.append(f"export returned {rows_exported} rows (expected {ROWS})")
    if import_code not in (200, 201, 204):
        passed = False
        reasons.append(f"import returned HTTP {import_code}")
    if dst_count < ROWS:
        passed = False
        reasons.append(f"destination count {dst_count} < expected {ROWS}")
    if preserved < ROWS:
        passed = False
        reasons.append(f"only {preserved}/{ROWS} markers preserved")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        src_ns=src_ns, dst_ns=dst_ns,
        export_http_code=export_code,
        import_http_code=import_code,
        rows_exported=rows_exported,
        rows_in_destination=dst_count,
        markers_preserved=preserved,
        expected_rows=ROWS,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
