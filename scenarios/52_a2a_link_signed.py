#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 52 — Cross-agent attest_level=self_signed link.

S52 cross-agent attest_level=self_signed: openclaw stores M, hermes stores N,
daemon-on-openclaw signs the link openclaw→hermes; verify
attest_level=self_signed, signature_present=true, observed_by=daemon. Repeat
with link initiated from hermes side.

# v0.7.0 Continuation 6 — HTTP migration (2026-05-09)
#
# Migrated from MCP-stdio (`ai-memory mcp` against
# `AI_MEMORY_DB=/var/lib/ai-memory/<node>.db` sqlite path) to the
# new HTTP endpoint `POST /api/v1/links/verify`. The MCP-stdio
# path read from a sqlite file that is empty/stale on
# postgres-backed daemons — the new endpoint dispatches via the
# SAL `MemoryStore::verify_link` trait so postgres-backed daemons
# read from the live `memory_links` table.
#
# Wire shape (POST /api/v1/links/verify):
#   { source_id, target_id?, link_id? }
#   -> { verified, attest_level, signature_present, observed_by,
#        source_id, target_id, relation, findings }
"""
import sys
import pathlib

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

    # Continuation 6: verify the freshly-created link via the new
    # HTTP endpoint POST /api/v1/links/verify. Replaces the prior
    # MCP-stdio `memory_verify` path which read from a stale sqlite
    # path on postgres-backed daemons.
    verify_rc, verify_resp = h.http_on(
        src_node, "POST", "/api/v1/links/verify",
        body={"source_id": m_id, "target_id": n_id},
        agent_id=src_aid, include_status=True,
    )
    vbody = (verify_resp or {}).get("body") if isinstance(verify_resp, dict) else None
    if not isinstance(vbody, dict):
        return {
            "from": m_id, "to": n_id,
            "err": f"verify_link returned non-dict body rc={verify_rc} resp={verify_resp}",
        }

    return {
        "from": m_id, "to": n_id,
        "attest_level": vbody.get("attest_level") or rbody.get("attest_level"),
        # The HTTP verify_link surface returns a `verified` boolean +
        # `signature_present` flag. Both must be true for an honest pass.
        "signature_verified": bool(vbody.get("verified")),
        "signature_present": bool(vbody.get("signature_present")),
        "observed_by": vbody.get("observed_by") or rbody.get("observed_by"),
        "rc": verify_rc,
        "verify_http_code": (verify_resp or {}).get("http_code") if isinstance(verify_resp, dict) else 0,
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
        # Continuation 6: the HTTP verify_link endpoint returns both a
        # `verified` boolean (signature crypto-verified against the
        # signing daemon's pubkey) and a `signature_present` flag
        # (a 64-byte ed25519 signature exists in `memory_links`).
        # Both must be true: signature_present=False would mean the
        # daemon stored an unsigned link; verified=False would mean
        # the bytes are present but invalid.
        if not rec.get("signature_present"):
            passed = False
            reasons.append(f"{label}: signature_present=False (expected True)")
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
