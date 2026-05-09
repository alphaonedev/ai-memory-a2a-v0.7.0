#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 77 — Postgres-backed daemon bootstrap (Wave 4).

Wave 4 prerequisite verification: each in-fleet daemon was launched with
`--store-url postgres://...` (post-Continuation 3) AND its capabilities
surface reports the postgres backend identifier. PASS criteria:

  - openclaw + hermes both expose /api/v1/capabilities with 200 OK.
  - Both daemons report a storage_backend field whose value identifies
    postgres (case-insensitive: "postgres", "pg", "postgres-sal" all OK).
  - Process command-line on both droplets contains `--store-url postgres`.

Self-skips cleanly when A2A_BACKEND_KIND=sqlite (legacy 56/68 baseline).
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log

SCENARIO_ID = "77"


def _probe_command_line(h: Harness, node_ip: str) -> str:
    """Return the daemon's command-line via pgrep -af. '' on miss."""
    r = h.ssh_exec(node_ip, "pgrep -af 'ai-memory serve' | head -n1", timeout=10)
    return (r.stdout or "").strip()


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    h.skip_if_backend_sqlite(
        "S77 verifies postgres-backed daemon bootstrap; "
        "A2A_BACKEND_KIND=sqlite means daemons run with --db <path> "
        "(legacy v0.7.0-alpha topology) — nothing to validate."
    )

    openclaw, hermes = h.node1_ip, h.node2_ip
    reasons: list[str] = []
    passed = True

    log("phase A: capabilities surface on both daemons")
    open_label = h.daemon_storage_label(openclaw)
    herm_label = h.daemon_storage_label(hermes)
    log(f"  openclaw storage_backend={open_label!r}")
    log(f"  hermes   storage_backend={herm_label!r}")

    def _label_says_postgres(s: str) -> bool:
        s = (s or "").lower()
        return any(k in s for k in ("postgres", "postgresql", "pg-sal", "pg_sal", "pg"))

    if h.node_backend(openclaw) == "postgres" and not _label_says_postgres(open_label):
        passed = False
        reasons.append(
            f"openclaw capabilities report storage_backend={open_label!r}; "
            "expected something matching 'postgres'")
    if h.node_backend(hermes) == "postgres" and not _label_says_postgres(herm_label):
        passed = False
        reasons.append(
            f"hermes capabilities report storage_backend={herm_label!r}; "
            "expected something matching 'postgres'")

    log("phase B: process command-line check (--store-url postgres)")
    open_cmd = _probe_command_line(h, openclaw)
    herm_cmd = _probe_command_line(h, hermes)
    log(f"  openclaw cmdline tail: {open_cmd[-200:]}")
    log(f"  hermes   cmdline tail: {herm_cmd[-200:]}")

    if h.node_backend(openclaw) == "postgres" and "--store-url" not in open_cmd:
        passed = False
        reasons.append(
            "openclaw daemon command-line missing --store-url; "
            "expected `ai-memory serve --store-url postgres://...`")
    if h.node_backend(openclaw) == "postgres" and "postgres" not in open_cmd:
        passed = False
        reasons.append("openclaw daemon command-line --store-url is not postgres scheme")

    if h.node_backend(hermes) == "postgres" and "--store-url" not in herm_cmd:
        passed = False
        reasons.append(
            "hermes daemon command-line missing --store-url; "
            "expected `ai-memory serve --store-url postgres://...`")
    if h.node_backend(hermes) == "postgres" and "postgres" not in herm_cmd:
        passed = False
        reasons.append("hermes daemon command-line --store-url is not postgres scheme")

    h.emit(
        passed=passed,
        reason="; ".join(reasons),
        per_agent={
            "openclaw": {
                "storage_backend": open_label,
                "node_backend": h.node_backend(openclaw),
                "cmdline_tail": open_cmd[-160:],
            },
            "hermes": {
                "storage_backend": herm_label,
                "node_backend": h.node_backend(hermes),
                "cmdline_tail": herm_cmd[-160:],
            },
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
