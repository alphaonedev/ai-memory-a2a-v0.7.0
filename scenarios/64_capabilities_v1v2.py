#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 64 — capabilities v1/v2 heterogeneous.

openclaw queries accept=v1, hermes queries accept=v2; both agree on
schema_version and 8-family roster.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log

SCENARIO_ID = "64"
EIGHT_FAMILIES = {"core", "graph", "meta", "power", "subscription", "governance",
                  "audit", "kg"}


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)

    log("phase A: openclaw queries capabilities accept=v1")
    rc1, v1 = h.http_on(h.node1_ip, "GET", "/api/v1/capabilities?accept=v1")
    log("phase B: hermes queries capabilities accept=v2")
    rc2, v2 = h.http_on(h.node2_ip, "GET", "/api/v1/capabilities?accept=v2")

    fam1 = set((v1 or {}).get("families", [])) if isinstance(v1, dict) else set()
    fam2 = set((v2 or {}).get("families", [])) if isinstance(v2, dict) else set()
    sv1 = (v1 or {}).get("schema_version") if isinstance(v1, dict) else None
    sv2 = (v2 or {}).get("schema_version") if isinstance(v2, dict) else None

    reasons: list[str] = []
    passed = True
    if rc1 != 0 or rc2 != 0:
        passed = False; reasons.append(f"http rc1={rc1} rc2={rc2}")
    # 8-family roster on both — even v1 carries the same family list, just under
    # a smaller schema_version envelope.
    if fam1 != EIGHT_FAMILIES:
        passed = False; reasons.append(f"openclaw v1 families={sorted(fam1)} (expected 8)")
    if fam2 != EIGHT_FAMILIES:
        passed = False; reasons.append(f"hermes v2 families={sorted(fam2)} (expected 8)")
    if sv1 is None or sv2 is None:
        passed = False; reasons.append(f"schema_version missing v1={sv1} v2={sv2}")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw_v1": {"schema_version": sv1, "families": sorted(fam1)},
               "hermes_v2":   {"schema_version": sv2, "families": sorted(fam2)},
           }, reasons=reasons)


if __name__ == "__main__":
    main()
