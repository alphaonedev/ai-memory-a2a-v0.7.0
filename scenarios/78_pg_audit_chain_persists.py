#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 78 — Audit chain seq persists across daemon restart (postgres backend).

Wave 4 validates the F2 fix on the postgres path: restarting a
postgres-backed daemon must continue audit sequence numbers from the
prior tail rather than resetting to 1. Mirrors S57's forward-monotonicity
oracle but runs against `--store-url postgres://`.

Phases:
  A. Capture last (line, sequence) of /var/log/ai-memory/audit/audit.log
     on openclaw + hermes.
  B. Write 3 audited events on each node.
  C. Restart both daemons (postgres-backed).
  D. Settle, then write 1 more event each; assert post-restart sequence
     == prior_seq + (events written between A and the post-restart op).

Self-skips when A2A_BACKEND_KIND=sqlite.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "78"


def _read_last_line_with_sequence(h: Harness, node_ip: str) -> tuple[int, int]:
    cmd = (
        "python3 - <<'PY'\n"
        "import json\n"
        "last_lineno = 0\n"
        "last_seq = 0\n"
        "try:\n"
        "    with open('/var/log/ai-memory/audit/audit.log') as f:\n"
        "        for i, line in enumerate(f, 1):\n"
        "            line = line.strip()\n"
        "            if not line:\n"
        "                continue\n"
        "            try:\n"
        "                ev = json.loads(line)\n"
        "                seq = ev.get('sequence')\n"
        "                if isinstance(seq, int):\n"
        "                    last_lineno = i\n"
        "                    last_seq = seq\n"
        "            except Exception:\n"
        "                continue\n"
        "except FileNotFoundError:\n"
        "    pass\n"
        "print(f'{last_lineno} {last_seq}')\n"
        "PY"
    )
    r = h.ssh_exec(node_ip, cmd)
    try:
        ln, seq = (r.stdout or "").strip().split()
        return (int(ln), int(seq))
    except (ValueError, AttributeError):
        return (0, 0)


def _verify_post_anchor_monotonic(
    h: Harness, node_ip: str, prior_lineno: int, prior_seq: int
) -> tuple[bool, str]:
    cmd = (
        "python3 - <<'PY'\n"
        "import json, sys\n"
        f"prior_lineno = {prior_lineno}\n"
        f"prior_seq = {prior_seq}\n"
        "prev = prior_seq\n"
        "checked = 0\n"
        "with open('/var/log/ai-memory/audit/audit.log') as f:\n"
        "    for i, line in enumerate(f, 1):\n"
        "        if i <= prior_lineno:\n"
        "            continue\n"
        "        line = line.strip()\n"
        "        if not line:\n"
        "            continue\n"
        "        try:\n"
        "            ev = json.loads(line)\n"
        "        except Exception:\n"
        "            continue\n"
        "        seq = ev.get('sequence')\n"
        "        if not isinstance(seq, int):\n"
        "            continue\n"
        "        if seq != prev + 1:\n"
        "            print(f'NONMONO line={i} prev={prev} this={seq}')\n"
        "            sys.exit(2)\n"
        "        prev = seq\n"
        "        checked += 1\n"
        "print(f'OK checked={checked} last={prev}')\n"
        "PY"
    )
    r = h.ssh_exec(node_ip, cmd)
    out = (r.stdout or "").strip()
    return (r.returncode == 0 and out.startswith("OK"), out or (r.stderr or "")[:120])


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    h.skip_if_backend_sqlite(
        "S78 validates F2 fix on postgres backend; "
        "A2A_BACKEND_KIND=sqlite duplicates S57 — skip."
    )

    openclaw, hermes = h.node1_ip, h.node2_ip
    suffix = new_uuid()[:6]
    OPEN = f"ai:s78-openclaw-{suffix}"
    HERM = f"ai:s78-hermes-{suffix}"
    ns = f"s78-{suffix}"

    log("phase A: snapshot audit-log tail on both daemons")
    open_lineno, open_seq = _read_last_line_with_sequence(h, openclaw)
    herm_lineno, herm_seq = _read_last_line_with_sequence(h, hermes)
    log(f"  openclaw tail line={open_lineno} seq={open_seq}")
    log(f"  hermes   tail line={herm_lineno} seq={herm_seq}")

    log("phase B: write 3 audited events on each node")
    for i in range(3):
        h.write_memory(openclaw, OPEN, ns, title=f"pre-{i}",
                       content=f"pre-restart {i} {new_uuid()[:6]}")
        h.write_memory(hermes, HERM, ns, title=f"pre-h{i}",
                       content=f"pre-restart h{i} {new_uuid()[:6]}")
    h.settle(2, "audit fsync pre-restart")

    log("phase C: restart postgres-backed daemons")
    for node in (openclaw, hermes):
        h.ssh_exec(node, "systemctl restart ai-memory || true")
    h.settle(10, "daemon restart settle (postgres backend)")

    log("phase D: post-restart audited write + monotonicity check")
    h.write_memory(openclaw, OPEN, ns, title="post-restart-1",
                   content="post-restart audited op")
    h.write_memory(hermes, HERM, ns, title="post-restart-h1",
                   content="post-restart audited op")
    h.settle(2, "audit fsync post-restart")

    open_ok, open_detail = _verify_post_anchor_monotonic(h, openclaw, open_lineno, open_seq)
    herm_ok, herm_detail = _verify_post_anchor_monotonic(h, hermes, herm_lineno, herm_seq)

    reasons: list[str] = []
    passed = True
    if not open_ok:
        passed = False
        reasons.append(f"openclaw post-anchor monotonicity FAIL: {open_detail}")
    if not herm_ok:
        passed = False
        reasons.append(f"hermes post-anchor monotonicity FAIL: {herm_detail}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons),
        per_agent={
            "openclaw": {
                "anchor_line": open_lineno,
                "anchor_seq": open_seq,
                "monotonic_after": open_detail,
            },
            "hermes": {
                "anchor_line": herm_lineno,
                "anchor_seq": herm_seq,
                "monotonic_after": herm_detail,
            },
        },
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
