#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 43 — memory_capabilities v2 schema (v0.6.3).

v0.6.3 bumps the capabilities document to schema_version=2 and adds 5
new top-level blocks alongside the v1 fields:

  v1 (preserved):  tier, version, features, models
  v2 (new):        permissions, hooks, compaction, approval, transcripts

Every peer in the mesh must report schema_version=2, all 5 v2 blocks,
AND keep all 4 v1 fields. Drift on any peer means a node was missed
during the v0.6.3 rollout — agents talking to that peer will see a
v1-only surface and silently miss governance / hooks / compaction.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log


SCENARIO_ID = "43"

V1_FIELDS = ("tier", "version", "features", "models")
V2_BLOCKS = ("permissions", "hooks", "compaction", "approval", "transcripts")


def _capabilities(h: Harness, ip: str) -> dict | None:
    # Capabilities surface lives at /api/v1/capabilities in v0.6.3+.
    # Scenario 30 tries multiple paths for backward compat; v2 schema is
    # only emitted on the canonical path so we probe it directly here.
    for path in ("/api/v1/capabilities", "/api/v1/capability"):
        _, doc = h.http_on(ip, "GET", path, include_status=True)
        if isinstance(doc, dict) and doc.get("http_code") == 200:
            body = doc.get("body")
            if isinstance(body, dict):
                body["_path"] = path
                return body
    return None


def _grade(cap: dict | None) -> dict:
    """Score a single peer's capabilities doc against v2 expectations."""
    if not isinstance(cap, dict):
        return {
            "responded": False,
            "schema_version": None,
            "missing_v1": list(V1_FIELDS),
            "missing_v2": list(V2_BLOCKS),
        }
    sv = cap.get("schema_version")
    # Accept either the string "2" or the int 2 (json shape varies).
    sv_norm = str(sv) if sv is not None else None
    missing_v1 = [k for k in V1_FIELDS if k not in cap]
    missing_v2 = [k for k in V2_BLOCKS if k not in cap]
    return {
        "responded": True,
        "schema_version": sv_norm,
        "missing_v1": missing_v1,
        "missing_v2": missing_v2,
    }


def main() -> None:
    h = Harness.from_env(SCENARIO_ID, require_node4=True)
    peers = [("node-1", h.node1_ip), ("node-2", h.node2_ip),
             ("node-3", h.node3_ip), ("node-4", h.node4_ip)]

    grades: dict[str, dict] = {}
    for name, ip in peers:
        cap = _capabilities(h, ip)
        g = _grade(cap)
        grades[name] = g
        log(f"  {name} schema_version={g['schema_version']!r} "
            f"missing_v1={g['missing_v1']} missing_v2={g['missing_v2']}")

    reasons: list[str] = []
    passed = True
    for name, g in grades.items():
        if not g["responded"]:
            passed = False
            reasons.append(f"{name} did not return a capabilities document")
            continue
        if g["schema_version"] != "2":
            passed = False
            reasons.append(f"{name} schema_version={g['schema_version']!r} (expected \"2\")")
        if g["missing_v2"]:
            passed = False
            reasons.append(f"{name} missing v2 blocks: {g['missing_v2']}")
        if g["missing_v1"]:
            passed = False
            reasons.append(f"{name} missing v1 fields: {g['missing_v1']}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        v1_fields=list(V1_FIELDS),
        v2_blocks=list(V2_BLOCKS),
        per_peer={n.replace("-", "_"): g for n, g in grades.items()},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
