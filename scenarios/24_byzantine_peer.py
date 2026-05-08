#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 24 — Byzantine peer / tampered sync integrity.

Node-2 manually crafts a sync_push to node-3 claiming sender_agent_id=
ai:alice (when a truthful node-2 would say ai:bob), with a memory
claiming metadata.agent_id=ai:alice. Node-3 should either:
  a) Reject the sync, OR
  b) Store it with metadata.agent_id preserved as declared (ai:alice) —
     never silently re-attributed to the transport identity (node-2's
     actual agent).
Silent re-attribution = Task 1.2 immutability breach.

Requires NODE2_PRIV + NODE3_PRIV env (private IPs for peer URLs).
"""

import sys
import os
import json
import pathlib
import shlex

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "24"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    node2_priv = os.environ.get("NODE2_PRIV", "")
    node3_priv = os.environ.get("NODE3_PRIV", "")
    if not node2_priv or not node3_priv:
        h.skip("NODE2_PRIV / NODE3_PRIV not set — private peer URLs unavailable")

    ns = "scenario24-byzantine"
    marker = new_uuid("bz-")
    scheme = "http" if h.tls_mode == "off" else "https"
    peer_url = f"{scheme}://{node3_priv}:9077"

    # Craft the sync_push payload.
    payload = {
        "sender_agent_id": "ai:alice",
        "sender_clock": {"entries": {"ai:alice": 1}},
        "memories": [{
            "tier": "long", "namespace": ns,
            "title": "bz-injected",
            "content": f"byzantine-marker={marker}",
            "priority": 5, "confidence": 1.0, "source": "api",
            "metadata": {"agent_id": "ai:alice", "scenario": "24"},
        }],
        "dry_run": False,
    }

    curl_flags = "curl -sS"
    if h.tls_mode != "off":
        curl_flags += " --cacert /etc/ai-memory-a2a/tls/ca.pem"
        if h.tls_mode == "mtls":
            curl_flags += " --cert /etc/ai-memory-a2a/tls/client.pem --key /etc/ai-memory-a2a/tls/client.key"

    remote_cmd = (
        f"{curl_flags} -o /tmp/s24-resp.json -w '%{{http_code}}' "
        f"-X POST {shlex.quote(peer_url + '/api/v1/sync/push')} "
        f"-H 'Content-Type: application/json' -d {shlex.quote(json.dumps(payload))}"
    )
    log("node-2 sends sync_push to node-3 claiming sender_agent_id=ai:alice")
    r = h.ssh_exec(h.node2_ip, remote_cmd, timeout=30)
    push_code = (r.stdout or "0").strip() or "0"
    log(f"  sync_push returned HTTP {push_code}")

    h.settle(5, reason="server-side sync apply")

    # Read back on node-3 and inspect stored metadata.agent_id.
    _, resp = h.list_memories(h.node3_ip, ns, limit=20)
    stored_aid = "ABSENT"
    if isinstance(resp, dict):
        for m in resp.get("memories") or []:
            if isinstance(m, dict) and marker in (m.get("content") or ""):
                stored_aid = ((m.get("metadata") or {}).get("agent_id")) or "ABSENT"
                break
    log(f"  node-3 stored metadata.agent_id={stored_aid} (declared: ai:alice)")

    reasons: list[str] = []
    passed = True
    if push_code in {"200", "201", "202"}:
        pass
    elif push_code in {"400", "401", "403", "422"}:
        log(f"  sync_push rejected HTTP {push_code} — stricter-than-spec, acceptable")
        stored_aid = "REJECTED_BY_SERVER"
    else:
        passed = False
        reasons.append(f"sync_push returned unexpected HTTP {push_code}")

    if stored_aid not in {"ai:alice", "ABSENT", "REJECTED_BY_SERVER"}:
        passed = False
        reasons.append(
            f"Task 1.2 violated: stored agent_id={stored_aid} (expected ai:alice, ABSENT, or REJECTED)"
        )

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        byzantine_marker=marker,
        sync_push_http_code=push_code,
        stored_metadata_agent_id=stored_aid,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
