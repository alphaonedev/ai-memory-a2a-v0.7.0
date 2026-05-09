#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 80 — Governance owner-only inheritance on postgres backend (Wave 4).

Validates the F1 fix on the postgres path: when a parent namespace's
standard sets `governance.write=owner` + `inherit=true`, a deep grandchild
write must walk the inheritance chain leaf-first and resolve the parent
owner correctly. This is the same oracle as S60, but must hold on a
postgres-backed daemon for Wave 4 to be GREEN.

PASS criteria mirror S60:
  - openclaw (owner) writes to /sub/deep => 201
  - hermes (non-owner, inherit=true)     => 403
  - hermes writes to unrelated ns        => 201

Self-skips when A2A_BACKEND_KIND=sqlite.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "80"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    h.skip_if_backend_sqlite(
        "S80 validates F1 inheritance fix on postgres backend; "
        "A2A_BACKEND_KIND=sqlite duplicates S60 — skip."
    )

    suffix = new_uuid()[:6]
    OPEN = f"ai:s80-openclaw-{suffix}"
    HERM = f"ai:s80-hermes-{suffix}"
    parent = f"s80-parent-{suffix}"
    child = f"{parent}/sub/deep"
    unrelated = f"s80-other-{suffix}"

    log(f"phase A: openclaw seeds standard memory + sets inherit=true,write=owner on {parent}")
    _, seed = h.write_memory(
        h.node1_ip, OPEN, parent, title="_anchor",
        content="parent anchor", tier="long",
        include_status=True,
    )
    sid = ((seed or {}).get("body") or {}).get("id") if isinstance(seed, dict) else None
    log(f"  parent anchor id={sid}")
    rc_p, resp_p = h.http_on(
        h.node1_ip, "POST", f"/api/v1/namespaces/{parent}/standard",
        body={"id": sid, "governance": {"write": "owner", "inherit": True}},
        agent_id=OPEN, include_status=True,
    )
    log(f"  set_standard rc={rc_p} resp={resp_p}")
    h.settle(3, "policy propagate (postgres backend)")

    log(f"phase B: openclaw writes to deep child {child} (expect 201)")
    _, owner_doc = h.write_memory(
        h.node1_ip, OPEN, child, title="owner-deep",
        content="owner deep", include_status=True,
    )
    owner_code = (owner_doc or {}).get("http_code") if isinstance(owner_doc, dict) else 0

    log(f"phase C: hermes writes to deep child {child} (expect 403)")
    _, herm_doc = h.write_memory(
        h.node2_ip, HERM, child, title="herm-deep",
        content="herm deep", include_status=True,
    )
    herm_code = (herm_doc or {}).get("http_code") if isinstance(herm_doc, dict) else 0

    log(f"phase D: hermes writes to unrelated namespace {unrelated} (expect 201)")
    _, herm2 = h.write_memory(
        h.node2_ip, HERM, unrelated, title="herm-other",
        content="herm in unrelated ns", include_status=True,
    )
    herm2_code = (herm2 or {}).get("http_code") if isinstance(herm2, dict) else 0

    reasons: list[str] = []
    passed = True
    if owner_code not in (200, 201):
        passed = False
        reasons.append(f"owner write to deep child got {owner_code} (expected 201)")
    if herm_code != 403:
        passed = False
        reasons.append(f"hermes write to deep child got {herm_code} (expected 403)")
    if herm2_code not in (200, 201):
        passed = False
        reasons.append(f"hermes write to unrelated got {herm2_code} (expected 201)")

    h.emit(
        passed=passed,
        reason="; ".join(reasons),
        per_agent={
            "openclaw_owner_deep": owner_code,
            "hermes_inherit_true": herm_code,
            "hermes_inherit_false": herm2_code,
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
