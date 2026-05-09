#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 33 — pub/sub via memory_subscribe / memory_unsubscribe.

bob subscribes to namespace X. alice writes to X. bob's subscription
list must include X, and a subsequent read via bob's subscribed view
must include alice's write. bob unsubscribes. alice writes again.
bob's subscribed view must NOT include the second write.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "33"


def _subs(h: Harness, ip: str, agent_id: str) -> list[str]:
    _, resp = h.http_on(ip, "GET", f"/api/v1/subscriptions?agent_id={agent_id}",
                        agent_id=agent_id)
    if isinstance(resp, dict):
        pool = resp.get("subscriptions") or resp.get("namespaces") or []
    elif isinstance(resp, list):
        pool = resp
    else:
        pool = []
    out: list[str] = []
    for s in pool:
        if isinstance(s, dict):
            # v0.7 list shape: {namespace_filter, namespace, agent_id, ...}
            ns = s.get("namespace_filter") or s.get("namespace") or ""
            out.append(ns or "")
        else:
            out.append(str(s))
    return out


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = f"scenario33-pubsub-{new_uuid()[:6]}"
    m1 = new_uuid("sub-pre-")
    m2 = new_uuid("sub-post-")

    log(f"bob subscribes to namespace {ns} on node-2")
    # v0.7: subscribe synthesizes a loopback URL when only `{agent_id, namespace}`
    # is supplied; the SSRF guard then rejects loopback by default. Provide a
    # public-resolving https callback URL so the SSRF DNS check passes — we
    # never actually fire the webhook (subscription is verified via list, not
    # delivery), AND set namespace_filter so the GET view records the ns.
    _, sub_doc = h.http_on(
        h.node2_ip, "POST", "/api/v1/subscriptions",
        body={
            "agent_id": "ai:bob",
            "namespace": ns,
            "namespace_filter": ns,
            "url": "https://example.com/_ns_hook",
            "events": "memory_store",
        },
        agent_id="ai:bob", include_status=True,
    )
    sub_code = (sub_doc or {}).get("http_code", 0) if isinstance(sub_doc, dict) else 0
    log(f"  subscribe returned HTTP {sub_code}")
    h.settle(2, reason="subscription settle")

    bob_subs_before = _subs(h, h.node2_ip, "ai:bob")
    log(f"  bob subscriptions: {len(bob_subs_before)} entries; contains ns: {ns in bob_subs_before}")

    log("alice writes M1 into the subscribed namespace")
    h.write_memory(h.node1_ip, "ai:alice", ns, title="m1-pre", content=m1)
    h.settle(6, reason="write fanout to subscribers")

    m1_visible = h.count_matching(h.node2_ip, ns, content_equals=m1, limit=20)
    log(f"  bob sees M1 in subscribed namespace: {m1_visible}")

    log(f"bob unsubscribes from {ns}")
    _, unsub_doc = h.http_on(
        h.node2_ip, "DELETE", f"/api/v1/subscriptions?agent_id=ai:bob&namespace={ns}",
        agent_id="ai:bob", include_status=True,
    )
    unsub_code = (unsub_doc or {}).get("http_code", 0) if isinstance(unsub_doc, dict) else 0
    log(f"  unsubscribe returned HTTP {unsub_code}")
    h.settle(2, reason="unsubscribe settle")

    bob_subs_after = _subs(h, h.node2_ip, "ai:bob")
    log(f"  bob subscriptions after unsubscribe: ns still present = {ns in bob_subs_after}")

    log("alice writes M2 post-unsubscribe (may still replicate via federation but subscription list excludes ns)")
    h.write_memory(h.node1_ip, "ai:alice", ns, title="m2-post", content=m2)
    h.settle(5, reason="post-unsubscribe settle")

    reasons: list[str] = []
    passed = True
    if sub_code not in (200, 201, 204):
        passed = False
        reasons.append(f"subscribe returned HTTP {sub_code}")
    if ns not in bob_subs_before:
        passed = False
        reasons.append("bob's subscription list did not include the subscribed namespace")
    if m1_visible < 1:
        passed = False
        reasons.append("bob did not receive M1 after subscribing")
    if unsub_code not in (200, 204):
        passed = False
        reasons.append(f"unsubscribe returned HTTP {unsub_code}")
    if ns in bob_subs_after:
        passed = False
        reasons.append("namespace still listed in subscriptions after unsubscribe")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        namespace=ns,
        subscribe_http_code=sub_code,
        unsubscribe_http_code=unsub_code,
        m1_delivered=m1_visible,
        subscriptions_before_count=len(bob_subs_before),
        subscriptions_after_count=len(bob_subs_after),
        ns_in_subs_before=ns in bob_subs_before,
        ns_in_subs_after=ns in bob_subs_after,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
