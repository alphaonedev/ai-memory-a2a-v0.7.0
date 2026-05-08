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

    body = {"from": m_id, "to": n_id, "rel_type": "references"}
    rc, resp = h.http_on(src_node, "POST", "/api/v1/links",
                         body=body, agent_id=src_aid, include_status=True)
    body = (resp or {}).get("body") if isinstance(resp, dict) else None
    if not isinstance(body, dict):
        return {"err": f"link create failed rc={rc} resp={resp}"}
    return {
        "from": m_id, "to": n_id,
        "attest_level": body.get("attest_level"),
        "signature": body.get("signature") or "",
        "observed_by": body.get("observed_by"),
        "rc": rc,
    }


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    openclaw, hermes = h.node1_ip, h.node2_ip

    log("phase A: link openclaw→hermes signed by openclaw daemon")
    a = link_and_check(h, openclaw, hermes, "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2")
    log(f"  {a}")

    log("phase B: link hermes→openclaw signed by hermes daemon")
    b = link_and_check(h, hermes, openclaw, "ai:hermes@nyc3:droplet-2", "ai:openclaw@nyc3:droplet-1")
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
        sig = rec.get("signature") or ""
        # Signature comes back base64 or hex; either way the decoded length is 64.
        try:
            import base64, binascii
            try:
                decoded_len = len(base64.b64decode(sig + "=" * (-len(sig) % 4), validate=False))
            except (binascii.Error, ValueError):
                decoded_len = len(bytes.fromhex(sig)) if all(c in "0123456789abcdefABCDEF" for c in sig) else len(sig)
        except Exception:
            decoded_len = len(sig)
        if decoded_len != 64:
            passed = False
            reasons.append(f"{label}: signature decoded len={decoded_len} (expected 64)")
        if rec.get("observed_by") != "daemon":
            passed = False
            reasons.append(f"{label}: observed_by={rec.get('observed_by')} (expected daemon)")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={"openclaw_side": a, "hermes_side": b}, reasons=reasons)


if __name__ == "__main__":
    main()
