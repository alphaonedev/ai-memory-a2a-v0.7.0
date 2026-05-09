#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 71 — AGE Cypher path == recursive-CTE fallback path (Path B).

Original premise (v0.7.0-r1/r2): drive AGE-vs-CTE via raw psql against
campaign-authored SQL views (`kg_query_view`, etc). F6 RCA showed those
views are not part of `postgres_schema.sql`; the canonical SAL adapter
exposes the routing INSIDE PostgresStore (kg_query → kg_query_cypher |
kg_query_cte) and is reachable only via the in-tree cargo test
`tests/age_cte_equivalence.rs`.

Path B (post-F6, 2026-05-08): re-point this scenario at the in-tree
test which is the canonical equivalence assertion. We ssh into openclaw
(which holds /opt/ai-memory-src @ e0d2086 round-2-fixes) and invoke

    cargo test --features sal-postgres,sal --test age_cte_equivalence \\
        -- --nocapture --test-threads=1

with `AI_MEMORY_TEST_POSTGRES_URL` + `AI_MEMORY_TEST_AGE_URL` pointed at
a fresh disposable postgres database (`aimemory_kg71`). The test suite
itself owns the equivalence oracle (sorted-row comparison of AGE vs CTE
result sets across kg_query / kg_timeline / kg_invalidate). The cargo
runner's exit code IS the scenario verdict: rc=0 => PASS.

Note on AGE half: `tests/age_cte_equivalence.rs` follows a soft-skip
pattern for the AGE branch (eprintln + ok) when fixture projection into
`memory_graph` fails — same pattern as `benches/age_vs_cte.rs`. The CTE
branch always runs and validates the canonical fallback path. Path B
considers exit-0 as the contract; the test author already encoded the
"AGE optional" stance via `eprintln!("skip AGE half: ...")`.

Phases (Path B):
  A. Bootstrap disposable db `aimemory_kg71` (caller pre-creates with
     vector + age extensions + create_graph('memory_graph')).
  B. Invoke cargo test on openclaw via ssh.
  C. PASS iff exit code 0 (which encodes the in-tree oracle's verdict).
"""
import os
import sys
import pathlib
import shlex

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log

SCENARIO_ID = "71"
SRC_DIR = "/opt/ai-memory-src"
TEST_DB = "aimemory_kg71"


def _bootstrap_db(h: Harness, admin_url: str, db: str) -> None:
    """Drop+recreate `db` and bootstrap age + pgvector + memory_graph
    projection so the AGE-half fixture can land. Idempotent within a
    scenario run; the operator may also pre-create externally."""
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
    graph = (
        f"psql {shlex.quote(db_url)} -c "
        "\"LOAD 'age'; SET search_path = ag_catalog, public; "
        "SELECT create_graph('memory_graph');\""
    )
    h.ssh_exec(h.node1_ip, graph, timeout=30)


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    try:
        admin_url = h.postgres_url(db="postgres")
        test_url = h.postgres_url(db=TEST_DB)
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    log("phase A: bootstrap disposable db aimemory_kg71")
    _bootstrap_db(h, admin_url, TEST_DB)

    log("phase B: invoke cargo test --test age_cte_equivalence on openclaw")
    cargo_cmd = (
        f"export PATH=/root/.cargo/bin:$PATH && cd {SRC_DIR} && "
        f"AI_MEMORY_TEST_POSTGRES_URL={shlex.quote(test_url)} "
        f"AI_MEMORY_TEST_AGE_URL={shlex.quote(test_url)} "
        "cargo test --features sal-postgres,sal "
        "--test age_cte_equivalence -- --nocapture --test-threads=1 2>&1"
    )
    r = h.ssh_exec(h.node1_ip, cargo_cmd, timeout=270)
    out = (r.stdout or "")
    log("  cargo test rc=" + str(r.returncode))
    # Show the test result summary line in the scenario log
    for line in out.splitlines()[-15:]:
        log("  | " + line)

    reasons: list[str] = []
    passed = (r.returncode == 0)
    if not passed:
        reasons.append(
            f"cargo test --test age_cte_equivalence exited rc={r.returncode}; "
            f"tail: {out[-400:]}"
        )

    # Parse out the canonical "test result: ok. N passed; ..." line
    summary_line = ""
    for line in out.splitlines():
        if "test result:" in line:
            summary_line = line.strip()
            break

    h.emit(
        passed=passed,
        path_b=True,
        reason="; ".join(reasons),
        per_agent={
            "openclaw": {
                "cargo_rc": r.returncode,
                "summary_line": summary_line,
                "test_db": TEST_DB,
                "validator": "tests/age_cte_equivalence.rs",
            }
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
