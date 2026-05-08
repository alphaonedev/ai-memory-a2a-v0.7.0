#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 6 — Contradiction detection.

alice writes "<TOPIC> is blue". bob writes "<TOPIC> is red". charlie calls
/api/v1/contradictions?topic=... and must see both memories + a contradicts
relation.
"""

import sys
import pathlib
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "6"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario6-contradiction"
    topic = f"sky-color-{new_uuid()[:8]}"

    log(f"alice writes claim: \"{topic} is blue\" on node-1")
    rc_a, resp_a = h.write_memory(
        h.node1_ip, "ai:alice", ns,
        title=f"{topic}-alice",
        content=f"{topic} is blue",
        metadata={"topic": topic},
    )
    m_alice = (resp_a or {}).get("id") if isinstance(resp_a, dict) else None
    m_alice = m_alice or (resp_a or {}).get("memory_id") if isinstance(resp_a, dict) else None

    log(f"bob writes contradicting claim: \"{topic} is red\" on node-2")
    rc_b, resp_b = h.write_memory(
        h.node2_ip, "ai:bob", ns,
        title=f"{topic}-bob",
        content=f"{topic} is red",
        metadata={"topic": topic},
    )
    m_bob = (resp_b or {}).get("id") if isinstance(resp_b, dict) else None
    m_bob = m_bob or (resp_b or {}).get("memory_id") if isinstance(resp_b, dict) else None

    log(f"  alice.id={m_alice} bob.id={m_bob}")
    h.settle(10, reason="quorum fanout + contradiction indexing")

    log("charlie queries /api/v1/contradictions on node-3")
    q = urllib.parse.urlencode({"topic": topic, "namespace": ns})
    rc_d, doc = h.http_on(h.node3_ip, "GET", f"/api/v1/contradictions?{q}", include_status=True)
    detect_code = (doc or {}).get("http_code", 0) if isinstance(doc, dict) else 0
    body = (doc or {}).get("body") if isinstance(doc, dict) else None
    log(f"  HTTP {detect_code}")

    sees_both = False
    sees_link = False
    if detect_code == 200 and isinstance(body, dict):
        pool = (body.get("memories") or []) + (body.get("contradictions") or [])
        ids_seen = {(m.get("id") or m.get("memory_id") or "") for m in pool if isinstance(m, dict)}
        sees_both = bool(m_alice) and bool(m_bob) and m_alice in ids_seen and m_bob in ids_seen
        rels = (body.get("links") or []) + (body.get("relations") or [])
        for r in rels:
            if isinstance(r, dict):
                relation = (r.get("relation") or r.get("type") or "")
                if "contradict" in relation.lower():
                    sees_link = True
                    break

    log(f"  sees both memories: {sees_both}; sees contradicts link: {sees_link}")

    reasons: list[str] = []
    passed = True
    if detect_code != 200:
        passed = False
        reasons.append(f"detect_contradiction endpoint returned HTTP {detect_code}")
    if not sees_both:
        passed = False
        reasons.append("response did not include both memories")
    if not sees_link:
        passed = False
        reasons.append("response did not include a contradicts relation")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        topic=topic,
        alice_id=m_alice or "",
        bob_id=m_bob or "",
        detect_http_code=detect_code,
        charlie_sees_both_memories=sees_both,
        charlie_sees_contradicts_link=sees_link,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
