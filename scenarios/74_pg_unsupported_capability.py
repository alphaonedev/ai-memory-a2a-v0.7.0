#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 74 — Verify postgres SAL adapter returns `UnsupportedCapability`
for the two capabilities not yet wired in v0.7.0-alpha.

The postgres SAL adapter at v0.7.0-alpha schema_version=15 lacks two
capabilities present in the SQLite adapter at v28:
  * `LINKS`              — `MemoryStore::link()` errors
  * `AGENT_REGISTRATION` — `MemoryStore::register_agent()` errors

Both must surface as `Err(StoreError::UnsupportedCapability { name: ... })`
— NOT silently succeed, NOT panic, NOT 500. This test asserts the error
surface is well-behaved and the capability name is exactly the documented
string.

Phases:
  A. open a postgres SAL adapter via a tiny inline Rust binary OR via
     `ai-memory probe-capability` if installed; capture stderr.
  B. invoke link()                  — must return UnsupportedCapability("LINKS")
  C. invoke register_agent()        — must return UnsupportedCapability("AGENT_REGISTRATION")
  D. invoke memory_store()          — must succeed (sanity: adapter works for the supported path)

PASS iff:
  * link()           → exit !=0 + stderr contains "UnsupportedCapability" + "LINKS"
  * register_agent() → exit !=0 + stderr contains "UnsupportedCapability" + "AGENT_REGISTRATION"
  * memory_store()   → exit 0 (the adapter is genuinely usable for the supported surface)
  * NO stack traces, NO panics in any of the three runs.
"""
import sys, pathlib, shlex, re
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "74"


def _probe(h: Harness, node_ip: str, pg_url: str, op: str, *args: str) -> dict:
    """Run `ai-memory probe-capability --store-url <pg_url> <op> ...` and
    capture rc + stderr. The probe-capability subcommand is a thin
    wrapper added in v0.7.0-alpha for exactly this kind of test
    (per-capability error-surface verification)."""
    argv = " ".join(shlex.quote(a) for a in args)
    cmd = (
        f"ai-memory probe-capability --store-url {shlex.quote(pg_url)} "
        f"{shlex.quote(op)} {argv} 2>&1; echo __RC__=$?"
    )
    r = h.ssh_exec(node_ip, cmd, timeout=60)
    out = r.stdout or ""
    m = re.search(r"__RC__=(\d+)", out)
    rc = int(m.group(1)) if m else r.returncode
    return {"rc": rc, "out": out, "stderr_tail": out[-400:]}


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    try:
        pg_url = h.postgres_url(db="aimemory_s74")
        admin_url = h.postgres_url(db="postgres")
    except RuntimeError as e:
        h.skip(f"postgres password unavailable: {e}")
        return

    log("setup: drop+create disposable aimemory_s74 db")
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(admin_url)} -c 'DROP DATABASE IF EXISTS aimemory_s74'"
    ), timeout=20)
    h.ssh_exec(h.node1_ip, (
        f"psql {shlex.quote(admin_url)} -c 'CREATE DATABASE aimemory_s74 OWNER aimemory'"
    ), timeout=20)
    h.ssh_exec(h.node1_ip, (
        f"ai-memory schema-init --store-url {shlex.quote(pg_url)}"
    ), timeout=60)

    log("phase A: probe capability surface — first establish baseline (store)")
    store_res = _probe(
        h, h.node1_ip, pg_url, "store",
        "--namespace", "s74-baseline",
        "--title", f"baseline-{new_uuid()[:6]}",
        "--content", "store should succeed on pg",
    )
    log(f"  store rc={store_res['rc']}")

    log("phase B: link() — expect UnsupportedCapability(LINKS)")
    link_res = _probe(
        h, h.node1_ip, pg_url, "link",
        "--from", "00000000-0000-0000-0000-000000000001",
        "--to",   "00000000-0000-0000-0000-000000000002",
        "--rel",  "references",
    )
    log(f"  link rc={link_res['rc']}")

    log("phase C: register_agent() — expect UnsupportedCapability(AGENT_REGISTRATION)")
    reg_res = _probe(
        h, h.node1_ip, pg_url, "register_agent",
        "--agent-id", "ai:s74-test",
        "--agent-type", "scenario",
    )
    log(f"  register rc={reg_res['rc']}")

    reasons: list[str] = []
    passed = True

    # Sanity: store on the supported surface must succeed.
    if store_res["rc"] != 0:
        passed = False
        reasons.append(f"baseline store() failed rc={store_res['rc']} (adapter may be broken)")

    # link() must error with the documented capability name.
    if link_res["rc"] == 0:
        passed = False
        reasons.append("link() returned rc=0 — silent success on UNSUPPORTED capability")
    elif "UnsupportedCapability" not in link_res["out"]:
        passed = False
        reasons.append(f"link() error without UnsupportedCapability marker: {link_res['stderr_tail']}")
    elif "LINKS" not in link_res["out"]:
        passed = False
        reasons.append(f"link() UnsupportedCapability without LINKS name: {link_res['stderr_tail']}")
    if "panicked" in link_res["out"].lower() or "stack backtrace" in link_res["out"].lower():
        passed = False
        reasons.append("link() panicked instead of returning Err")

    # register_agent() must error similarly.
    if reg_res["rc"] == 0:
        passed = False
        reasons.append("register_agent() returned rc=0 — silent success")
    elif "UnsupportedCapability" not in reg_res["out"]:
        passed = False
        reasons.append(f"register_agent() error without UnsupportedCapability: {reg_res['stderr_tail']}")
    elif "AGENT_REGISTRATION" not in reg_res["out"]:
        passed = False
        reasons.append(f"register_agent() missing AGENT_REGISTRATION name: {reg_res['stderr_tail']}")
    if "panicked" in reg_res["out"].lower() or "stack backtrace" in reg_res["out"].lower():
        passed = False
        reasons.append("register_agent() panicked instead of returning Err")

    h.emit(
        passed=passed, reason="; ".join(reasons),
        per_agent={
            "openclaw": {
                "store_rc": store_res["rc"],
                "link_rc": link_res["rc"],
                "register_agent_rc": reg_res["rc"],
                "link_tail": link_res["stderr_tail"],
                "register_tail": reg_res["stderr_tail"],
            }
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
