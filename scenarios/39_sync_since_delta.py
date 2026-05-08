#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 39 — /api/v1/sync/since delta sync.

While node-3's ai-memory serve is suspended, alice + bob write N rows.
Resume node-3. Query node-1's /sync/since?after=<pre-partition-checkpoint>
from node-3 and verify the response contains exactly the N rows that
were written during the partition.
"""

import sys
import pathlib
import time
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "39"
N = 6


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = f"scenario39-delta-{new_uuid()[:6]}"

    # Checkpoint: handler (handlers.rs:2306) requires RFC 3339, not unix ms.
    # Subtract 1s so the `since > checkpoint` comparison is strict-inclusive
    # of our writes.
    checkpoint = (datetime.now(timezone.utc).replace(microsecond=0)).isoformat()
    log(f"checkpoint = {checkpoint}")

    log("suspending ai-memory on node-3")
    h.ssh_exec(h.node3_ip, "pgrep -f 'ai-memory serve' | xargs -r kill -STOP", timeout=30)
    time.sleep(2)

    markers: list[str] = []
    log(f"alice + bob write {N} rows while node-3 is out")
    for i in range(N):
        u = new_uuid(f"delta-{i}-")
        markers.append(u)
        writer_ip = h.node1_ip if i % 2 == 0 else h.node2_ip
        writer_id = "ai:alice" if i % 2 == 0 else "ai:bob"
        h.write_memory(writer_ip, writer_id, ns,
                       title=f"delta-{i}", content=f"marker={u}")

    log("resuming ai-memory on node-3")
    h.ssh_exec(h.node3_ip, "pgrep -f 'ai-memory serve' | xargs -r kill -CONT", timeout=30)
    # v0.6.2 (S39 follow-up): 4s was the prior settle — empirically produced
    # empty curl bodies under mtls with `diag_updated_since=null`, suggesting
    # node-1's TLS/serve wasn't consistently ready to answer from node-3's
    # vantage. Bump to 15s + actively poll node-1's health from node-3 so we
    # block until the link is proven hot before issuing the /sync/since call.
    h.settle(15, reason="process resume + federation catchup")
    import shlex as _sh
    # v0.6.2 (S39 RCA): DO firewall blocks port 9077 on PUBLIC interfaces
    # (terraform main.tf:192-194). curl from node-3 TO node-1 must go via
    # node-1's VPC PRIVATE IP so the packet stays inside the VPC and the
    # firewall allows it. Using `.node1_ip` (public) → 15 s curl timeout
    # → empty body → rows_returned_raw=0 on every v3r22/v3r23/v3r24 run.
    peer_host = h.node1_priv or h.node1_ip  # back-compat fallback
    if h.tls_mode == "off":
        health_cmd = f"curl -sSf --max-time 5 {_sh.quote(f'http://{peer_host}:9077/api/v1/health')}"
    else:
        health_cmd = (
            f"curl -sSf --max-time 5 --cacert /etc/ai-memory-a2a/tls/ca.pem "
            + ("--cert /etc/ai-memory-a2a/tls/client.pem --key /etc/ai-memory-a2a/tls/client.key "
               if h.tls_mode == "mtls" else "")
            + f"{_sh.quote(f'https://{peer_host}:9077/api/v1/health')}"
        )
    health_ok = False
    import time as _t
    for attempt in range(10):
        r = h.ssh_exec(h.node3_ip, health_cmd, timeout=10)
        if r.returncode == 0 and '"ok"' in (r.stdout or ""):
            health_ok = True
            break
        _t.sleep(2)
    log(f"  node-3 → node-1 health reachable: {health_ok} (after {attempt+1} probes)")

    log(f"node-3 asks node-1 /api/v1/sync/since?since={checkpoint}")
    # Use a plain curl from node-3 (we don't want to inject node-3's own view).
    # `since` is RFC3339; `namespace` is filtered client-side (handler doesn't
    # accept it — it returns the full post-checkpoint delta capped at limit).
    # v0.6.2 (S39 follow-up): capture http_code + stderr so empty-body
    # failures have actionable detail instead of "returned 0/6".
    since_q = urllib.parse.quote(checkpoint)
    target = (
        f"http://{peer_host}:9077/api/v1/sync/since?since={since_q}&limit=500"
        if h.tls_mode == "off"
        else f"https://{peer_host}:9077/api/v1/sync/since?since={since_q}&limit=500"
    )
    tls_flags = ""
    if h.tls_mode != "off":
        tls_flags = "--cacert /etc/ai-memory-a2a/tls/ca.pem "
        if h.tls_mode == "mtls":
            tls_flags += "--cert /etc/ai-memory-a2a/tls/client.pem --key /etc/ai-memory-a2a/tls/client.key "
    cmd = (
        f"curl -sS --max-time 15 -w '\\n__HTTP_CODE__%{{http_code}}__' "
        f"{tls_flags}{_sh.quote(target)}"
    )
    r = h.ssh_exec(h.node3_ip, cmd, timeout=30)
    raw_stdout = (r.stdout or "")
    curl_http_code = 0
    delta_body = raw_stdout
    if "__HTTP_CODE__" in raw_stdout:
        delta_body, _, tail = raw_stdout.rpartition("__HTTP_CODE__")
        try:
            curl_http_code = int(tail.strip().rstrip("_"))
        except ValueError:
            curl_http_code = 0
    delta_body = delta_body.strip()
    curl_stderr = (r.stderr or "").strip()[:400]
    log(f"  curl exit={r.returncode} http_code={curl_http_code} body_len={len(delta_body)} stderr={curl_stderr!r}")

    import json as _json
    returned = 0
    returned_raw = 0
    present = 0
    diag_updated_since = None
    diag_earliest = None
    diag_latest = None
    try:
        parsed = _json.loads(delta_body) if delta_body else {}
        if isinstance(parsed, dict):
            # PR alphaonedev/ai-memory-mcp#361 adds these diagnostic fields.
            diag_updated_since = parsed.get("updated_since")
            diag_earliest = parsed.get("earliest_updated_at")
            diag_latest = parsed.get("latest_updated_at")
            pool = parsed.get("memories") or parsed.get("rows") or parsed.get("delta") or []
            returned_raw = len(pool)
            # Client-side namespace filter — endpoint returns global delta.
            pool_ns = [m for m in pool if isinstance(m, dict) and m.get("namespace") == ns]
            returned = len(pool_ns)
            for m in pool_ns:
                content = m.get("content") or ""
                if any(marker in content for marker in markers):
                    present += 1
    except _json.JSONDecodeError:
        pass

    log(f"  /sync/since raw={returned_raw} ns-filtered={returned}; {present}/{N} match our markers")
    log(f"  diag: updated_since={diag_updated_since} earliest={diag_earliest} latest={diag_latest}")

    reasons: list[str] = []
    passed = True
    if present != N:
        passed = False
        reasons.append(f"delta returned {present}/{N} expected markers — delta-sync incomplete")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        namespace=ns,
        checkpoint=checkpoint,
        expected_markers=N,
        markers_present=present,
        rows_returned=returned,
        rows_returned_raw=returned_raw,
        diag_updated_since=diag_updated_since,
        diag_earliest_updated_at=diag_earliest,
        diag_latest_updated_at=diag_latest,
        diag_node3_health_reachable=health_ok,
        diag_curl_exit=r.returncode,
        diag_curl_http_code=curl_http_code,
        diag_curl_body_head=(delta_body[:300] if delta_body else ""),
        diag_curl_stderr=curl_stderr,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
