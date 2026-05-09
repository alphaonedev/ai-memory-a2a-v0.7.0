#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 52 — Cross-agent attest_level=self_signed link.

S52 cross-agent attest_level=self_signed: openclaw stores M, hermes stores N,
daemon-on-openclaw signs the link openclaw→hermes; verify
attest_level=self_signed, length(signature)=64, observed_by=daemon. Repeat
with link initiated from hermes side.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "52"


def link_and_check(h: Harness, src_node: str, dst_node: str, src_aid: str, dst_aid: str) -> dict:
    """Store M on src, N on dst, link M→N from src's daemon, return link record."""
    ns = f"scenario52-{src_aid.replace(':', '-').replace('@', '-')}"
    marker = new_uuid("s52-")
    _, m_doc = h.write_memory(src_node, src_aid, ns, title=f"M-{marker}",
                              content=f"M for {marker}", include_status=True)
    _, n_doc = h.write_memory(dst_node, dst_aid, ns, title=f"N-{marker}",
                              content=f"N for {marker}", include_status=True)
    m_id = (m_doc or {}).get("body", {}).get("id") if isinstance(m_doc, dict) else None
    n_id = (n_doc or {}).get("body", {}).get("id") if isinstance(n_doc, dict) else None
    if not (m_id and n_id):
        return {"err": f"store failed M={m_id} N={n_id}"}

    # v0.7 contract: links use source_id/target_id/relation (not from/to/rel_type).
    link_req = {"source_id": m_id, "target_id": n_id, "relation": "related_to"}
    rc, resp = h.http_on(src_node, "POST", "/api/v1/links",
                         body=link_req, agent_id=src_aid, include_status=True)
    rbody = (resp or {}).get("body") if isinstance(resp, dict) else None
    if not isinstance(rbody, dict):
        return {"err": f"link create failed rc={rc} resp={resp}"}

    # v0.7 POST /api/v1/links returns only `{linked, attest_level}` — the
    # signature bytes + observed_by live in `memory_links` and surface via
    # `memory_verify` (MCP tool, no HTTP twin). Invoke memory_verify over
    # MCP-stdio with --db pointing at the live daemon's sqlite path.
    import json as _json
    daemon_db = ("/var/lib/ai-memory/openclaw.db"
                 if src_node == h.node1_ip
                 else "/var/lib/ai-memory/hermes.db")
    init = _json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"clientInfo": {"name": "s52", "version": "0"},
                                   "capabilities": {}, "protocolVersion": "2024-11-05"}})
    call = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "memory_verify",
                                   "arguments": {"source_id": m_id,
                                                 "target_id": n_id,
                                                 "relation": "related_to"}}})
    stdin = init + "\n" + call + "\n"
    cmd = f"AI_MEMORY_DB={daemon_db} ai-memory mcp --profile graph"
    r = h.ssh_exec(src_node, cmd, timeout=20, stdin=stdin)
    verify: dict = {}
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if d.get("id") == 1:
            cont = (d.get("result") or {}).get("content") or []
            if cont and isinstance(cont[0], dict):
                txt = cont[0].get("text") or ""
                try:
                    verify = _json.loads(txt)
                except _json.JSONDecodeError:
                    pass
            break

    return {
        "from": m_id, "to": n_id,
        "attest_level": verify.get("attest_level") or rbody.get("attest_level"),
        # Pass the raw signature_verified marker through; the assertion
        # below is now "verified=true" (semantic) instead of "signature
        # length=64" (byte-level), since v0.7 doesn't surface raw bytes
        # over MCP — `memory_verify` returns only the bool result.
        "signature_verified": bool(verify.get("signature_verified")),
        "observed_by": verify.get("signed_by") or rbody.get("observed_by"),
        "rc": rc,
    }


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    openclaw, hermes = h.node1_ip, h.node2_ip
    suffix = new_uuid()[:6]
    OPEN = f"ai:s52-openclaw-{suffix}"
    HERM = f"ai:s52-hermes-{suffix}"

    log("phase A: link openclaw→hermes signed by openclaw daemon")
    a = link_and_check(h, openclaw, hermes, OPEN, HERM)
    log(f"  {a}")

    log("phase B: link hermes→openclaw signed by hermes daemon")
    b = link_and_check(h, hermes, openclaw, HERM, OPEN)
    log(f"  {b}")

    reasons: list[str] = []
    passed = True
    for label, rec in [("openclaw_side", a), ("hermes_side", b)]:
        if "err" in rec:
            passed = False
            reasons.append(f"{label}: {rec['err']}")
            continue
        if rec.get("attest_level") != "self_signed":
            passed = False
            reasons.append(f"{label}: attest_level={rec.get('attest_level')} (expected self_signed)")
        # v0.7 verification: memory_verify returns a bool `signature_verified`
        # (not raw bytes). The 64-byte ed25519 signature is in the DB but
        # not surfaced over MCP. Assert the verifier confirmed the signature
        # — semantically equivalent to "well-formed 64-byte signature".
        if not rec.get("signature_verified"):
            passed = False
            reasons.append(f"{label}: signature_verified=False (expected True)")
        if rec.get("observed_by") != "daemon":
            passed = False
            reasons.append(f"{label}: observed_by={rec.get('observed_by')} (expected daemon)")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={"openclaw_side": a, "hermes_side": b}, reasons=reasons)


if __name__ == "__main__":
    main()
