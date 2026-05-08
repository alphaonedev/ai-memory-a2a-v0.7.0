#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 73 — Run the upstream `tests/sal_contract.rs` suite against the
live postgres droplet.

The ai-memory-mcp repo ships `tests/sal_contract.rs` — a per-backend
contract test that asserts the SAL trait surface (store, recall, link
where supported, kg_query, etc.) behaves identically across SQLite +
postgres. v0.7.0-alpha is the first release where the postgres adapter
must pass this suite end-to-end.

Phases:
  A. resolve repo + cargo: try (in order) `$AI_MEMORY_REPO_PATH`,
     `/opt/ai-memory-mcp`, then SKIP. Same for `cargo` on PATH.
  B. drop+create the disposable `aimemory_saltest` database on
     postgres-node so the run starts from a clean state.
  C. export AI_MEMORY_TEST_POSTGRES_URL and run
     `cargo test --features sal-postgres --test sal_contract -- --test-threads=1`.
  D. parse cargo output for `test result:` summary; PASS iff `0 failed`.

SKIP conditions (reported clearly in the report):
  * cargo not on PATH on the orchestrator/runner node
  * the ai-memory-mcp repo isn't checked out anywhere we can find it
  * the postgres password file is missing

PASS iff: cargo test exits 0 AND output contains `0 failed`.
"""
import sys, pathlib, shlex, re, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "73"


def _which(h: Harness, node_ip: str, binary: str) -> str:
    r = h.ssh_exec(node_ip, f"command -v {shlex.quote(binary)} || true", timeout=10)
    return (r.stdout or "").strip()


def _find_repo(h: Harness, node_ip: str) -> str:
    candidates = [
        os.environ.get("AI_MEMORY_REPO_PATH", ""),
        "/opt/ai-memory-mcp",
        "/root/ai-memory-mcp",
        "/Users/fate/v07/v07-fixes",
    ]
    for path in candidates:
        if not path:
            continue
        r = h.ssh_exec(node_ip, f"test -f {shlex.quote(path)}/Cargo.toml && echo yes || echo no",
                        timeout=10)
        if (r.stdout or "").strip() == "yes":
            return path
    return ""


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    try:
        admin_url = h.postgres_url(db="postgres")
        pg_url = h.postgres_url(db="aimemory_saltest")
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    # The cargo run can happen on either openclaw or the orchestrator.
    # Default to openclaw because it's already inside the VPC and has
    # build-essential pre-installed by setup_node.sh.
    runner_ip = h.node1_ip

    log("phase A: locate cargo + ai-memory-mcp repo on runner")
    cargo_path = _which(h, runner_ip, "cargo")
    repo_path = _find_repo(h, runner_ip)
    if not cargo_path:
        h.skip("cargo not installed on runner droplet — install rustup or "
               "run S73 from a host with cargo. (See docs/coverage.md "
               "Postgres+AGE section for the SKIP-condition policy.)",
               cargo="missing", repo=repo_path)
        return
    if not repo_path:
        h.skip("ai-memory-mcp repo not found on runner; set "
               "AI_MEMORY_REPO_PATH or clone to /opt/ai-memory-mcp",
               cargo=cargo_path, repo="missing")
        return
    log(f"  cargo={cargo_path} repo={repo_path}")

    log("phase B: prepare aimemory_saltest db (drop+create)")
    h.ssh_exec(runner_ip, (
        f"psql {shlex.quote(admin_url)} -c "
        "'DROP DATABASE IF EXISTS aimemory_saltest'"
    ), timeout=20)
    h.ssh_exec(runner_ip, (
        f"psql {shlex.quote(admin_url)} -c "
        "'CREATE DATABASE aimemory_saltest OWNER aimemory'"
    ), timeout=20)

    log("phase C: cargo test --features sal-postgres --test sal_contract")
    cmd = (
        f"cd {shlex.quote(repo_path)} && "
        f"AI_MEMORY_TEST_POSTGRES_URL={shlex.quote(pg_url)} "
        f"cargo test --features sal-postgres --test sal_contract -- "
        f"--test-threads=1 --nocapture"
    )
    r = h.ssh_exec(runner_ip, cmd, timeout=900)
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    log(f"  cargo rc={r.returncode}")
    log(f"  tail: {out[-400:]}")

    log("phase D: parse `test result:` summary")
    summary = re.findall(
        r"test result: ([a-zA-Z]+)\.\s*(\d+)\s*passed;\s*(\d+)\s*failed",
        out,
    )
    total_passed = sum(int(p) for _, p, _ in summary)
    total_failed = sum(int(f) for _, _, f in summary)

    reasons: list[str] = []
    passed = r.returncode == 0 and total_failed == 0 and total_passed > 0
    if r.returncode != 0:
        reasons.append(f"cargo test exited {r.returncode}")
    if total_failed > 0:
        reasons.append(f"{total_failed} test(s) failed in sal_contract")
    if total_passed == 0:
        reasons.append("zero passing tests parsed — output may be malformed")

    h.emit(
        passed=passed, reason="; ".join(reasons),
        per_agent={
            "runner": {
                "cargo_rc": r.returncode,
                "tests_passed": total_passed,
                "tests_failed": total_failed,
                "summary_lines": [list(s) for s in summary],
            }
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
