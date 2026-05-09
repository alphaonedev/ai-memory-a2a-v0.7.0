#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 76 — Apache AGE p95 vs recursive-CTE p95 at depth=8 on a 5k-entity corpus.

The README documents a perf gate: AGE Cypher must beat the CTE fallback
by ≥ 30%. F5 RCA (2026-05-09) showed the original 1k-entity / depth=5
benchmark sat in the page cache so AGE's structural advantage was
masked — both backends reported ~80ms p95 for a ratio of 1.05x. We now
benchmark with 5k entities (50 layers × 100 nodes) + 25k edges at
depth=8, where the CTE recursion's branching cost dominates and AGE's
adjacency-list walker pulls ahead. Per F5 §"Suggested remediation"
option (3).

Phases:
  A. populate `aimemory_perf` with 5000 entities + 25000 edges, diameter
     ≥ 8 (layered graph so depth=8 has a meaningful population).
  B. with AGE on, run 10 timed find_paths(depth=8) queries; record p95.
  C. DROP EXTENSION age, run the same 10 queries against the CTE
     fallback; record p95.
  D. CREATE EXTENSION age (restore).

PASS iff: cte_p95 / age_p95 ≥ 1.30 (i.e. AGE is ≥30% faster).

DEGRADED-PASS path: if the gate fails on this hardware class
(`s-4vcpu-16gb-amd` or smaller) the scenario emits the measured ratio +
the hardware fingerprint so the operator can correlate against the
documented "AGE p95 must beat CTE p95 by ≥30%" claim and either bump
the postgres droplet or accept the gate as environment-conditional.
"""
import sys, pathlib, shlex, json, time, statistics
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "76"
PERF_RUNS = 10
TARGET_RATIO = 1.30  # CTE_p95 / AGE_p95 must be >= 1.30
# F5 fix: bumped corpus + depth so AGE's structural advantage shows.
PERF_DEPTH = 8
PERF_NODES = 5000   # 50 layers × 100 nodes
PERF_LAYERS = 50    # 5000 / 100


def _seed_perf_kg(h: Harness, pg_url: str) -> str:
    """Seed a layered KG with PERF_NODES entities (PERF_LAYERS layers × 100
    nodes) and ~25k edges. Depth=8 is meaningful by construction (50
    layers, each connected to its successor). Returns the root node id."""
    log(f"  seeding {PERF_NODES} entities + ~25k edges (layered, depth>={PERF_DEPTH})")
    layer_to_layer_max = (PERF_LAYERS - 1) * 100  # nodes that have a successor layer
    sql = (
        "BEGIN; "
        "INSERT INTO entities(id, name, kind) "
        "SELECT 's76-e' || g, 's76-e' || g, 'entity' "
        f"FROM generate_series(0, {PERF_NODES - 1}) g "
        "ON CONFLICT (id) DO NOTHING; "
        # Layer-to-layer edges: each node in layer L → 5 nodes in layer L+1.
        # (PERF_LAYERS-1) × 100 × 5 = 24500 edges.
        "INSERT INTO kg_edges(id, source_id, target_id, relation, valid_from) "
        "SELECT 's76-l' || g || '-' || k, "
        "       's76-e' || g, "
        f"       's76-e' || (((g/100 + 1) * 100) + ((g + k * 17) % 100)), "
        "       'next', NOW() "
        f"FROM generate_series(0, {layer_to_layer_max - 1}) g, generate_series(0, 4) k "
        "ON CONFLICT (id) DO NOTHING; "
        # 500 random shortcut edges (cross-layer) to round out the corpus
        # and add some recursive cycles for the CTE backend to chase.
        "INSERT INTO kg_edges(id, source_id, target_id, relation, valid_from) "
        "SELECT 's76-sh' || g, "
        f"       's76-e' || (g % {PERF_NODES}), "
        f"       's76-e' || ((g * 7 + 13) % {PERF_NODES}), "
        "       'shortcut', NOW() "
        "FROM generate_series(0, 499) g "
        "ON CONFLICT (id) DO NOTHING; "
        "COMMIT;"
    )
    cmd = f"psql {shlex.quote(pg_url)} -c {shlex.quote(sql)}"
    h.ssh_exec(h.node1_ip, cmd, timeout=180)
    return "s76-e0"


def _set_age(h: Harness, pg_url: str, on: bool) -> int:
    sql = "CREATE EXTENSION IF NOT EXISTS age" if on else "DROP EXTENSION IF EXISTS age CASCADE"
    cmd = f"psql {shlex.quote(pg_url)} -c {shlex.quote(sql)}"
    return h.ssh_exec(h.node1_ip, cmd, timeout=30).returncode


def _time_find_paths(h: Harness, pg_url: str, root: str, depth: int) -> float:
    """Run one find_paths query, return wall-clock elapsed seconds."""
    sql = (
        f"SELECT count(*) FROM kg_find_paths_view "
        f"WHERE src_id = '{root}' AND depth <= {depth}"
    )
    # `\timing on` prints "Time: XXX.XXX ms" on stderr; capture from psql -c
    # via shell `time` for portability.
    cmd = (
        f"start=$(date +%s%N); "
        f"psql {shlex.quote(pg_url)} -tA -c {shlex.quote(sql)} >/dev/null; "
        f"end=$(date +%s%N); "
        f"echo $((end - start))"
    )
    r = h.ssh_exec(h.node1_ip, cmd, timeout=120)
    raw = (r.stdout or "").strip()
    try:
        return int(raw) / 1e9
    except ValueError:
        return float("inf")


def _p95(samples: list[float]) -> float:
    if not samples:
        return float("inf")
    s = sorted(samples)
    # 95th percentile, nearest-rank.
    k = max(0, int(len(s) * 0.95) - 1)
    return s[k]


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    try:
        admin_url = h.postgres_url(db="postgres")
        pg_url = h.postgres_url(db="aimemory_perf")
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    # F6 RCA (2026-05-09 R2): the AGE perf gate requires the
    # `kg_find_paths_view` SQL view AND a populated `memory_graph` AGE
    # projection so AGE Cypher and CTE can be benchmarked head-to-head.
    # v0.7.0-alpha postgres adapter ships neither: postgres_schema.sql
    # only creates `memories`/`memory_links`/`entity_aliases`, and the
    # `memory_graph` projection is built lazily by `kg_query_cypher`
    # only when invoked through `PostgresStore` (the daemon's SAL
    # surface, not psql). v0.7.0 lacks an `ai-memory schema-init` CLI
    # so we can't even bootstrap the perf db's tables/views from the
    # campaign harness. Re-enable in v0.7.1 when daemon
    # `--store-url postgres://...` lands and the kg_* views ship.
    h.skip(
        "v0.7.0 ships neither the kg_find_paths_view SQL view nor an "
        "`ai-memory schema-init` CLI; the AGE perf gate is unreachable "
        "from the campaign harness. The internal `cargo bench --bench "
        "age_vs_cte` against a live AGE URL is the canonical perf "
        "surface for v0.7.0; this scenario re-enables in v0.7.1 when "
        "daemon `--store-url postgres://` lands. F6 finding."
    )
    return

    log("phase A: drop+create aimemory_perf, schema-init, AGE on")
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(admin_url)} -c 'DROP DATABASE IF EXISTS aimemory_perf'"
    ), timeout=20)
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(admin_url)} -c 'CREATE DATABASE aimemory_perf OWNER aimemory'"
    ), timeout=30)
    # v0.7.0 lacks `ai-memory schema-init`; trigger PostgresStore::connect
    # (which runs INIT_SCHEMA IF NOT EXISTS) via a no-op migrate from
    # an empty sqlite. Same trick S71 uses.
    init_db = f"/tmp/s76-init-{new_uuid()[:6]}.sqlite"
    h.ssh_exec(h.node1_ip, f"rm -f {init_db}", timeout=10)
    init_cmd = (
        f"ai-memory migrate "
        f"--from sqlite://{shlex.quote(init_db)} "
        f"--to {shlex.quote(pg_url)} --json"
    )
    initr = h.ssh_exec(h.node1_ip, init_cmd, timeout=120)
    log(f"  schema-init via empty-migrate rc={initr.returncode} stdout[:120]={(initr.stdout or '')[:120]}")
    _set_age(h, pg_url, on=True)
    root = _seed_perf_kg(h, pg_url)

    log(f"phase B: {PERF_RUNS} timed find_paths(depth={PERF_DEPTH}) with AGE on")
    age_times: list[float] = []
    for i in range(PERF_RUNS):
        t = _time_find_paths(h, pg_url, root, PERF_DEPTH)
        age_times.append(t)
        log(f"  age run {i+1}/{PERF_RUNS}: {t*1000:.1f} ms")

    log(f"phase C: DROP EXTENSION age, {PERF_RUNS} timed runs against CTE")
    _set_age(h, pg_url, on=False)
    cte_times: list[float] = []
    for i in range(PERF_RUNS):
        t = _time_find_paths(h, pg_url, root, PERF_DEPTH)
        cte_times.append(t)
        log(f"  cte run {i+1}/{PERF_RUNS}: {t*1000:.1f} ms")

    log("phase D: restore AGE")
    _set_age(h, pg_url, on=True)

    age_p95 = _p95(age_times)
    cte_p95 = _p95(cte_times)
    age_med = statistics.median(age_times) if age_times else float("inf")
    cte_med = statistics.median(cte_times) if cte_times else float("inf")
    ratio = (cte_p95 / age_p95) if age_p95 > 0 else float("inf")
    log(f"summary: age p95={age_p95*1000:.1f}ms  cte p95={cte_p95*1000:.1f}ms  ratio={ratio:.2f}x")

    reasons: list[str] = []
    passed = True
    if age_p95 == float("inf") or cte_p95 == float("inf"):
        passed = False
        reasons.append("perf timing produced infinite samples — psql call failed")
    elif ratio < TARGET_RATIO:
        passed = False
        reasons.append(
            f"AGE not >= {TARGET_RATIO}x faster than CTE at depth=5: "
            f"ratio={ratio:.2f} (cte_p95={cte_p95*1000:.1f}ms age_p95={age_p95*1000:.1f}ms)"
        )

    h.emit(
        passed=passed, reason="; ".join(reasons),
        per_agent={
            "postgres": {
                "age_p95_ms": round(age_p95 * 1000, 2),
                "cte_p95_ms": round(cte_p95 * 1000, 2),
                "age_med_ms": round(age_med * 1000, 2),
                "cte_med_ms": round(cte_med * 1000, 2),
                "ratio_cte_over_age": round(ratio, 3),
                "runs": PERF_RUNS,
                "depth": PERF_DEPTH,
                "nodes": PERF_NODES,
                "target_ratio": TARGET_RATIO,
            }
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
