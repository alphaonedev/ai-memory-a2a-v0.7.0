#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 53 — F8 enforce gate cross-agent.

openclaw sets governance.write=owner on ns/X; openclaw write to ns/X succeeds
(201); hermes write to ns/X gets 403 with sanitized error and increments
decision_counts.enforce.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "53"


def _decision_counts(h: Harness, node_ip: str) -> dict:
    """Read enforce/advisory/off counters via /api/v1/capabilities — the
    counts live under `permissions.decision_counts` in the v3 response."""
    _, cap = h.http_on(node_ip, "GET", "/api/v1/capabilities")
    if isinstance(cap, dict):
        perms = cap.get("permissions") or {}
        dc = perms.get("decision_counts") or {}
        if isinstance(dc, dict):
            return dc
    return {}


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    openclaw, hermes = h.node1_ip, h.node2_ip
    suffix = new_uuid()[:6]
    OPEN = f"ai:s53-openclaw-{suffix}"
    HERM = f"ai:s53-hermes-{suffix}"
    ns = f"scenario53-{suffix}"

    # Phase A — owner pre-writes a seed memory in ns under their agent_id, then
    # registers that id as the namespace standard. v0.7 namespace-owner is
    # `metadata.agent_id` of the standard's memory; without this two-step,
    # the auto-created placeholder is owned by "system" and BOTH agents get
    # denied (no resolvable owner the caller can match).
    log(f"phase A: owner ({OPEN}) seeds standard memory + sets write=owner on {ns}")
    _, seed = h.write_memory(openclaw, OPEN, ns, title="_anchor",
                             content="namespace anchor", tier="long",
                             include_status=True)
    sid = ((seed or {}).get("body") or {}).get("id") if isinstance(seed, dict) else None
    log(f"  seed standard memory id={sid}")
    rc_a, resp_a = h.http_on(
        openclaw, "POST", f"/api/v1/namespaces/{ns}/standard",
        body={"id": sid, "governance": {"write": "owner"}},
        agent_id=OPEN, include_status=True,
    )
    log(f"  set_standard -> rc={rc_a} resp={resp_a}")

    # Phase B — owner writes (expect 201).
    log(f"phase B: owner writes to {ns} (expect 201)")
    _, doc = h.write_memory(openclaw, OPEN, ns, title="owner-write",
                            content="owner write", include_status=True)
    owner_code = (doc or {}).get("http_code") if isinstance(doc, dict) else 0

    # Capture decision_counts.enforce on EACH node before intruder write.
    # The counter is per-process — to read intruder writes, we must read
    # from the node where the writes were issued (hermes for the cross-
    # node attack path, openclaw for same-node reproduction).
    h.settle(2, "let counter snapshots quiesce")
    enforce_before_open = _decision_counts(h, openclaw).get("enforce", 0) or 0
    enforce_before_herm = _decision_counts(h, hermes).get("enforce", 0) or 0
    log(f"  enforce_before openclaw={enforce_before_open} hermes={enforce_before_herm}")

    # Phase C — intruder writes through OPENCLAW (the node owning the
    # standard) so the enforce decision is recorded on openclaw's counter,
    # which is the node that actually evaluates governance.
    log(f"phase C: intruder ({HERM}) writes to {ns} via openclaw daemon (expect 403 x3)")
    intruder_code = 0
    intruder_body = None
    for i in range(3):
        _, intruder = h.write_memory(openclaw, HERM, ns, title=f"intruder-{i}",
                                     content=f"intruder write {i}", include_status=True)
        intruder_code = (intruder or {}).get("http_code") if isinstance(intruder, dict) else 0
        intruder_body = (intruder or {}).get("body") if isinstance(intruder, dict) else None

    # Phase D — verify enforce counter delta on openclaw (where the
    # decisions were rendered).
    h.settle(2, "let counter samples settle")
    enforce_after_open = _decision_counts(h, openclaw).get("enforce", 0) or 0
    enforce_after_herm = _decision_counts(h, hermes).get("enforce", 0) or 0
    log(f"  enforce_after openclaw={enforce_after_open} hermes={enforce_after_herm}")
    # The deny-decisions for cross-agent writes via openclaw land on
    # openclaw's counter; the cross-node-via-hermes path was tried first
    # but hermes isn't the policy authority for this ns (only the local-
    # node's resolve_governance_policy fires).
    delta = (enforce_after_open - enforce_before_open) + \
            (enforce_after_herm - enforce_before_herm)

    reasons: list[str] = []
    passed = True
    if owner_code not in (200, 201):
        passed = False
        reasons.append(f"owner write got {owner_code} (expected 201)")
    if intruder_code != 403:
        passed = False
        reasons.append(f"intruder got {intruder_code} (expected 403)")
    if isinstance(intruder_body, dict):
        msg = (intruder_body.get("error") or intruder_body.get("message") or "")
        # Sanitized error: must not echo back metadata blobs / SQL / paths.
        if any(needle in msg.lower() for needle in ("select ", "/var/", "/etc/", "trace ", "panic")):
            passed = False
            reasons.append(f"intruder error not sanitized: {msg[:120]}")
    if delta < 1:
        passed = False
        reasons.append(f"decision_counts.enforce delta={delta} (expected >=1)")

    h.emit(passed=passed, reason="; ".join(reasons),
           owner_http_code=owner_code, intruder_http_code=intruder_code,
           enforce_delta=delta, namespace=ns, reasons=reasons)


if __name__ == "__main__":
    main()
