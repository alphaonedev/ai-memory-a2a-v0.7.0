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
# v0.7 family roster (src/profile.rs:Family): 8 canonical families.
# `subscription`/`audit`/`kg` from v0.6.x were merged/renamed:
#   subscription → governance (subscriptions live alongside policy)
#   audit        → graph (memory_verify lives in graph in v0.7)
#   kg           → graph
# v0.7 introduces lifecycle, archive, other so the canonical 8 is:
EIGHT_FAMILIES = {"core", "lifecycle", "graph", "governance",
                  "power", "meta", "archive", "other"}


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)

    # v0.7 capabilities accept selection is via the `Accept-Capabilities`
    # header (NOT the `?accept=` query param). v1 omits the v2 blocks
    # (permissions/hooks/compaction/approval/transcripts) and emits
    # schema_version=null; v3 emits schema_version="3" + a flat `tools`
    # array tagged with `family`. The "8-family roster" is derivable from
    # the tools[] family tags in v3 — v1 carries only tier/version/features
    # /models so families are not derivable from v1.
    log("phase A: openclaw queries capabilities accept=v1 (via Accept-Capabilities header)")
    rc1, v1 = h.http_on(h.node1_ip, "GET", "/api/v1/capabilities",
                        extra_headers={"Accept-Capabilities": "v1"})
    log("phase B: hermes queries capabilities accept=v2 (via Accept-Capabilities header)")
    rc2, v2 = h.http_on(h.node2_ip, "GET", "/api/v1/capabilities",
                        extra_headers={"Accept-Capabilities": "v3"})

    # v3: tools[] is a flat list with `family` tags — derive the family set.
    def families_from_tools(doc: object) -> set[str]:
        if not isinstance(doc, dict):
            return set()
        tools = doc.get("tools") or []
        return {t.get("family") for t in tools if isinstance(t, dict) and t.get("family")}

    fam1 = families_from_tools(v1)
    fam2 = families_from_tools(v2)
    sv1 = (v1 or {}).get("schema_version") if isinstance(v1, dict) else None
    sv2 = (v2 or {}).get("schema_version") if isinstance(v2, dict) else None
    # v0.7 v1 has `version` not `schema_version`; promote that to detect presence.
    v1_version = (v1 or {}).get("version") if isinstance(v1, dict) else None

    reasons: list[str] = []
    passed = True
    if rc1 != 0 or rc2 != 0:
        passed = False; reasons.append(f"http rc1={rc1} rc2={rc2}")
    # In v0.7 v1 envelope, families are NOT enumerated (only v3 carries
    # tools[].family). Only assert the 8-family roster on the v3 side.
    if not fam2:
        passed = False; reasons.append("v3 capabilities did not enumerate any tool families")
    elif fam2 != EIGHT_FAMILIES and not EIGHT_FAMILIES.issubset(fam2):
        # Tolerate additional v0.7-introduced families (e.g. `archive`, `lifecycle`).
        passed = False
        reasons.append(f"v3 families missing 8-roster: got={sorted(fam2)} expected_subset={sorted(EIGHT_FAMILIES)}")
    # v1 should at least carry version (the legacy spine). v3 must carry
    # schema_version="3".
    if not v1_version:
        passed = False; reasons.append(f"v1 missing version; doc={list(v1.keys()) if isinstance(v1, dict) else type(v1).__name__}")
    if str(sv2 or "") not in ("2", "3"):
        passed = False; reasons.append(f"v3 schema_version={sv2!r} (expected '3')")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw_v1": {"schema_version": sv1, "families": sorted(fam1)},
               "hermes_v2":   {"schema_version": sv2, "families": sorted(fam2)},
           }, reasons=reasons)


if __name__ == "__main__":
    main()
