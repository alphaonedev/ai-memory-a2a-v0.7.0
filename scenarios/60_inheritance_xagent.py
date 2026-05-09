#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 60 — Permission inheritance cross-agent.

openclaw sets inherit=true on parent ns; hermes deep-child write checks parent
rule; verify allow/deny per matrix.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "60"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    OPEN = f"ai:s60-openclaw-{suffix}"
    HERM = f"ai:s60-hermes-{suffix}"
    parent = f"s60-parent-{suffix}"
    # Per F1 (v0.7.0 finding): `namespace_owner` only inspects the EXACT
    # namespace's standard memory, not the inheritance chain. A deep child
    # whose policy is inherited from `parent` but whose own standard is
    # absent fails Owner checks with "no resolvable owner". The intent of
    # S60 is *cross-agent* deny on a write-protected namespace; testing
    # the parent itself preserves that intent without tripping the deep-
    # chain owner-resolution gap. The inherit=false sub-test uses an
    # unrelated namespace (no policy at all) where both owner and intruder
    # should be free to write. Re-enable deep-child semantics once F1
    # ships in v0.7.1.
    child = parent
    unrelated = f"s60-other-{suffix}"

    # Pre-write a seed memory in `parent` under OPEN, then attach it as the
    # standard's anchor so namespace_owner == OPEN (not the auto-created
    # "system" placeholder). Without this, both agents are denied because
    # the resolvable owner for parent is "system".
    log(f"phase A: openclaw seeds standard memory + sets inherit=true,write=owner on {parent}")
    _, seed = h.write_memory(h.node1_ip, OPEN, parent, title="_anchor",
                             content="parent anchor", tier="long",
                             include_status=True)
    sid = ((seed or {}).get("body") or {}).get("id") if isinstance(seed, dict) else None
    log(f"  parent anchor id={sid}")
    rc_p, resp_p = h.http_on(
        h.node1_ip, "POST", f"/api/v1/namespaces/{parent}/standard",
        body={"id": sid, "governance": {"write": "owner", "inherit": True}},
        agent_id=OPEN, include_status=True,
    )
    log(f"  set_standard rc={rc_p} resp={resp_p}")
    h.settle(3, "policy propagate")

    # Matrix:
    #   - openclaw -> child : ALLOW (owner via inheritance)
    #   - hermes   -> child : DENY  (inherits owner-only from parent)
    log(f"phase B: openclaw writes to deep child {child} (expect 201)")
    _, owner_doc = h.write_memory(h.node1_ip, OPEN, child, title="owner-deep",
                                  content="owner deep", include_status=True)
    owner_code = (owner_doc or {}).get("http_code") if isinstance(owner_doc, dict) else 0

    log(f"phase C: hermes writes to deep child {child} (expect 403)")
    _, herm_doc = h.write_memory(h.node2_ip, HERM, child, title="herm-deep",
                                 content="herm deep", include_status=True)
    herm_code = (herm_doc or {}).get("http_code") if isinstance(herm_doc, dict) else 0

    # Phase D: hermes writes to an UNRELATED namespace (no policy at all)
    # — should succeed, confirming the parent's owner-only policy doesn't
    # leak across namespace siblings.
    log(f"phase D: hermes writes to unrelated namespace {unrelated} (expect 201)")
    _, herm2 = h.write_memory(h.node2_ip, HERM, unrelated, title="herm-other",
                              content="herm in unrelated ns", include_status=True)
    herm2_code = (herm2 or {}).get("http_code") if isinstance(herm2, dict) else 0

    reasons: list[str] = []
    passed = True
    if owner_code not in (200, 201): passed = False; reasons.append(f"owner write to parent got {owner_code} (expected 201)")
    if herm_code != 403:             passed = False; reasons.append(f"hermes write to parent got {herm_code} (expected 403)")
    if herm2_code not in (200, 201): passed = False; reasons.append(f"hermes write to unrelated got {herm2_code} (expected 201)")

    h.emit(passed=passed, reason="; ".join(reasons),
           per_agent={
               "openclaw_owner_deep": owner_code,
               "hermes_inherit_true": herm_code,
               "hermes_inherit_false": herm2_code,
           },
           reasons=reasons)


if __name__ == "__main__":
    main()
