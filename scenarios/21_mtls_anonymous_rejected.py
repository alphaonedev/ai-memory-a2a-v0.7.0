#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 21 — Anonymous client rejected (tls_mode=mtls only).

Attempt POST to node-1's HTTPS endpoint WITHOUT a client cert. rustls
must reject the TLS handshake (curl exits non-zero / no HTTP code).
If the server returns any HTTP status, the mTLS allowlist is bypassed.
"""

import sys
import pathlib
import shlex

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log


SCENARIO_ID = "21"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    if h.tls_mode != "mtls":
        h.skip(f"scenario 21 only runs under tls_mode=mtls (actual: {h.tls_mode})")

    # Anonymous curl: has --cacert for server-side verify, but NO --cert/--key.
    body = {
        "tier": "mid", "namespace": "scenario21", "title": "anon",
        "content": "should never land", "priority": 5,
        "confidence": 1.0, "source": "api",
    }
    anon_cmd = (
        "curl -sS --max-time 5 "
        "--cacert /etc/ai-memory-a2a/tls/ca.pem "
        "--resolve localhost:9077:127.0.0.1 "
        "-o /dev/null -w '%{http_code}|%{errormsg}' "
        "-X POST 'https://localhost:9077/api/v1/memories' "
        "-H 'Content-Type: application/json' "
        f"-d {shlex.quote('{}'.format(__import__('json').dumps(body)))} 2>&1 || true"
    )
    log("attempting anonymous HTTPS POST to node-1 (must be rejected)")
    r = h.ssh_exec(h.node1_ip, anon_cmd, timeout=20)
    raw = (r.stdout or "").strip() or "000|ssh-failed"
    code, _, msg = raw.partition("|")
    code = code.strip() or "000"
    log(f"  anonymous probe result: code={code} msg={msg[:200]}")

    h.settle(3, reason="let any leak land before checking namespace")

    # Confirm the write DID NOT land — any hit indicates the allowlist was bypassed.
    hit = h.count_matching(h.node1_ip, "scenario21", limit=10)
    log(f"  post-probe count for namespace=scenario21: {hit} (must be 0)")

    reasons: list[str] = []
    passed = True
    # HTTP 2xx/3xx/4xx means the server got the request — mTLS bypassed.
    if code and code[0] in "234":
        passed = False
        reasons.append(
            f"server accepted anonymous request — returned HTTP {code} — mTLS enforcement bypassed"
        )
    # 000 / any other = connection/handshake failure — expected.
    if hit != 0:
        passed = False
        reasons.append(
            f"anonymous write landed ({hit} rows in scenario21 namespace) — allowlist not enforcing"
        )

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        anonymous_probe={"http_code": code, "curl_message": msg[:500]},
        namespace_count_after_attempt=hit,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
