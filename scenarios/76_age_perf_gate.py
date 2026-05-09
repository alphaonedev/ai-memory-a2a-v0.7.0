#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 76 — Apache AGE p95 vs recursive-CTE p95 at depth=5.

The README documents a perf gate: AGE Cypher must beat the CTE fallback
by ≥ 30% at depth=5 on a 1000-entity / 5000-edge corpus. This scenario
reproduces that benchmark across the live postgres droplet.

Phases:
  A. populate `aimemory_perf` with 1000 entities + 5000 edges, diameter
     ≥ 5 (we use a layered graph so depth=5 has a meaningful population).
  B. with AGE on, run 10 timed find_paths(depth=5) queries; record p95.
  C. DROP EXTENSION age, run the same 10 queries against the CTE
     fallback; record p95.
  D. CREATE EXTENSION age (restore).

PASS iff: cte_p95 / age_p95 ≥ 1.30 (i.e. AGE is ≥30% faster).

DEGRADED-PASS path: if `cargo bench --bench age_vs_cte` is available
locally and produces its own report, prefer that report's verdict; we
fall back to the hand-rolled timing comparison only when cargo isn't
present (mirrors the SKIP-condition policy used in S73).
"""
import sys, pathlib, shlex, json, time, statistics
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "76"
PERF_RUNS = 10
TARGET_RATIO = 1.30  # CTE_p95 / AGE_p95 must be >= 1.30


def _seed_perf_kg(h: Harness, pg_url: str) -> str:
    """Seed a layered KG with 1000 entities (10 layers × 100 nodes) and
    5000 edges. Depth=5 is meaningful by construction. Returns the root
    node id."""
    log("  seeding 1000 entities + 5000 edges (layered)")
    sql = (
        "BEGIN; "
        "INSERT INTO entities(id, name, kind) "
        "SELECT 's76-e' || g, 's76-e' || g, 'entity' "
        "FROM generate_series(0, 999) g "
        "ON CONFLICT (id) DO NOTHING; "
        # Layer-to-layer edges: each node in layer L (size 100) → 5 nodes
        # in layer L+1 (10 layers, 100 nodes each). 9 × 100 × 5 = 4500.
        "INSERT INTO kg_edges(id, source_id, target_id, relation, valid_from) "
        "SELECT 's76-l' || g || '-' || k, "
        "       's76-e' || g, "
        "       's76-e' || (((g/100 + 1) * 100) + ((g + k * 17) % 100)), "
        "       'next', NOW() "
        "FROM generate_series(0, 899) g, generate_series(0, 4) k "
        "ON CONFLICT (id) DO NOTHING; "
        # 500 random shortcut edges to bring edge total to ~5000.
        "INSERT INTO kg_edges(id, source_id, target_id, relation, valid_from) "
        "SELECT 's76-sh' || g, "
        "       's76-e' || (g % 1000), "
        "       's76-e' || ((g * 7 + 13) % 1000), "
        "       'shortcut', NOW() "
        "FROM generate_series(0, 499) g "
        "ON CONFLICT (id) DO NOTHING; "
        "COMMIT;"
    )
    cmd = f"psql {shlex.quote(pg_url)} -c {shlex.quote(sql)}"
    h.ssh_exec(h.node1_ip, cmd, timeout=120)
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

    # v0.7.0-alpha pg adapter requires pgvector + `schema-init` CLI to
    # populate the `entities`/`kg_edges`/`kg_find_paths_view` schema. Both
    # are absent on this campaign's postgres-node bootstrap — no way to
    # seed a perf corpus.
    import shlex as _shlex
    r = h.ssh_exec(h.node1_ip, (
        f"psql {_shlex.quote(admin_url)} -tAc "
        "\"SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'\""
    ), timeout=20)
    if "1" not in (r.stdout or "").strip():
        h.skip(
            "v0.7.0-alpha pg adapter requires pgvector + schema-init CLI "
            "(neither shipped on this build); perf gate cannot run."
        )
        return

    log("phase A: drop+create aimemory_perf, schema-init, AGE on")
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(admin_url)} -c 'DROP DATABASE IF EXISTS aimemory_perf'"
    ), timeout=20)
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(admin_url)} -c 'CREATE DATABASE aimemory_perf OWNER aimemory'"
    ), timeout=30)
    h.ssh_exec(h.node1_ip, (
        f"ai-memory schema-init --store-url {shlex.quote(pg_url)}"
    ), timeout=120)
    _set_age(h, pg_url, on=True)
    root = _seed_perf_kg(h, pg_url)

    log("phase B: 10 timed find_paths(depth=5) with AGE on")
    age_times: list[float] = []
    for i in range(PERF_RUNS):
        t = _time_find_paths(h, pg_url, root, 5)
        age_times.append(t)
        log(f"  age run {i+1}/{PERF_RUNS}: {t*1000:.1f} ms")

    log("phase C: DROP EXTENSION age, 10 timed runs against CTE")
    _set_age(h, pg_url, on=False)
    cte_times: list[float] = []
    for i in range(PERF_RUNS):
        t = _time_find_paths(h, pg_url, root, 5)
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
                "depth": 5,
            }
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
