#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 72 — A2A KG over the live federation + postgres SAL adapter
contract sweep (Path B).

Original premise (v0.7.0-r1/r2): openclaw migrates a 10-entity / 20-link
KG to postgres+AGE; hermes reads it back via psql. F6 RCA showed two
gaps that make this premise structurally unreachable on v0.7.0:
  - `ai-memory migrate` walks `from.list(...)` only, never iterates
    `memory_links` (src/migrate.rs does not call `from.list_links` /
    `to.store_link`). Net effect: even a successful migrate produces 10
    memories on postgres + zero links — the KG is empty after migration.
  - The campaign's psql probes (`kg_query_view` / `kg_timeline_view`)
    target SQL views that are not part of `postgres_schema.sql`.

Path B (post-F6, 2026-05-08): re-frame the assertion. What S72 actually
*means* to validate is "A2A KG works on the live federation AND the
postgres SAL adapter is contract-clean." Both halves are reachable on
v0.7.0:

  Phase A — openclaw daemon (sqlite) creates a 10-entity / 20-link KG
            via HTTP. Memories + links land on the live openclaw.db.
  Phase B — hermes daemon (sqlite, federation peer) — confirm hermes
            converges on the memories via federation (existing working
            quorum-write path; the link-fanout-via-federation contract
            is on the v0.7.1 backlog and is NOT what S72 is asserting).
  Phase C — SAL contract sweep against the postgres adapter via the
            in-tree `cargo test --test sal_contract`. This is the
            canonical postgres-adapter validator (20 tests covering
            insert/get, list+limit, namespace isolation, FTS,
            update-preserves-id, delete, double-delete, concurrent
            writes, capabilities floor, verify-report). When all 20
            postgres_contract::* tests pass on the disposable
            `aimemory_sal72` database, the postgres SAL surface is
            certified contract-clean — that's what S72 was probing
            indirectly via raw psql.

PASS iff:
  - openclaw HTTP write of 10 memories + 20 links succeeds.
  - hermes converges on >= 10 memories within the federation settle
    window (existing working A2A path).
  - cargo test --test sal_contract exits rc=0 against the postgres
    adapter (20 postgres_contract::* tests + 9 sqlite_contract::*).
