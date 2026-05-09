#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 71 — AGE Cypher path ≡ recursive-CTE fallback path.

The postgres SAL adapter uses Apache AGE when loaded, falls back to a
recursive CTE otherwise. This asserts the two backends return identical
results for kg_query, kg_timeline, kg_invalidate, find_paths.

Phases:
  A. seed 10 entities + 20 links (depth-5 chain + cycle) in aimemory_kgtest.
  B. capture all 4 KG-op fingerprints with AGE on.
  C. DROP EXTENSION age CASCADE; capture fingerprints from CTE fallback.
  D. CREATE EXTENSION age (restore).
PASS iff: AGE-on fingerprint == AGE-off fingerprint for every op.

DESTRUCTIVE on the AGE extension state — uses disposable aimemory_kgtest.
"""
import sys, pathlib, shlex, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "71"


def _psql_json(h: Harness, pg_url: str, sql: str) -> object:
    """Run `sql` returning JSON via psql -tA. The SQL must `SELECT
    json_agg(t) FROM (...)` form; we parse stdout as JSON."""
    cmd = f"psql {shlex.quote(pg_url)} -tA -c {shlex.quote(sql)}"
    r = h.ssh_exec(h.node1_ip, cmd, timeout=60)
    raw = (r.stdout or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _set_age(h: Harness, pg_url: str, on: bool) -> int:
    """Toggle the AGE extension. Returns psql rc."""
    sql = "CREATE EXTENSION IF NOT EXISTS age" if on else "DROP EXTENSION IF EXISTS age CASCADE"
    cmd = f"psql {shlex.quote(pg_url)} -c {shlex.quote(sql)}"
    r = h.ssh_exec(h.node1_ip, cmd, timeout=30)
    log(f"  AGE {'on' if on else 'off'} -> rc={r.returncode}")
    return r.returncode


def _capture_kg_ops(h: Harness, pg_url: str, src_id: str) -> dict:
    """Run the 4 KG ops via the SAL adapter and return a stable
    fingerprint of each (sorted ids + depths + counts)."""
    out: dict[str, object] = {}

    # 1. kg_query — return the set of (target_id, relation) for src_id.
    q1 = (
        "SELECT json_agg(row_to_json(t) ORDER BY t.target_id, t.relation) FROM ("
        f"  SELECT target_id, relation FROM kg_query_view WHERE source_id = '{src_id}'"
        ") t"
    )
    out["kg_query"] = _psql_json(h, pg_url, q1)

    # 2. kg_timeline — ordered (event_ts, target_id) for src_id.
    q2 = (
        "SELECT json_agg(row_to_json(t) ORDER BY t.event_ts, t.target_id) FROM ("
        f"  SELECT event_ts, target_id, relation FROM kg_timeline_view WHERE source_id = '{src_id}'"
        ") t"
    )
    out["kg_timeline"] = _psql_json(h, pg_url, q2)

    # 3. kg_invalidate — count of edges that would be invalidated; the
    #    physical invalidation is destructive so we read the dry-run view.
    q3 = (
        "SELECT count(*) FROM kg_query_view "
        f"WHERE source_id = '{src_id}' AND valid_until IS NULL"
    )
    out["kg_invalidate_count"] = _psql_json(h, pg_url, q3)

    # 4. find_paths — depths to every reachable node, max_depth=8.
    q4 = (
        "SELECT json_agg(row_to_json(t) ORDER BY t.depth, t.dst_id) FROM ("
        f"  SELECT depth, dst_id FROM kg_find_paths_view "
        f"  WHERE src_id = '{src_id}' AND depth <= 8"
        ") t"
    )
    out["find_paths"] = _psql_json(h, pg_url, q4)
    return out


def _seed_kg(h: Harness, pg_url: str) -> str:
    """Seed 10 entities + 20 links with a depth-5 chain + cycle.
    Returns the source-entity id used as the path-query root."""
    src_root = "s71-root"
    entity_inserts = ",".join(
        f"('s71-e{i}','s71-e{i}','entity')" for i in range(10)
    )
    # Chain s71-e0 → s71-e1 → ... → s71-e5 (depth 5)
    chain_links = [(f"s71-e{i}", f"s71-e{i+1}", "next") for i in range(5)]
    # Cycle s71-e5 → s71-e2
    cycle_links = [("s71-e5", "s71-e2", "back")]
    # 14 more cross-links across the rest.
    extra_links = [
        ("s71-e6", "s71-e7", "ref"), ("s71-e7", "s71-e8", "ref"),
        ("s71-e8", "s71-e9", "ref"), ("s71-e9", "s71-e0", "ref"),
        ("s71-e0", "s71-e6", "side"), ("s71-e1", "s71-e7", "side"),
        ("s71-e2", "s71-e8", "side"), ("s71-e3", "s71-e9", "side"),
        ("s71-e4", "s71-e6", "side"), ("s71-e5", "s71-e7", "side"),
        ("s71-e0", "s71-e2", "fast"), ("s71-e1", "s71-e3", "fast"),
        ("s71-e2", "s71-e4", "fast"), ("s71-e3", "s71-e5", "fast"),
    ]
    all_links = chain_links + cycle_links + extra_links
    link_inserts = ",".join(
        f"('s71-l{i}','{s}','{d}','{r}',NOW())"
        for i, (s, d, r) in enumerate(all_links)
    )
    sql = (
        "BEGIN; "
        f"INSERT INTO entities(id, name, kind) VALUES {entity_inserts} "
        "ON CONFLICT (id) DO NOTHING; "
        f"INSERT INTO kg_edges(id, source_id, target_id, relation, valid_from) "
        f"VALUES {link_inserts} ON CONFLICT (id) DO NOTHING; "
        "COMMIT;"
    )
    cmd = f"psql {shlex.quote(pg_url)} -c {shlex.quote(sql)}"
    h.ssh_exec(h.node1_ip, cmd, timeout=30)
    return "s71-e0"  # query root for find_paths / kg_query


def _fingerprint_match(a: object, b: object) -> bool:
    """Stable comparison: if either is dict/list, compare canonical JSON."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    try:
        admin_url = h.postgres_url(db="postgres")
        pg_url = h.postgres_url(db="aimemory_kgtest")
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    # F6 RCA (2026-05-09 R2): AGE/CTE equivalence requires the SAL
    # adapter to route reads through `kg_query_cypher` (AGE branch) vs
    # `kg_query_cte` (CTE branch). That dispatcher lives inside
    # `PostgresStore` (src/store/postgres.rs:411) and is reachable only
    # from the running daemon — but v0.7.0 daemon refuses
    # `--store-url postgres://...` (deferred to v0.7.1). The campaign
    # CANNOT exercise the AGE Cypher path via direct psql because:
    #   1. v0.7.0 does not ship an `ai-memory schema-init` CLI.
    #   2. The `memory_graph` AGE projection is created lazily by
    #      `kg_query_cypher`'s LOAD/SET path and depends on per-session
    #      state.
    #   3. The campaign-authored `kg_query_view` / `kg_timeline_view` /
    #      `kg_find_paths_view` SQL views are NOT part of
    #      postgres_schema.sql.
    # The cargo test `tests/age_cte_equivalence.rs` already covers the
    # equivalence assertion against a live postgres URL with AGE
    # installed; that's where the verification belongs in v0.7.0.
    # Re-enable as a campaign scenario in v0.7.1 when daemon
    # `--store-url postgres://` lands.
    h.skip(
        "AGE/CTE equivalence requires SAL routing through PostgresStore "
        "(src/store/postgres.rs::kg_query → kg_query_cypher | kg_query_cte) "
        "which is reachable only from a daemon running --store-url postgres://, "
        "deferred to v0.7.1. The cargo test tests/age_cte_equivalence.rs "
        "already covers this assertion against a live AGE URL. F6 finding."
    )
    return

    src_root = _seed_kg(h, pg_url)
    log(f"  seeded; root={src_root}")

    log("phase B: capture KG ops with AGE on")
    age_on = _capture_kg_ops(h, pg_url, src_root)
    log(f"  age_on keys present: {[k for k, v in age_on.items() if v is not None]}")

    log("phase C: DROP EXTENSION age CASCADE → CTE fallback")
    _set_age(h, pg_url, on=False)
    age_off = _capture_kg_ops(h, pg_url, src_root)
    log(f"  age_off keys present: {[k for k, v in age_off.items() if v is not None]}")

    log("phase D: restore AGE")
    _set_age(h, pg_url, on=True)

    reasons: list[str] = []
    passed = True
    for op in ("kg_query", "kg_timeline", "kg_invalidate_count", "find_paths"):
        if not _fingerprint_match(age_on.get(op), age_off.get(op)):
            passed = False
            reasons.append(
                f"{op}: AGE result != CTE result "
                f"(age_on={str(age_on.get(op))[:80]} | age_off={str(age_off.get(op))[:80]})"
            )

    h.emit(
        passed=passed, reason="; ".join(reasons),
        per_agent={"openclaw": {"age_on": age_on, "age_off": age_off}},
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
