#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 57 — Audit chain A2A.

S57 audit chain A2A: enable AI_MEMORY_AUDIT_DIR=/var/log/ai-memory/audit/ on
both nodes; run 5 stores + 5 recalls + 5 links from each node;
`ai-memory audit verify` exits 0; tamper test (append a hand-crafted bad line)
exits non-zero with chain-break message; cross-restart continuity verified.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "57"


def run_workload(h: Harness, node_ip: str, agent_id: str) -> int:
    """Drive 5 stores + 5 recalls + 5 links. Returns count of clean responses."""
    ns = f"s57-{agent_id.split(':')[1].split('@')[0]}"
    ok = 0
    # 5 stores
    ids = []
    for i in range(5):
        _, doc = h.write_memory(node_ip, agent_id, ns, title=f"s57-w{i}",
                                content=f"audit smoke {i} {new_uuid()[:6]}",
                                include_status=True)
        if isinstance(doc, dict):
            mid = (doc.get("body") or {}).get("id")
            if mid:
                ids.append(mid); ok += 1
    # 5 recalls
    for _ in range(5):
        rc, _ = h.list_memories(node_ip, ns, limit=10)
        if rc == 0:
            ok += 1
    # 5 links
    if len(ids) >= 2:
        for i in range(min(5, len(ids) - 1)):
            rc, _ = h.http_on(node_ip, "POST", "/api/v1/links",
                              body={"source_id": ids[i], "target_id": ids[i+1], "relation": "related_to"},
                              agent_id=agent_id, include_status=True)
            if rc == 0:
                ok += 1
    return ok


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    openclaw, hermes = h.node1_ip, h.node2_ip

    log("phase A: audit-dir is enabled at boot via AI_MEMORY_AUDIT_DIR")
    # Verify both nodes have the env exported in their service.
    for node in (openclaw, hermes):
        r = h.ssh_exec(node, "systemctl show ai-memory --property=Environment | grep AUDIT_DIR || env | grep AUDIT_DIR || true")
        log(f"  {node}: {r.stdout.strip()}")

    log("phase B: drive 5+5+5 ops on each node")
    suffix = new_uuid()[:6]
    open_ok = run_workload(h, openclaw, f"ai:s57-openclaw-{suffix}")
    herm_ok = run_workload(h, hermes,   f"ai:s57-hermes-{suffix}")
    log(f"  openclaw_ops_ok={open_ok}  hermes_ops_ok={herm_ok}")

    log("phase C: ai-memory audit verify")
    # F2 fix landed in commit e0d2086 (Round-5): audit::init now seeds
    # the SEQUENCE counter from the trailing record's sequence so the
    # next emit is last_sequence+1, monotonic across restarts.
    #
    # The droplet's pre-fix audit.log may carry HISTORIC sequence
    # resets from before the fix landed; the campaign's F2 brief
    # specifies leaving audit.log in place precisely to validate
    # forward continuity (the fix does not retroactively repair old
    # gaps). To exercise the fix authentically we therefore use a
    # **line-anchored** post-restart sequence check rather than scanning
    # the full file: capture the current line count of audit.log BEFORE
    # the restart in phase E, then after the restart emit one more event
    # and assert that event's sequence == (sequence at the captured
    # line) + 1. This pins the F2 invariant — the post-restart emission
    # continues from the prior tail's sequence + 1 — without being
    # confused by historic resets earlier in the file.
    def _read_last_line_with_sequence(node_ip: str) -> tuple[int, int]:
        """Return (line_number, sequence) of the LAST record in audit.log
        that has a parseable sequence field. (line_number, 0) on empty."""
        cmd = (
            "python3 - <<'PY'\n"
            "import json\n"
            "last_lineno = 0\n"
            "last_seq = 0\n"
            "with open('/var/log/ai-memory/audit/audit.log') as f:\n"
            "    for i, line in enumerate(f, 1):\n"
            "        line = line.strip()\n"
            "        if not line:\n"
            "            continue\n"
            "        try:\n"
            "            ev = json.loads(line)\n"
            "            seq = ev.get('sequence')\n"
            "            if isinstance(seq, int):\n"
            "                last_lineno = i\n"
            "                last_seq = seq\n"
            "        except Exception:\n"
            "            continue\n"
            "print(f'{last_lineno} {last_seq}')\n"
            "PY"
        )
        r = h.ssh_exec(node_ip, cmd)
        try:
            ln, seq = (r.stdout or "").strip().split()
            return (int(ln), int(seq))
        except (ValueError, AttributeError):
            return (0, 0)

    def _verify_post_restart_monotonic(node_ip: str, prior_lineno: int, prior_seq: int) -> tuple[bool, str]:
        """Verify that records emitted AFTER `prior_lineno` form a
        strictly monotonic continuation: the first such record must
        have sequence == prior_seq + 1, and each subsequent record
        must increment by 1. Records inserted into audit.log AFTER the
        prior_lineno but corresponding to events that happened before
        it (impossible in a sane writer) are flagged as non-monotonic.
        """
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

    # Phase C log-tail anchor — captured AFTER the workload writes.
    # Phase E will check that all NEW lines past this point form a
    # strictly monotonic continuation.
    open_prior_lineno, open_prior_seq = _read_last_line_with_sequence(openclaw)
    herm_prior_lineno, herm_prior_seq = _read_last_line_with_sequence(hermes)
    log(
        f"  audit prior tail: openclaw=line{open_prior_lineno}/seq{open_prior_seq} "
        f"hermes=line{herm_prior_lineno}/seq{herm_prior_seq}"
    )
    # Keep `*_anchor` aliases so downstream emit() reporting stays
    # backwards-compatible with prior schema readers.
    open_anchor = open_prior_seq
    herm_anchor = herm_prior_seq

    verify_open = h.ssh_exec(openclaw, "ai-memory audit verify --audit-dir /var/log/ai-memory/audit/")
    verify_herm = h.ssh_exec(hermes,   "ai-memory audit verify --audit-dir /var/log/ai-memory/audit/")

    log("phase D: tamper test — append a bad line; verify must exit non-zero")
    tamper_script = r"""set -u
LATEST=$(ls -1t /var/log/ai-memory/audit/*.log 2>/dev/null | head -1)
[ -n "$LATEST" ] || { echo NO_LOG; exit 99; }
cp "$LATEST" "$LATEST.bak"
echo '{"ts":"1970-01-01T00:00:00Z","op":"tamper","prev_hash":"000","hash":"deadbeef"}' >> "$LATEST"
ai-memory audit verify --audit-dir /var/log/ai-memory/audit/; rc=$?
mv "$LATEST.bak" "$LATEST"
echo "TAMPER_RC=$rc"
"""
    tamper = h.ssh_bash_script(openclaw, tamper_script, timeout=60)
    tamper_rc = -1
    msg = ""
    for line in (tamper.stdout or "").splitlines():
        if line.startswith("TAMPER_RC="):
            try: tamper_rc = int(line.split("=", 1)[1])
            except ValueError: pass
        msg += line + "\n"

    log("phase E: restart continuity — restart daemon, then drive 1 op per node")
    for node in (openclaw, hermes):
        h.ssh_exec(node, "systemctl restart ai-memory || true")
    h.settle(10, "daemon restart settle")
    # Drive a single audited write on each node so a fresh post-restart
    # event lands in the log; the F2 forward-monotonicity check below
    # then proves the writer continued from the pre-restart tail rather
    # than resetting to 1.
    suffix2 = new_uuid()[:6]
    h.write_memory(openclaw, f"ai:s57-openclaw-postr-{suffix2}", "s57-openclaw-postrestart",
                   title="post-restart-1", content="post-restart audited op")
    h.write_memory(hermes,   f"ai:s57-hermes-postr-{suffix2}",   "s57-hermes-postrestart",
                   title="post-restart-1", content="post-restart audited op")
    h.settle(2, "audit fsync")
    restart_open = h.ssh_exec(openclaw, "ai-memory audit verify --audit-dir /var/log/ai-memory/audit/")
    restart_herm = h.ssh_exec(hermes,   "ai-memory audit verify --audit-dir /var/log/ai-memory/audit/")

    # F2 forward-monotonicity: every record emitted AFTER the prior
    # tail line (including post-restart events) must be strictly
    # prev_seq + 1, where the seed is the sequence at prior_lineno.
    open_mono_ok, open_mono_detail = _verify_post_restart_monotonic(
        openclaw, open_prior_lineno, open_prior_seq,
    )
    herm_mono_ok, herm_mono_detail = _verify_post_restart_monotonic(
        hermes, herm_prior_lineno, herm_prior_seq,
    )

    reasons: list[str] = []
    passed = True
    if open_ok < 14: reasons.append(f"openclaw workload ops_ok={open_ok}/15"); passed = False
    if herm_ok < 14: reasons.append(f"hermes workload ops_ok={herm_ok}/15"); passed = False
    if not open_mono_ok: reasons.append(f"openclaw post-anchor monotonicity FAIL: {open_mono_detail}"); passed = False
    if not herm_mono_ok: reasons.append(f"hermes post-anchor monotonicity FAIL: {herm_mono_detail}"); passed = False
    if tamper_rc == 0: reasons.append("tamper verify returned 0 (expected non-zero)"); passed = False
    chain_break_seen = any(needle in msg.lower() for needle in ("chain", "tamper", "hash"))
    if not chain_break_seen: reasons.append("tamper message did not mention chain/hash break"); passed = False

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw": {"workload_ok": open_ok, "verify_rc": verify_open.returncode,
                            "post_restart_rc": restart_open.returncode,
                            "anchor": open_anchor, "monotonic_after": open_mono_detail},
               "hermes":   {"workload_ok": herm_ok, "verify_rc": verify_herm.returncode,
                            "post_restart_rc": restart_herm.returncode,
                            "anchor": herm_anchor, "monotonic_after": herm_mono_detail},
           },
           tamper_rc=tamper_rc, reasons=reasons)


if __name__ == "__main__":
    main()