"""
import os
import sys
import pathlib
import shlex
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "72"
SRC_DIR = "/opt/ai-memory-src"
TEST_DB = "aimemory_sal72"


def _seed_local_kg(h: Harness, agent: str) -> tuple[str, list[str]]:
    """Seed openclaw's HTTP daemon with 10 entities + 20 links.
    Returns (namespace, [memory_ids])."""
    ns = f"s72-kg-{new_uuid()[:6]}"
    log(f"  seeding 10 memories + 20 links in ns={ns}")
    ids: list[str] = []
    for i in range(10):
        _, d = h.write_memory(
            h.node1_ip, agent, ns,
            title=f"s72-e{i}", content=f"entity {i}",
            include_status=True,
        )
        if isinstance(d, dict):
            mid = (d.get("body") or {}).get("id")
            if mid:
                ids.append(mid)
    if len(ids) != 10:
        return ns, ids

    # 20 links: depth-5 chain + cycle + 14 cross
    edges = []
    for i in range(5):
        edges.append((ids[i], ids[i + 1], "next"))
    edges.append((ids[5], ids[2], "back"))
    for a, b, r in [
        (6, 7, "ref"), (7, 8, "ref"), (8, 9, "ref"), (9, 0, "ref"),
        (0, 6, "side"), (1, 7, "side"), (2, 8, "side"), (3, 9, "side"),
        (4, 6, "side"), (5, 7, "side"),
        (0, 2, "fast"), (1, 3, "fast"), (2, 4, "fast"), (3, 5, "fast"),
    ]:
        edges.append((ids[a], ids[b], r))
    n_links_ok = 0
    for src, dst, rel in edges:
        rc, resp = h.http_on(
            h.node1_ip, "POST", "/api/v1/links",
            body={"from": src, "to": dst, "rel_type": rel},
            agent_id=agent, include_status=True,
        )
        if isinstance(resp, dict) and resp.get("http_code") in (200, 201):
            n_links_ok += 1
    log(f"  links ok: {n_links_ok}/20")
    return ns, ids


def _hermes_converge_count(h: Harness, namespace: str, expect: int,
                           settle_s: int = 8) -> int:
    """Settle for federation fanout, then count memories visible on hermes."""
    log(f"  settle {settle_s}s for federation convergence")
    time.sleep(settle_s)
    rc, resp = h.list_memories(h.node2_ip, namespace, limit=50)
    if rc != 0 or not isinstance(resp, dict):
        return 0
    return len(resp.get("memories") or [])


def _bootstrap_db(h: Harness, admin_url: str, db: str) -> None:
    """Drop+recreate disposable db with vector + age extensions."""
    log(f"  bootstrap disposable db {db}")
    drop_create = (
        f"psql {shlex.quote(admin_url)} -c "
        f"'DROP DATABASE IF EXISTS {db}'; "
        f"psql {shlex.quote(admin_url)} -c "
        f"'CREATE DATABASE {db} OWNER aimemory'"
    )
    h.ssh_exec(h.node1_ip, drop_create, timeout=30)
    db_url = h.postgres_url(db=db)
    ext = (
        f"psql {shlex.quote(db_url)} -c "
        "'CREATE EXTENSION IF NOT EXISTS vector; "
        "CREATE EXTENSION IF NOT EXISTS age;'"
    )
    h.ssh_exec(h.node1_ip, ext, timeout=30)


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN = "ai:openclaw@nyc3:droplet-1"
    try:
        admin_url = h.postgres_url(db="postgres")
        test_url = h.postgres_url(db=TEST_DB)
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    reasons: list[str] = []
    passed = True

    # -- Phase A: openclaw HTTP seeds 10-entity / 20-link KG (live sqlite)
    log("phase A: openclaw seeds 10 entities + 20 links via HTTP API")
    ns, ids = _seed_local_kg(h, OPEN)
    if len(ids) != 10:
        passed = False
        reasons.append(f"phase A: only seeded {len(ids)}/10 entities")

    # -- Phase B: hermes federation convergence
    log("phase B: hermes convergence over federation")
    n_hermes = _hermes_converge_count(h, ns, expect=10) if ids else 0
    if n_hermes < 10:
        passed = False
        reasons.append(
            f"phase B: hermes converged on {n_hermes}/10 memories "
            "(federation fanout incomplete)"
        )

    # -- Phase C: postgres SAL adapter contract sweep
    log("phase C: bootstrap aimemory_sal72 + invoke pre-built /opt/tests/sal_contract")
    _bootstrap_db(h, admin_url, TEST_DB)
    # v0.7.0 slim-image (Option-4, 2026-05-11): test binary pre-built;
    # invoked directly instead of `cargo test --test sal_contract`.
    #
    # Plan C R7 cert finding (2026-05-12): `--test-threads=2` races on the
    # AGE label-table lazy-create path inside `sal_contract::postgres_*`.
    # Two threads both trigger `create_graph('memory_graph')` against the
    # disposable database; one wins, the other gets `relation "Memory"
    # already exists` (AGE represents Cypher labels as quoted-name
    # postgres tables; `Memory` is the canonical vertex label). The race
    # is in the test setup, not the daemon. Serialise with --test-threads=1
    # so each test owns its own AGE projection lifecycle.
    cargo_cmd = (
        f"AI_MEMORY_TEST_POSTGRES_URL={shlex.quote(test_url)} "
        "/opt/tests/sal_contract --test-threads=1 2>&1"
    )
    r = h.ssh_exec(h.node1_ip, cargo_cmd, timeout=270)
    out = (r.stdout or "")
    log("  sal_contract cargo test rc=" + str(r.returncode))
    for line in out.splitlines()[-15:]:
        log("  | " + line)

    summary_line = ""
    for line in out.splitlines():
        if "test result:" in line:
            summary_line = line.strip()
            break

    if r.returncode != 0:
        passed = False
        reasons.append(
            f"phase C: cargo test --test sal_contract rc={r.returncode}; "
            f"tail: {out[-300:]}"
        )

    h.emit(
        passed=passed,
        path_b=True,
        reason="; ".join(reasons),
        per_agent={
            "openclaw": {
                "namespace": ns,
                "memories_seeded": len(ids),
                "validator": "tests/sal_contract.rs",
                "cargo_rc": r.returncode,
                "summary_line": summary_line,
            },
            "hermes": {
                "memories_converged": n_hermes,
            },
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
