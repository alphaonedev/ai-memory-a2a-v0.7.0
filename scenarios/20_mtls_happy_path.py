#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 20 — mTLS happy-path (tls_mode=mtls only).

alice writes via HTTPS presenting the campaign client cert. Peers node-2
and node-3 recall the write — also over HTTPS + client cert. Skipped
(pass=null) when tls_mode != mtls.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "20"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    if h.tls_mode != "mtls":
        h.skip(f"scenario 20 only runs under tls_mode=mtls (actual: {h.tls_mode})")

    ns = "scenario20-mtls"
    marker = new_uuid("mtls-")
    content = f"scenario20 mTLS happy-path marker={marker}"

    log("alice writes HTTPS + client cert on node-1")
    _, write_doc = h.write_memory(
        h.node1_ip, "ai:alice", ns,
        title=f"mtls-alice-{marker}",
        content=content, tier="long",
        include_status=True,
    )
    write_code = (write_doc or {}).get("http_code", 0) if isinstance(write_doc, dict) else 0
    log(f"  write returned HTTP {write_code}")
    h.settle(12, reason="W=2/N=4 quorum")

    sees: dict[str, int] = {}
    for name, ip in (("node-2", h.node2_ip), ("node-3", h.node3_ip)):
        n = h.count_matching(ip, ns, content_contains=marker, limit=20)
        sees[name] = n
        log(f"  {name} sees marker: {n}")

    reasons: list[str] = []
    passed = True
    if write_code not in (200, 201):
        passed = False
        reasons.append(f"write returned HTTP {write_code} (expected 200/201)")
    for name, n in sees.items():
        if n < 1:
            passed = False
            reasons.append(f"{name} did not see the mTLS write after settle")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        marker=marker,
        write_http_code=write_code,
        peers_see={n.replace("-", "_"): v for n, v in sees.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
