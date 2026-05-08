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
                              body={"from": ids[i], "to": ids[i+1], "rel_type": "related"},
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
    open_ok = run_workload(h, openclaw, "ai:openclaw@nyc3:droplet-1")
    herm_ok = run_workload(h, hermes,   "ai:hermes@nyc3:droplet-2")
    log(f"  openclaw_ops_ok={open_ok}  hermes_ops_ok={herm_ok}")

    log("phase C: ai-memory audit verify (expect rc=0 on both)")
    verify_open = h.ssh_exec(openclaw, "ai-memory audit verify --dir /var/log/ai-memory/audit/")
    verify_herm = h.ssh_exec(hermes,   "ai-memory audit verify --dir /var/log/ai-memory/audit/")

    log("phase D: tamper test — append a bad line; verify must exit non-zero")
    tamper_script = r"""set -u
LATEST=$(ls -1t /var/log/ai-memory/audit/*.log 2>/dev/null | head -1)
[ -n "$LATEST" ] || { echo NO_LOG; exit 99; }
cp "$LATEST" "$LATEST.bak"
echo '{"ts":"1970-01-01T00:00:00Z","op":"tamper","prev_hash":"000","hash":"deadbeef"}' >> "$LATEST"
ai-memory audit verify --dir /var/log/ai-memory/audit/; rc=$?
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

    log("phase E: restart continuity — restart daemon, verify still rc=0")
    for node in (openclaw, hermes):
        h.ssh_exec(node, "systemctl restart ai-memory || true")
    h.settle(8, "daemon restart settle")
    restart_open = h.ssh_exec(openclaw, "ai-memory audit verify --dir /var/log/ai-memory/audit/")
    restart_herm = h.ssh_exec(hermes,   "ai-memory audit verify --dir /var/log/ai-memory/audit/")

    reasons: list[str] = []
    passed = True
    if open_ok < 14: reasons.append(f"openclaw workload ops_ok={open_ok}/15"); passed = False
    if herm_ok < 14: reasons.append(f"hermes workload ops_ok={herm_ok}/15"); passed = False
    if verify_open.returncode != 0: reasons.append(f"openclaw verify rc={verify_open.returncode}"); passed = False
    if verify_herm.returncode != 0: reasons.append(f"hermes verify rc={verify_herm.returncode}"); passed = False
    if tamper_rc == 0: reasons.append("tamper verify returned 0 (expected non-zero)"); passed = False
    chain_break_seen = any(needle in msg.lower() for needle in ("chain", "tamper", "hash"))
    if not chain_break_seen: reasons.append("tamper message did not mention chain/hash break"); passed = False
    if restart_open.returncode != 0: reasons.append(f"post-restart openclaw verify rc={restart_open.returncode}"); passed = False
    if restart_herm.returncode != 0: reasons.append(f"post-restart hermes verify rc={restart_herm.returncode}"); passed = False

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw": {"workload_ok": open_ok, "verify_rc": verify_open.returncode,
                            "post_restart_rc": restart_open.returncode},
               "hermes":   {"workload_ok": herm_ok, "verify_rc": verify_herm.returncode,
                            "post_restart_rc": restart_herm.returncode},
           },
           tamper_rc=tamper_rc, reasons=reasons)


if __name__ == "__main__":
    main()
