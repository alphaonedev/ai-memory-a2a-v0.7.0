#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 81 — Heterogeneous federation: openclaw=sqlite, hermes=postgres (Wave 4).

Validates that the A2A federation contract (sync_push, peer-list,
quorum-write convergence) is backend-agnostic by deliberately running
openclaw on sqlite and hermes on postgres (or the inverse). Both
directions are exercised:
  Phase A: openclaw (sqlite) writes 5 memories; hermes (postgres) sees
           them via federation fanout within settle window.
  Phase B: hermes (postgres) writes 5 memories; openclaw (sqlite) sees
           them via federation fanout.
  Phase C: capabilities probe — both daemons expose
           protocol_version (or version) AND the storage_backend
           field reports their respective backends.

This scenario is the canonical run mode for A2A_BACKEND_KIND=mixed and
also runs cleanly when BACKEND_KIND=postgres (the "mixed" assertion is
softened — both nodes are expected to be postgres in that case).

Self-skips when A2A_BACKEND_KIND=sqlite.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "81"
SEED_PER_SIDE = 5
SETTLE_S = 8


def _seed_and_collect_ids(h: Harness, node_ip: str, agent: str, ns: str,
                          n: int, prefix: str) -> list[str]:
    ids: list[str] = []
    for i in range(n):
        rc, doc = h.write_memory(
            node_ip, agent, ns,
            title=f"{prefix}-{i}-{new_uuid()[:6]}",
            content=f"{prefix} content {i}",
            include_status=True,
        )
        if isinstance(doc, dict):
            mid = (doc.get("body") or {}).get("id")
            if mid:
                ids.append(mid)
    return ids


def _count_visible(h: Harness, node_ip: str, ns: str, expected_ids: list[str]) -> int:
    """Count how many of `expected_ids` are visible in `ns` on `node_ip`."""
    rc, resp = h.list_memories(node_ip, ns, limit=100)
    if rc != 0 or not isinstance(resp, dict):
        return 0
    visible_ids = {m.get("id") for m in (resp.get("memories") or []) if m.get("id")}
    return sum(1 for mid in expected_ids if mid in visible_ids)


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    h.skip_if_backend_sqlite(
        "S81 exercises heterogeneous federation; not meaningful on "
        "uniform sqlite topology."
    )

    openclaw, hermes = h.node1_ip, h.node2_ip
    open_kind = h.node_backend(openclaw)
    herm_kind = h.node_backend(hermes)
    log(f"  topology: openclaw={open_kind} hermes={herm_kind} (campaign-kind={h.backend_kind()})")

    suffix = new_uuid()[:6]
    OPEN = f"ai:s81-openclaw-{suffix}"
    HERM = f"ai:s81-hermes-{suffix}"
    ns_open = f"s81-from-openclaw-{suffix}"
    ns_herm = f"s81-from-hermes-{suffix}"

    log(f"phase A: openclaw ({open_kind}) seeds {SEED_PER_SIDE} → hermes ({herm_kind}) converge")
    ids_open = _seed_and_collect_ids(h, openclaw, OPEN, ns_open, SEED_PER_SIDE, "open")
    h.settle(SETTLE_S, "federation fanout openclaw->hermes")
    seen_on_hermes = _count_visible(h, hermes, ns_open, ids_open)
    log(f"  hermes saw {seen_on_hermes}/{len(ids_open)} of openclaw's writes")

    log(f"phase B: hermes ({herm_kind}) seeds {SEED_PER_SIDE} → openclaw ({open_kind}) converge")
    ids_herm = _seed_and_collect_ids(h, hermes, HERM, ns_herm, SEED_PER_SIDE, "herm")
    h.settle(SETTLE_S, "federation fanout hermes->openclaw")
    seen_on_openclaw = _count_visible(h, openclaw, ns_herm, ids_herm)
    log(f"  openclaw saw {seen_on_openclaw}/{len(ids_herm)} of hermes's writes")

    log("phase C: per-node capabilities + storage_backend probe")
    open_label = h.daemon_storage_label(openclaw)
    herm_label = h.daemon_storage_label(hermes)
    log(f"  openclaw storage_backend={open_label!r}")
    log(f"  hermes   storage_backend={herm_label!r}")

    def _label_matches(actual: str, expected: str) -> bool:
        a = (actual or "").lower()
        if expected == "postgres":
            return any(k in a for k in ("postgres", "postgresql", "pg-sal", "pg_sal", "pg"))
        return any(k in a for k in ("sqlite", "sqlcipher"))

    reasons: list[str] = []
    passed = True
    if len(ids_open) < SEED_PER_SIDE:
        passed = False
        reasons.append(f"openclaw seeded {len(ids_open)}/{SEED_PER_SIDE}")
    if len(ids_herm) < SEED_PER_SIDE:
        passed = False
        reasons.append(f"hermes seeded {len(ids_herm)}/{SEED_PER_SIDE}")
    if seen_on_hermes < SEED_PER_SIDE:
        passed = False
        reasons.append(
            f"federation openclaw→hermes converged {seen_on_hermes}/{SEED_PER_SIDE}"
        )
    if seen_on_openclaw < SEED_PER_SIDE:
        passed = False
        reasons.append(
            f"federation hermes→openclaw converged {seen_on_openclaw}/{SEED_PER_SIDE}"
        )
    # Capability label checks — soft when label is empty (older daemon
    # builds may not surface storage_backend yet); hard when label is
    # non-empty AND mismatched.
    if open_label and not _label_matches(open_label, open_kind):
        passed = False
        reasons.append(
            f"openclaw storage_backend={open_label!r} does not match expected {open_kind!r}"
        )
    if herm_label and not _label_matches(herm_label, herm_kind):
        passed = False
        reasons.append(
            f"hermes storage_backend={herm_label!r} does not match expected {herm_kind!r}"
        )

    h.emit(
        passed=passed,
        reason="; ".join(reasons),
        topology={"openclaw": open_kind, "hermes": herm_kind},
        per_agent={
            "openclaw": {
                "seeded": len(ids_open),
                "saw_peer_writes": seen_on_openclaw,
                "storage_backend": open_label,
            },
            "hermes": {
                "seeded": len(ids_herm),
                "saw_peer_writes": seen_on_hermes,
                "storage_backend": herm_label,
            },
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
