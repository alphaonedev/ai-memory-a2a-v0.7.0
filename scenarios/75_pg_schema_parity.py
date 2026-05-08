#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 75 — Postgres schema parity snapshot vs SQLite.

The postgres schema in v0.7.0-alpha is at version 15. The SQLite schema
is at version 28. The 13-version gap is INTENTIONAL for v0.7.0 (we ship
the SAL trait + AGE substrate first, schema parity catches up in v0.7.1+),
but the gap MUST be visible — this scenario fails if either side moves
silently.

This is a "sanity-snapshot" test, not a regression test in the usual
sense: its job is to ensure we don't ship v0.7.0 thinking postgres has
full parity. It also enumerates the missing-migration set so the
generated coverage report is precise.

Phases:
  A. read schema_version from postgres via psql.
  B. read schema_version from openclaw's local sqlite.
  C. compute the missing-migration set (sqlite_v - pg_v).
  D. enumerate which v0.7 features each missing migration enables —
     this list lives in MISSING_FEATURES below; if a future migration
     repurposes a version number, this dict needs an upstream PR.

PASS iff:
  pg_schema_version == EXPECTED_PG (15)
  sqlite_schema_version == EXPECTED_SQLITE (28)
  missing_migrations == {16,17,...,28}
"""
import sys, pathlib, shlex
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log

SCENARIO_ID = "75"
EXPECTED_PG = 15
EXPECTED_SQLITE = 28

# Documented capability gaps imposed by the postgres schema being 13
# versions behind. Each entry: migration_v -> short feature name.
MISSING_FEATURES: dict[int, str] = {
    16: "governance_inheritance",
    17: "webhook_subscriptions",
    18: "audit_log_chain",
    19: "transcripts",
    20: "signed_events",
    21: "agent_quotas",
    22: "link_attest_level",
    23: "a2a_correlation",
    24: "smart_load_veto_state",
    25: "kg_temporal_indexes_v2",
    26: "tier_promotion_metadata",
    27: "subscription_dlq",
    28: "consolidated_from_agents",
}


def _read_pg_schema_version(h: Harness, pg_url: str) -> int:
    cmd = (
        f"psql {shlex.quote(pg_url)} -tA -c "
        "'SELECT max(version) FROM schema_version'"
    )
    r = h.ssh_exec(h.node1_ip, cmd, timeout=20)
    raw = (r.stdout or "").strip()
    try:
        return int(raw)
    except ValueError:
        return -1


def _read_sqlite_schema_version(h: Harness, sqlite_path: str) -> int:
    cmd = (
        f"sqlite3 {shlex.quote(sqlite_path)} "
        "'SELECT max(version) FROM schema_version'"
    )
    r = h.ssh_exec(h.node1_ip, cmd, timeout=20)
    raw = (r.stdout or "").strip()
    try:
        return int(raw)
    except ValueError:
        return -1


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    try:
        pg_url = h.postgres_url()  # main aimemory db; schema_init is run by setup_node.sh
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    log("phase A: read pg schema_version")
    pg_v = _read_pg_schema_version(h, pg_url)
    log(f"  postgres schema_version = {pg_v}")

    log("phase B: read sqlite schema_version (openclaw local)")
    sqlite_path = "/var/lib/ai-memory/store.db"
    sqlite_v = _read_sqlite_schema_version(h, sqlite_path)
    log(f"  sqlite   schema_version = {sqlite_v}")

    log("phase C: compute missing-migration set")
    missing = set()
    if pg_v >= 0 and sqlite_v >= 0:
        missing = set(range(pg_v + 1, sqlite_v + 1))
    log(f"  missing migrations on pg: {sorted(missing)}")

    log("phase D: missing-feature roster")
    missing_features = {v: MISSING_FEATURES.get(v, "<unmapped>") for v in sorted(missing)}
    log(f"  features not exercisable on pg: {missing_features}")

    expected_missing = set(range(EXPECTED_PG + 1, EXPECTED_SQLITE + 1))
    reasons: list[str] = []
    passed = True
    if pg_v != EXPECTED_PG:
        passed = False
        reasons.append(f"pg schema_version={pg_v} (expected {EXPECTED_PG} for v0.7.0-alpha)")
    if sqlite_v != EXPECTED_SQLITE:
        passed = False
        reasons.append(f"sqlite schema_version={sqlite_v} (expected {EXPECTED_SQLITE} for v0.7.0)")
    if missing != expected_missing:
        passed = False
        reasons.append(
            f"missing migration set {sorted(missing)} != expected {sorted(expected_missing)}"
        )

    h.emit(
        passed=passed, reason="; ".join(reasons),
        per_agent={
            "postgres": {"schema_version": pg_v},
            "sqlite":   {"schema_version": sqlite_v},
        },
        missing_migrations=sorted(missing),
        missing_features=missing_features,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
