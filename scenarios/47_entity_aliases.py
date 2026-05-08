#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 47 — memory_entity_register alias idempotence (v0.6.3).

Entities are first-class in v0.6.3: each entity has a canonical_name +
namespace and a set of aliases. memory_entity_register must be
idempotent on (canonical_name, namespace) — calling it twice with
DIFFERENT alias sets produces the same entity_id, with the union of
aliases attached.

Then memory_entity_get_by_alias must resolve every alias (from both
calls) back to that single entity.

Without alias-idempotence, we'd get a duplicate entity per call,
splitting the entity's neighborhood and silently fragmenting the KG.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "47"


def _entity_id(body: object) -> str:
    if isinstance(body, dict):
        e = body.get("entity") or body
        if isinstance(e, dict):
            return e.get("id") or e.get("entity_id") or ""
    return ""


def _aliases_of(body: object) -> list[str]:
    if isinstance(body, dict):
        e = body.get("entity") or body
        if isinstance(e, dict):
            v = e.get("aliases") or []
            if isinstance(v, list):
                return [str(x) for x in v]
    return []


def _register(h: Harness, ip: str, canonical: str, ns: str,
              aliases: list[str]) -> tuple[int, dict | None]:
    body = {
        "canonical_name": canonical,
        "namespace": ns,
        "aliases": aliases,
    }
    _, doc = h.http_on(ip, "POST", "/api/v1/entities",
                       body=body, agent_id="ai:alice", include_status=True)
    code = (doc or {}).get("http_code", 0) if isinstance(doc, dict) else 0
    payload = (doc or {}).get("body") if isinstance(doc, dict) else None
    return code, payload if isinstance(payload, dict) else None


def _get_by_alias(h: Harness, ip: str, alias: str, ns: str) -> tuple[int, str]:
    """Returns (http_code, resolved_entity_id)."""
    import urllib.parse as _u
    q = _u.urlencode({"alias": alias, "namespace": ns})
    _, doc = h.http_on(ip, "GET", f"/api/v1/entities/by-alias?{q}",
                       include_status=True)
    code = (doc or {}).get("http_code", 0) if isinstance(doc, dict) else 0
    body = (doc or {}).get("body") if isinstance(doc, dict) else None
    return code, _entity_id(body)


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    ns = f"scenario47-ent-{suffix}"
    canonical = f"AlphaOne-Project-{suffix}"
    aliases_a = ["a1-project", "alphaone-proj"]
    aliases_b = ["alpha1-proj", "ao-project"]

    log(f"register canonical={canonical!r} ns={ns} aliases={aliases_a}")
    code_a, body_a = _register(h, h.node1_ip, canonical, ns, aliases_a)
    eid_a = _entity_id(body_a)
    log(f"  HTTP {code_a} entity_id={eid_a!r}")

    log(f"register again, same canonical, different aliases={aliases_b}")
    code_b, body_b = _register(h, h.node1_ip, canonical, ns, aliases_b)
    eid_b = _entity_id(body_b)
    seen_aliases = _aliases_of(body_b)
    log(f"  HTTP {code_b} entity_id={eid_b!r} aliases-on-entity={seen_aliases}")

    h.settle(4, reason="entity propagation")

    # Resolve every alias; all 4 must map to the same entity_id.
    resolutions: dict[str, dict] = {}
    for alias in (*aliases_a, *aliases_b):
        rcode, rid = _get_by_alias(h, h.node2_ip, alias, ns)
        resolutions[alias] = {"http_code": rcode, "entity_id": rid}
        log(f"  by-alias({alias!r}) HTTP {rcode} -> {rid!r}")

    reasons: list[str] = []
    passed = True
    if code_a not in (200, 201):
        passed = False
        reasons.append(f"first register returned HTTP {code_a}")
    if code_b not in (200, 201):
        passed = False
        reasons.append(f"second register returned HTTP {code_b}")
    if not eid_a:
        passed = False
        reasons.append("first register did not return an entity_id")
    if eid_a and eid_b and eid_a != eid_b:
        passed = False
        reasons.append(
            f"non-idempotent: second register returned a DIFFERENT id "
            f"({eid_a} vs {eid_b}) — duplicates the entity"
        )
    # The second call's response should expose the union of aliases.
    union = set(aliases_a) | set(aliases_b)
    missing_in_union = [a for a in union if a not in seen_aliases]
    if missing_in_union:
        passed = False
        reasons.append(f"second register did not surface union aliases; missing {missing_in_union}")
    # Every alias must resolve back to the canonical entity.
    target_eid = eid_a or eid_b
    for alias, r in resolutions.items():
        if r["http_code"] != 200:
            passed = False
            reasons.append(f"by-alias({alias!r}) HTTP {r['http_code']}")
        elif target_eid and r["entity_id"] != target_eid:
            passed = False
            reasons.append(
                f"by-alias({alias!r}) resolved to {r['entity_id']!r}, expected {target_eid!r}"
            )

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        canonical=canonical,
        namespace=ns,
        first_register_http_code=code_a,
        second_register_http_code=code_b,
        first_entity_id=eid_a,
        second_entity_id=eid_b,
        aliases_on_entity=seen_aliases,
        alias_resolutions=resolutions,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
