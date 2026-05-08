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


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    openclaw, hermes = h.node1_ip, h.node2_ip
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    ns = f"scenario53-{new_uuid()[:8]}"

    # Phase A — owner sets governance.write=owner on ns/X.
    log(f"phase A: owner ({OPEN}) sets governance.write=owner on {ns}")
    body = {"namespace": ns, "policy": {"write": "owner"}}
    rc_a, resp_a = h.http_on(openclaw, "POST", "/api/v1/namespace/set_standard",
                             body=body, agent_id=OPEN, include_status=True)
    log(f"  set_standard -> rc={rc_a} resp={resp_a}")

    # Phase B — owner writes (expect 201).
    log(f"phase B: owner writes to {ns} (expect 201)")
    _, doc = h.write_memory(openclaw, OPEN, ns, title="owner-write",
                            content="owner write", include_status=True)
    owner_code = (doc or {}).get("http_code") if isinstance(doc, dict) else 0

    # Capture decision_counts before intruder write.
    _, dc_before = h.http_on(openclaw, "GET", "/api/v1/governance/decision_counts",
                             agent_id=OPEN)
    enforce_before = (dc_before or {}).get("enforce", 0) if isinstance(dc_before, dict) else 0

    # Phase C — intruder (hermes) writes; expect 403 + sanitized error.
    log(f"phase C: intruder ({HERM}) writes to {ns} (expect 403)")
    _, intruder = h.write_memory(hermes, HERM, ns, title="intruder",
                                 content="intruder write", include_status=True)
    intruder_code = (intruder or {}).get("http_code") if isinstance(intruder, dict) else 0
    intruder_body = (intruder or {}).get("body") if isinstance(intruder, dict) else None

    # Phase D — verify enforce counter delta.
    _, dc_after = h.http_on(openclaw, "GET", "/api/v1/governance/decision_counts",
                            agent_id=OPEN)
    enforce_after = (dc_after or {}).get("enforce", 0) if isinstance(dc_after, dict) else 0
    delta = enforce_after - enforce_before

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
