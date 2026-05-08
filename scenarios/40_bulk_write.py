#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 40 — /api/v1/memories/bulk bulk write quorum + fanout.

alice posts 500 memories in a single /bulk call on node-1. After settle,
node-2, node-3, and node-4 aggregator each must report the full 500.
"""

import sys
import json
import pathlib
import shlex

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "40"
BULK_SIZE = 500


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    ns = f"scenario40-bulk-{new_uuid()[:6]}"

    log(f"constructing {BULK_SIZE}-row bulk payload")
    rows = [
        {
            "tier": "mid", "namespace": ns,
            "title": f"b-{i}",
            "content": f"bulk-marker={new_uuid()}",
            "priority": 5, "confidence": 1.0, "source": "api",
            "metadata": {"agent_id": "ai:alice", "scenario": "40", "bulk_seq": i},
        }
        for i in range(BULK_SIZE)
    ]
    # bulk_create handler (handlers.rs:1873) takes `Json<Vec<CreateMemory>>` —
    # bare array, NOT {"memories": [...]}.
    log("staging bulk payload on node-1 /tmp, then POST /api/v1/memories/bulk")
    json_payload = json.dumps(rows)
    stage_cmd = f"cat > /tmp/s40-bulk.json <<'PAYLOAD_EOF'\n{json_payload}\nPAYLOAD_EOF"
    r_stage = h.ssh_exec(h.node1_ip, stage_cmd, timeout=60)
    if r_stage.returncode != 0:
        h.emit(passed=False, reason=f"failed to stage bulk payload: {r_stage.stderr[:200]}",
               reasons=[f"staging failed rc={r_stage.returncode}"])

    curl = h._remote_curl_prefix()
    post_cmd = (
        f"{curl} -o /dev/null -w '%{{http_code}}' "
        f"-X POST {shlex.quote(f'{h.remote_base_url()}/api/v1/memories/bulk')} "
        f"-H 'X-Agent-Id: ai:alice' -H 'Content-Type: application/json' "
        f"--data-binary @/tmp/s40-bulk.json"
    )
    r_post = h.ssh_exec(h.node1_ip, post_cmd, timeout=60)
    write_code = (r_post.stdout or "0").strip() or "0"
    log(f"  bulk POST returned HTTP {write_code}")
    h.settle(20, reason="bulk fanout across 3 peers + aggregator")

    per_peer: dict[str, int] = {}
    for name, ip in (("node-2", h.node2_ip), ("node-3", h.node3_ip), ("node-4", h.node4_ip)):
        _, resp = h.list_memories(ip, ns, limit=BULK_SIZE + 50)
        n = len((resp or {}).get("memories") or []) if isinstance(resp, dict) else 0
        per_peer[name] = n
        log(f"  {name} count={n} (expected {BULK_SIZE})")

    reasons: list[str] = []
    passed = True
    if write_code not in {"200", "201", "202", "204"}:
        passed = False
        reasons.append(f"bulk returned HTTP {write_code}")
    for name, n in per_peer.items():
        if n != BULK_SIZE:
            passed = False
            reasons.append(f"{name} saw {n}/{BULK_SIZE} bulk rows after fanout")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        namespace=ns,
        bulk_http_code=write_code,
        bulk_size=BULK_SIZE,
        per_peer_count={n.replace("-", "_"): v for n, v in per_peer.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
