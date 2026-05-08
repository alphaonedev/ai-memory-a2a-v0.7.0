#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 70 — SQLite→Postgres→SQLite migration round-trip + idempotency.

In v0.7.0-alpha the postgres surface is migration-only — `ai-memory serve
--store-url postgres://...` is deferred to v0.7.1. This scenario exercises
the only postgres path that ships GREEN in v0.7.0:
  * `ai-memory migrate --from sqlite://X --to postgres://Y --json` (forward)
  * idempotent re-run (UPSERT on (namespace, title), 0 net new on rerun)
  * reverse migration postgres → fresh sqlite
  * content-hash equivalence end-to-end

Phases:
  A. openclaw seeds 1000 memories on its local SQLite across 5 namespaces
     with varied tier/priority/tag distribution.
  B. forward migrate sqlite → postgres; psql row-count must equal 1000.
  C. re-run the same migrate; report must show 0 net new + 0 errors.
  D. reverse migrate postgres → fresh sqlite; row count + content-hash
     of the round-tripped corpus must equal the original.

PASS iff: A=1000, B=1000 in pg, C delta=0, D=1000 + sha256(content_set)
matches the seed.
"""
import sys, pathlib, hashlib, json, shlex
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "70"
SEED_COUNT = 1000
NAMESPACES = [f"s70-ns-{i}" for i in range(5)]


def _pg_count(h: Harness, pg_url: str) -> int:
    """Count distinct memories in the postgres `memories` table via psql.
    Run from openclaw (in the VPC). The password is embedded in pg_url
    so PGPASSWORD env is unnecessary."""
    cmd = f"psql {shlex.quote(pg_url)} -tA -c 'SELECT count(*) FROM memories'"
    r = h.ssh_exec(h.node1_ip, cmd, timeout=30)
    try:
        return int((r.stdout or "").strip())
    except ValueError:
        return -1


def _seed_local_sqlite(h: Harness, sqlite_path: str) -> int:
    """Seed openclaw's local sqlite with SEED_COUNT memories. Returns the
    actual count written (so the assertion catches partial seeding)."""
    rows: list[dict] = []
    for i in range(SEED_COUNT):
        ns = NAMESPACES[i % len(NAMESPACES)]
        rows.append({
            "namespace": ns,
            "title": f"s70-{i:04d}-{new_uuid()[:6]}",
            "content": f"s70-content-{i}-marker={new_uuid()}",
            "tier": ("hot", "mid", "cold")[i % 3],
            "priority": (i % 9) + 1,
            "metadata": {"agent_id": "ai:openclaw", "scenario": "70", "seq": i},
        })
    payload = json.dumps(rows)
    stage = (
        f"cat > /tmp/s70-seed.json <<'__SEED_EOF__'\n{payload}\n__SEED_EOF__"
    )
    h.ssh_exec(h.node1_ip, stage, timeout=60)
    # Use the bundled `ai-memory import` CLI which writes directly to the
    # store backing the local daemon. AI_MEMORY_STORE_URL points at the
    # local sqlite explicitly so we don't perturb the running daemon's db.
    cmd = (
        f"AI_MEMORY_STORE_URL=sqlite://{shlex.quote(sqlite_path)} "
        f"ai-memory import --format json --input /tmp/s70-seed.json --json"
    )
    r = h.ssh_exec(h.node1_ip, cmd, timeout=120)
    try:
        rep = json.loads(r.stdout or "{}")
        return int(rep.get("imported", 0))
    except (ValueError, TypeError):
        return 0


def _content_hash_sqlite(h: Harness, sqlite_path: str) -> str:
    cmd = (
        f"sqlite3 {shlex.quote(sqlite_path)} "
        f"\"SELECT content FROM memories ORDER BY namespace, title;\" "
        f"| sha256sum | awk '{{print $1}}'"
    )
    r = h.ssh_exec(h.node1_ip, cmd, timeout=30)
    return (r.stdout or "").strip()


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    try:
        pg_url = h.postgres_url(db="aimemory_s70")
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    seed_db = f"/tmp/s70-seed-{new_uuid()[:6]}.sqlite"
    return_db = f"/tmp/s70-return-{new_uuid()[:6]}.sqlite"
    h.ssh_exec(h.node1_ip, f"rm -f {seed_db} {return_db}", timeout=10)

    # Reset the target pg db to a clean state.
    log("phase A: drop+create aimemory_s70 on postgres-node, seed sqlite")
    pg_admin_url = h.postgres_url(db="postgres")
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(pg_admin_url)} -c "
        f"'DROP DATABASE IF EXISTS aimemory_s70'"
    ), timeout=20)
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(pg_admin_url)} -c "
        f"'CREATE DATABASE aimemory_s70 OWNER aimemory'"
    ), timeout=20)

    seed_n = _seed_local_sqlite(h, seed_db)
    log(f"  seeded {seed_n}/{SEED_COUNT} into {seed_db}")
    seed_hash = _content_hash_sqlite(h, seed_db)
    log(f"  seed content sha256={seed_hash[:16]}...")

    log("phase B: forward migrate sqlite → postgres")
    fwd_cmd = (
        f"ai-memory migrate "
        f"--from sqlite://{shlex.quote(seed_db)} "
        f"--to {shlex.quote(pg_url)} --json"
    )
    fwd = h.ssh_exec(h.node1_ip, fwd_cmd, timeout=300)
    fwd_report: dict = {}
    try:
        fwd_report = json.loads(fwd.stdout or "{}")
    except ValueError:
        pass
    fwd_count = _pg_count(h, pg_url)
    log(f"  forward migrate report={fwd_report} pg_count={fwd_count}")

    log("phase C: idempotent rerun")
    rerun = h.ssh_exec(h.node1_ip, fwd_cmd, timeout=300)
    rerun_report: dict = {}
    try:
        rerun_report = json.loads(rerun.stdout or "{}")
    except ValueError:
        pass
    rerun_count = _pg_count(h, pg_url)
    log(f"  rerun report={rerun_report} pg_count={rerun_count}")

    log("phase D: reverse migrate postgres → fresh sqlite")
    rev_cmd = (
        f"ai-memory migrate "
        f"--from {shlex.quote(pg_url)} "
        f"--to sqlite://{shlex.quote(return_db)} --json"
    )
    rev = h.ssh_exec(h.node1_ip, rev_cmd, timeout=300)
    rev_report: dict = {}
    try:
        rev_report = json.loads(rev.stdout or "{}")
    except ValueError:
        pass
    return_hash = _content_hash_sqlite(h, return_db)
    return_count_cmd = f"sqlite3 {shlex.quote(return_db)} 'SELECT count(*) FROM memories;'"
    rcr = h.ssh_exec(h.node1_ip, return_count_cmd, timeout=20)
    try:
        return_count = int((rcr.stdout or "0").strip())
    except ValueError:
        return_count = -1
    log(f"  reverse migrate report={rev_report} sqlite_count={return_count}")

    reasons: list[str] = []
    passed = True
    if seed_n != SEED_COUNT:
        passed = False; reasons.append(f"seed wrote {seed_n}/{SEED_COUNT}")
    if fwd_count != SEED_COUNT:
        passed = False; reasons.append(f"forward landed {fwd_count} in pg (expected {SEED_COUNT})")
    rerun_new = (rerun_report.get("inserted") if isinstance(rerun_report, dict) else None) or 0
    rerun_err = (rerun_report.get("errors") if isinstance(rerun_report, dict) else None) or 0
    if rerun_new != 0:
        passed = False; reasons.append(f"idempotent rerun reported {rerun_new} net new (expected 0)")
    if rerun_err != 0:
        passed = False; reasons.append(f"idempotent rerun reported {rerun_err} errors")
    if rerun_count != SEED_COUNT:
        passed = False; reasons.append(f"pg row count after rerun = {rerun_count} (expected {SEED_COUNT})")
    if return_count != SEED_COUNT:
        passed = False; reasons.append(f"reverse landed {return_count} in sqlite (expected {SEED_COUNT})")
    if seed_hash and return_hash and seed_hash != return_hash:
        passed = False
        reasons.append(f"content sha256 mismatch seed={seed_hash[:16]} return={return_hash[:16]}")

    h.emit(
        passed=passed, reason="; ".join(reasons),
        per_agent={
            "openclaw": {
                "seeded": seed_n,
                "fwd_pg_count": fwd_count,
                "rerun_inserted": rerun_new,
                "rerun_errors": rerun_err,
                "rev_sqlite_count": return_count,
                "seed_hash": seed_hash[:16],
                "return_hash": return_hash[:16],
            }
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
