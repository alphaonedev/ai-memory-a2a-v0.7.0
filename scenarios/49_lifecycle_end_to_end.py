#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 49 — full memory lifecycle end-to-end.

Walks one memory through every transition in the v0.6.3 lifecycle and
asserts each leaves the right audit trail:

  1. store (mid)        — POST /api/v1/memories tier=mid
  2. access             — GET  /api/v1/memories/{id} (touches access_count)
  3. consolidate        — POST /api/v1/consolidate with sibling memories
  4. promote (long)     — POST /api/v1/memories/{id}/promote
  5. expire             — POST /api/v1/memories/{id}/forget (or
                          equivalent expiry) → marks expired
  6. archive            — POST /api/v1/archive
  7. restore            — POST /api/v1/archive/{id}/restore
  8. purge              — DELETE /api/v1/archive/{id} (hard purge)

After each transition, we either fetch the memory or check archive
listings and assert the audit fields advanced. Idempotent: ns is
suffixed with a per-run UUID.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "49"


def _id_of(resp: object) -> str:
    if isinstance(resp, dict):
        return resp.get("id") or resp.get("memory_id") or ""
    return ""


def _get_memory_doc(h: Harness, ip: str, mid: str) -> dict | None:
    """Fetch a memory by id; v0.7 wraps it as `{memory: {...}, links: [...]}`."""
    _, doc = h.get_memory(ip, mid)
    if isinstance(doc, dict):
        m = doc.get("memory")
        if isinstance(m, dict):
            return m
        # 404 envelopes look like {"error": "not found"} — return None so
        # callers can distinguish "absent" from "present-but-empty-shape".
        if "error" in doc and "memory" not in doc:
            return None
        return doc
    return None


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    ns = f"scenario49-lifecycle-{suffix}"
    audit: dict[str, dict] = {}

    # ---- 1. store ----
    agent = f"ai:s49-{suffix}"
    log("step 1: store M1 at tier=mid")
    _, r = h.write_memory(h.node1_ip, agent, ns,
                          title="lc-main", content=f"lifecycle-{suffix}",
                          tier="mid")
    m1 = _id_of(r)
    log(f"  M1={m1}")
    if not m1:
        h.emit(passed=False, reason="seed store failed",
               reasons=["initial store returned no id"])
    h.settle(3, reason="post-store")
    after_store = _get_memory_doc(h, h.node1_ip, m1)
    audit["store"] = {"tier": (after_store or {}).get("tier"),
                      "exists": after_store is not None}

    # ---- 2. access ----
    # v0.7: GET /memories/{id} does NOT bump access_count (read-only fetch).
    # Only `recall` calls `touch()` which increments access_count. The
    # endpoint param is `context` (not `q`); pass the content marker so
    # the FTS query reaches the seed.
    log("step 2: access M1 (3x recall to bump counter)")
    import urllib.parse as _u
    q = _u.urlencode({"namespace": ns, "context": f"lifecycle-{suffix}", "limit": 5})
    for _ in range(3):
        h.http_on(h.node1_ip, "GET", f"/api/v1/recall?{q}", agent_id=agent)
    h.settle(2, reason="access counter flush")
    after_access = _get_memory_doc(h, h.node1_ip, m1)
    access_ct = 0
    if after_access:
        for k in ("access_count", "accesses", "read_count"):
            v = after_access.get(k)
            if isinstance(v, int):
                access_ct = v
                break
    audit["access"] = {"access_count": access_ct}

    # ---- 3. consolidate with siblings ----
    # v0.7 consolidate DELETES the source memories and creates a NEW
    # consolidated row. Use the new id as the "primary" for the rest of
    # the lifecycle, not the original M1 which no longer exists.
    log("step 3: write 2 sibling memories + consolidate")
    sib_ids: list[str] = [m1]
    for i in (1, 2):
        _, sr = h.write_memory(h.node1_ip, agent, ns,
                               title=f"lc-sib-{i}",
                               content=f"sibling-{i}-{suffix}", tier="mid")
        sid = _id_of(sr)
        if sid:
            sib_ids.append(sid)
    h.settle(3, reason="siblings settle")
    _, cdoc = h.http_on(
        h.node1_ip, "POST", "/api/v1/consolidate",
        body={"ids": sib_ids, "title": "lc-consolidated",
              "summary": f"lifecycle consolidation {suffix}", "namespace": ns},
        agent_id=agent, include_status=True,
    )
    cons_code = (cdoc or {}).get("http_code", 0) if isinstance(cdoc, dict) else 0
    cbody = (cdoc or {}).get("body") if isinstance(cdoc, dict) else None
    cons_id = ""
    if isinstance(cbody, dict):
        cons_id = cbody.get("id") or cbody.get("memory_id") or cbody.get("consolidated_memory_id") or ""
    audit["consolidate"] = {"http_code": cons_code, "consolidated_id": cons_id,
                            "sibling_count": len(sib_ids)}
    h.settle(4, reason="consolidation index")
    # Pivot: subsequent lifecycle steps target the consolidated row.
    if cons_id:
        m1 = cons_id
        log(f"  pivot: m1 -> consolidated id {m1}")

    # ---- 4. promote to long ----
    log("step 4: promote M1 to tier=long")
    # v0.7 promote takes no body (the path verb means promote-to-long).
    _, pdoc = h.http_on(h.node1_ip, "POST", f"/api/v1/memories/{m1}/promote",
                        agent_id=agent, include_status=True)
    p_code = (pdoc or {}).get("http_code", 0) if isinstance(pdoc, dict) else 0
    h.settle(3, reason="promote settle")
    after_promote = _get_memory_doc(h, h.node1_ip, m1)
    audit["promote"] = {"http_code": p_code,
                        "tier_after": (after_promote or {}).get("tier")}

    # ---- 5. expire (set expires_at to past via update) ----
    # v0.7 has no per-id /forget endpoint (forget is namespace-scoped). The
    # equivalent for "soft-expire this row" is to PUT expires_at to the past.
    log("step 5: expire M1 via PUT expires_at=past")
    past_iso = "2020-01-01T00:00:00Z"
    _, fdoc = h.http_on(h.node1_ip, "PUT", f"/api/v1/memories/{m1}",
                        body={"expires_at": past_iso}, agent_id=agent,
                        include_status=True)
    f_code = (fdoc or {}).get("http_code", 0) if isinstance(fdoc, dict) else 0
    h.settle(3, reason="expire settle")
    after_forget = _get_memory_doc(h, h.node1_ip, m1)
    expired_marker = False
    if isinstance(after_forget, dict):
        for k in ("expired_at", "forgotten_at", "expires_at", "tombstoned_at"):
            v = after_forget.get(k)
            if v:
                expired_marker = True
                break
    audit["expire"] = {"http_code": f_code, "marker_set": expired_marker,
                       "still_visible": after_forget is not None}

    # ---- 6. archive ----
    log("step 6: archive M1 via POST /api/v1/archive")
    _, adoc = h.http_on(h.node1_ip, "POST", "/api/v1/archive",
                        body={"ids": [m1], "reason": "scenario-49 lifecycle"},
                        agent_id=agent, include_status=True)
    a_code = (adoc or {}).get("http_code", 0) if isinstance(adoc, dict) else 0
    h.settle(4, reason="archive settle")
    _, alist = h.http_on(h.node1_ip, "GET", "/api/v1/archive?limit=50")
    archived_visible = False
    if isinstance(alist, dict):
        for m in (alist.get("memories") or alist.get("archived") or []):
            if isinstance(m, dict) and (m.get("id") == m1 or m.get("memory_id") == m1):
                archived_visible = True
                break
    audit["archive"] = {"http_code": a_code, "in_archive_listing": archived_visible}

    # ---- 7. restore ----
    log("step 7: restore M1")
    _, rdoc = h.http_on(h.node1_ip, "POST", f"/api/v1/archive/{m1}/restore",
                        agent_id=agent, include_status=True)
    r_code = (rdoc or {}).get("http_code", 0) if isinstance(rdoc, dict) else 0
    h.settle(3, reason="restore settle")
    after_restore = _get_memory_doc(h, h.node1_ip, m1)
    audit["restore"] = {"http_code": r_code,
                        "active_after_restore": after_restore is not None}

    # ---- 8. purge (hard delete from archive) ----
    log("step 8: archive again then hard-purge")
    h.http_on(h.node1_ip, "POST", "/api/v1/archive",
              body={"ids": [m1], "reason": "scenario-49 pre-purge"},
              agent_id=agent, include_status=True)
    h.settle(2, reason="re-archive settle")
    # v0.7 purge: bulk DELETE /api/v1/archive with body {ids:[...]}
    _, pdoc2 = h.http_on(h.node1_ip, "DELETE", "/api/v1/archive",
                         body={"ids": [m1]}, agent_id=agent, include_status=True)
    purge_code = (pdoc2 or {}).get("http_code", 0) if isinstance(pdoc2, dict) else 0
    h.settle(3, reason="purge settle")
    _, post_purge = h.http_on(h.node1_ip, "GET", "/api/v1/archive?limit=50")
    still_in_archive = False
    if isinstance(post_purge, dict):
        for m in (post_purge.get("memories") or post_purge.get("archived") or []):
            if isinstance(m, dict) and (m.get("id") == m1 or m.get("memory_id") == m1):
                still_in_archive = True
                break
    audit["purge"] = {"http_code": purge_code, "still_in_archive": still_in_archive}

    # ---- verdict ----
    reasons: list[str] = []
    passed = True
    if not audit["store"]["exists"]:
        passed = False
        reasons.append("store: memory not visible after write")
    if audit["store"]["tier"] != "mid":
        passed = False
        reasons.append(f"store: tier={audit['store']['tier']!r}, expected 'mid'")
    if audit["access"]["access_count"] < 1:
        passed = False
        reasons.append("access: access_count not incremented")
    if audit["consolidate"]["http_code"] not in (200, 201):
        passed = False
        reasons.append(f"consolidate: HTTP {audit['consolidate']['http_code']}")
    if not audit["consolidate"]["consolidated_id"]:
        passed = False
        reasons.append("consolidate: no consolidated_id returned")
    if audit["promote"]["http_code"] not in (200, 204):
        passed = False
        reasons.append(f"promote: HTTP {audit['promote']['http_code']}")
    if audit["promote"]["tier_after"] != "long":
        passed = False
        reasons.append(f"promote: tier={audit['promote']['tier_after']!r}, expected 'long'")
    if audit["expire"]["http_code"] not in (200, 202, 204):
        passed = False
        reasons.append(f"expire: HTTP {audit['expire']['http_code']}")
    if audit["archive"]["http_code"] not in (200, 201, 202, 204):
        passed = False
        reasons.append(f"archive: HTTP {audit['archive']['http_code']}")
    if not audit["archive"]["in_archive_listing"]:
        passed = False
        reasons.append("archive: M1 not visible in /api/v1/archive listing")
    if audit["restore"]["http_code"] not in (200, 204):
        passed = False
        reasons.append(f"restore: HTTP {audit['restore']['http_code']}")
    if not audit["restore"]["active_after_restore"]:
        passed = False
        reasons.append("restore: M1 not active again after restore")
    if audit["purge"]["http_code"] not in (200, 202, 204):
        passed = False
        reasons.append(f"purge: HTTP {audit['purge']['http_code']}")
    if audit["purge"]["still_in_archive"]:
        passed = False
        reasons.append("purge: M1 still visible in archive after hard purge")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        m1_id=m1,
        namespace=ns,
        audit=audit,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
