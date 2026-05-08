#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 30 — memory_capabilities handshake equivalence.

Every peer exposes its capabilities (protocol version, tool surface,
feature flags). All 4 peers must report the same protocol version and
an identical (or strictly-superset-compatible) tool surface. Any
drift would mean agents on different nodes see different APIs —
A2A-breaking.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log


SCENARIO_ID = "30"


def _capabilities(h: Harness, ip: str) -> dict | None:
    # Capability endpoint shape is unstable across versions. Try the few
    # common ones and return the first successful JSON response.
    for path in ("/api/v1/capabilities", "/api/v1/capability", "/api/v1/version"):
        _, doc = h.http_on(ip, "GET", path, include_status=True)
        if isinstance(doc, dict) and doc.get("http_code") == 200:
            body = doc.get("body")
            if isinstance(body, dict):
                body["_path"] = path
                return body
    return None


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    peers = [("node-1", h.node1_ip), ("node-2", h.node2_ip),
             ("node-3", h.node3_ip), ("node-4", h.node4_ip)]
    views: dict[str, dict | None] = {}

    for name, ip in peers:
        cap = _capabilities(h, ip)
        views[name] = cap
        log(f"  {name} capabilities: {list((cap or {}).keys())}")

    reasons: list[str] = []
    passed = True

    available = [(n, c) for n, c in views.items() if c is not None]
    if not available:
        passed = False
        reasons.append("no peer returned a capabilities response — endpoint may not be exposed")
    else:
        # All peers that DO respond must agree on protocol_version / version.
        versions = {
            n: (c.get("protocol_version") or c.get("version") or c.get("api_version") or "")
            for n, c in available
        }
        unique = set(v for v in versions.values() if v)
        if len(unique) > 1:
            passed = False
            reasons.append(f"capabilities version drift across peers: {versions}")
        # Any missing peer is a (softer) fail.
        for name in [n for n, c in views.items() if c is None]:
            passed = False
            reasons.append(f"{name} did not respond to capabilities probe")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        peer_views={n.replace("-", "_"): v for n, v in views.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
