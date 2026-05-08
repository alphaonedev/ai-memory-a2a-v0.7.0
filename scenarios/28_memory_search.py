#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 28 — memory_search keyword A2A.

Distinct from /recall (semantic) and /expand_query: /search is literal
keyword match. alice writes a memory containing a unique token; bob and
charlie search for that exact token and must return >=1 hit each.
"""

import sys
import urllib.parse
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "28"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario28-search"
    # No hyphens: FTS5 unicode61 tokenizer splits on `-` at index time,
    # and sanitize_fts_query (db.rs:1544) currently strips `-` from query
    # tokens, so a hyphenated needle would never match. Use alnum only.
    token = f"kwsearch{new_uuid()[:10]}"

    log(f"alice writes a row containing unique token={token}")
    h.write_memory(h.node1_ip, "ai:alice", ns,
                   title="keyword-needle",
                   content=f"The operational needle we search for is {token} embedded in prose.")
    h.settle(8, reason="search index populate + fanout")

    log("bob + charlie call /api/v1/search with the exact token")
    peer_hits: dict[str, int] = {}
    for name, ip in (("node-2", h.node2_ip), ("node-3", h.node3_ip)):
        q = urllib.parse.urlencode({"q": token, "namespace": ns, "limit": 20})
        _, resp = h.http_on(ip, "GET", f"/api/v1/search?{q}")
        hits = 0
        if isinstance(resp, dict):
            for m in (resp.get("memories") or resp.get("results") or []):
                if isinstance(m, dict) and token in (m.get("content") or ""):
                    hits += 1
        peer_hits[name] = hits
        log(f"  {name} keyword search returned {hits} hits")

    reasons: list[str] = []
    passed = True
    for name, hits in peer_hits.items():
        if hits < 1:
            passed = False
            reasons.append(f"{name} did not find the unique token via /search")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        token=token,
        peer_hits={n.replace("-", "_"): v for n, v in peer_hits.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
