#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 72 — A2A: openclaw migrates KG to postgres+AGE; hermes reads it back.

In v0.7.0-alpha, hermes can't run `ai-memory serve --store-url
postgres://...` (deferred to v0.7.1), but it CAN access the postgres SAL
adapter via direct psql calls against the AGE-backed db. This scenario
verifies that two agents on different droplets observe the same KG truth
through the postgres+AGE substrate.

Phases:
  A. openclaw populates a 10-entity / 20-link KG on its local SQLite.
  B. openclaw runs `ai-memory migrate --to postgres://...` (forward only).
  C. hermes — over the VPC, against postgres-node directly — runs the
     three KG queries via psql + AGE Cypher and captures fingerprints.
  D. ground-truth fingerprints captured directly against the same
     postgres connection from openclaw.

PASS iff: hermes' fingerprints == openclaw's ground-truth fingerprints
for kg_query, kg_timeline, find_paths on the same root entity.
"""
import sys, pathlib, shlex, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "72"


def _seed_local_kg(h: Harness, sqlite_path: str, agent: str) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Build 10 entities + 20 links into openclaw's local sqlite via the
    HTTP API + /api/v1/links surface (same code path S65 uses)."""
    ns = f"s72-kg-{new_uuid()[:6]}"
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
    edges: list[tuple[str, str, str]] = []
    if len(ids) == 10:
        for i in range(5):  # depth-5 chain
            edges.append((ids[i], ids[i+1], "next"))
        edges.append((ids[5], ids[2], "back"))  # cycle
        # 14 cross-links
        cross = [
            (6, 7, "ref"), (7, 8, "ref"), (8, 9, "ref"), (9, 0, "ref"),
            (0, 6, "side"), (1, 7, "side"), (2, 8, "side"), (3, 9, "side"),
            (4, 6, "side"), (5, 7, "side"),
            (0, 2, "fast"), (1, 3, "fast"), (2, 4, "fast"), (3, 5, "fast"),
        ]
        for a, b, r in cross:
            edges.append((ids[a], ids[b], r))
        for s, d, r in edges:
            h.http_on(h.node1_ip, "POST", "/api/v1/links",
                      body={"from": s, "to": d, "rel_type": r},
                      agent_id=agent, include_status=True)
    return ids, edges


def _kg_fingerprint(h: Harness, ssh_node_ip: str, pg_url: str, root_id: str) -> dict:
    """Run kg_query / kg_timeline / find_paths from the given droplet's
    psql against the shared postgres. Each result reduced to a sorted-id
    fingerprint stable across AGE vs CTE backends."""
    out: dict[str, object] = {}
    queries = {
        "kg_query": (
            "SELECT json_agg(target_id ORDER BY target_id) FROM kg_query_view "
            f"WHERE source_id = '{root_id}'"
        ),
        "kg_timeline": (
            "SELECT json_agg(target_id ORDER BY event_ts, target_id) "
            f"FROM kg_timeline_view WHERE source_id = '{root_id}'"
        ),
        "find_paths": (
            "SELECT json_agg(json_build_object('depth',depth,'dst',dst_id) "
            f"ORDER BY depth, dst_id) FROM kg_find_paths_view "
            f"WHERE src_id = '{root_id}' AND depth <= 8"
        ),
    }
    for op, sql in queries.items():
        cmd = f"psql {shlex.quote(pg_url)} -tA -c {shlex.quote(sql)}"
        r = h.ssh_exec(ssh_node_ip, cmd, timeout=45)
        raw = (r.stdout or "").strip()
        try:
            out[op] = json.loads(raw) if raw else None
        except ValueError:
            out[op] = raw
    return out


def _fp_match(a: object, b: object) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN = "ai:openclaw@nyc3:droplet-1"
    try:
        admin_url = h.postgres_url(db="postgres")
        pg_url = h.postgres_url(db="aimemory_s72")
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    # v0.7.0-alpha pg adapter requires pgvector — not installed on the
    # campaign's postgres-node. Skip cleanly until operator installs it.
    import shlex as _shlex
    r = h.ssh_exec(h.node1_ip, (
        f"psql {_shlex.quote(admin_url)} -tAc "
        "\"SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'\""
    ), timeout=20)
    if "1" not in (r.stdout or "").strip():
        h.skip(
            "v0.7.0-alpha pg adapter requires pgvector; postgres-node has "
            "only `age`. Re-run after pgvector is installed + enabled."
        )
        return

    log("phase A: openclaw seeds 10-entity KG on its local sqlite")
    seed_db = f"/tmp/s72-seed-{new_uuid()[:6]}.sqlite"
    h.ssh_exec(h.node1_ip, f"rm -f {seed_db}", timeout=10)
    ids, edges = _seed_local_kg(h, seed_db, OPEN)
    if len(ids) != 10:
        h.emit(passed=False,
               reason=f"only seeded {len(ids)}/10 entities",
               reasons=["seed failed"])
        return
    root_id = ids[0]

    log("phase B: openclaw migrates → postgres aimemory_s72")
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(admin_url)} -c 'DROP DATABASE IF EXISTS aimemory_s72'"
    ), timeout=20)
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(admin_url)} -c 'CREATE DATABASE aimemory_s72 OWNER aimemory'"
    ), timeout=20)
    # Pull openclaw's running sqlite path from env, fall back to /var/lib.
    openclaw_db = "/var/lib/ai-memory/store.db"
    fwd_cmd = (
        f"ai-memory migrate "
        f"--from sqlite://{shlex.quote(openclaw_db)} "
        f"--to {shlex.quote(pg_url)} --json"
    )
    fwd = h.ssh_exec(h.node1_ip, fwd_cmd, timeout=300)
    log(f"  migrate stdout[:200]={(fwd.stdout or '')[:200]}")

    h.settle(4, "post-migration AGE settle")

    log("phase C: hermes reads KG fingerprint via psql against shared pg")
    hermes_fp = _kg_fingerprint(h, h.node2_ip, pg_url, root_id)
    log(f"  hermes ops: {list(hermes_fp.keys())}")

    log("phase D: openclaw reads ground-truth fingerprint from same pg")
    open_fp = _kg_fingerprint(h, h.node1_ip, pg_url, root_id)

    reasons: list[str] = []
    passed = True
    for op in ("kg_query", "kg_timeline", "find_paths"):
        if not _fp_match(open_fp.get(op), hermes_fp.get(op)):
            passed = False
            reasons.append(
                f"{op}: openclaw vs hermes mismatch "
                f"(open={str(open_fp.get(op))[:80]} | herm={str(hermes_fp.get(op))[:80]})"
            )
    # Also validate hermes saw a non-empty KG (catches a silent failure
    # where psql returns NULL but both sides tie at NULL).
    if not hermes_fp.get("kg_query"):
        passed = False
        reasons.append("hermes saw empty kg_query — migration may not have run")

    h.emit(
        passed=passed, reason="; ".join(reasons),
        per_agent={"openclaw": open_fp, "hermes": hermes_fp},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
