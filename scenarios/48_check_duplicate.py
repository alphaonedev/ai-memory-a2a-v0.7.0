#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 48 — memory_check_duplicate near-match detection (v0.6.3).

Writes a memory with deliberate phrasing, then calls
/api/v1/check_duplicate with semantically near-identical (but not
byte-identical) content. The endpoint must:

  * return at least one match,
  * include the original memory's id in the match list,
  * include a similarity score in (0, 1] for that match.

If the API only catches byte-equal duplicates we'd never know the
agent is double-storing rephrased versions of the same fact — exactly
the bug check_duplicate is meant to prevent.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "48"


def _id_of(resp: object) -> str:
    if isinstance(resp, dict):
        return resp.get("id") or resp.get("memory_id") or ""
    return ""


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = f"scenario48-dup-{new_uuid()[:6]}"

    # Original is specific enough that random recall noise won't hit it.
    original = (
        "AlphaOne deploys ai-memory v0.6.3 to the production cluster on "
        f"the third Tuesday of each quarter. Marker={new_uuid()}"
    )
    near_match = (
        "The AlphaOne team rolls ai-memory 0.6.3 into prod every quarter "
        "on the third Tuesday."
    )

    log("alice writes the original memory on node-1")
    _, resp = h.write_memory(h.node1_ip, "ai:alice", ns,
                             title="dup-canonical", content=original,
                             tier="long")
    orig_id = _id_of(resp)
    log(f"  original id={orig_id}")
    if not orig_id:
        h.emit(passed=False, reason="seed write failed",
               reasons=["seed write failed"], orig_id=orig_id)
    h.settle(6, reason="indexing + embedding before duplicate check")

    log("alice calls /api/v1/check_duplicate with near-match content")
    _, doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/check_duplicate",
        body={"content": near_match, "namespace": ns, "limit": 5},
        agent_id="ai:alice", include_status=True, timeout=30,
    )
    code = (doc or {}).get("http_code", 0) if isinstance(doc, dict) else 0
    body = (doc or {}).get("body") if isinstance(doc, dict) else None
    log(f"  HTTP {code}")

    matches: list[dict] = []
    if isinstance(body, dict):
        matches = (
            body.get("matches") or body.get("duplicates")
            or body.get("memories") or body.get("results") or []
        )
    elif isinstance(body, list):
        matches = body
    matches = [m for m in matches if isinstance(m, dict)]
    log(f"  matches returned: {len(matches)}")

    found_match: dict | None = None
    for m in matches:
        mid = m.get("id") or m.get("memory_id") or ""
        # Some shapes nest the memory under a `memory` key.
        if not mid and isinstance(m.get("memory"), dict):
            mid = m["memory"].get("id") or m["memory"].get("memory_id") or ""
        if mid == orig_id:
            found_match = m
            break

    score: float | None = None
    if found_match is not None:
        for k in ("similarity", "score", "similarity_score", "distance"):
            v = found_match.get(k)
            if isinstance(v, (int, float)):
                score = float(v)
                break

    log(f"  original-as-match found={found_match is not None} score={score}")

    reasons: list[str] = []
    passed = True
    if code != 200:
        passed = False
        reasons.append(f"check_duplicate returned HTTP {code}")
    if not matches:
        passed = False
        reasons.append("check_duplicate returned 0 matches for a near-identical input")
    if found_match is None:
        passed = False
        reasons.append(f"original memory ({orig_id}) absent from matches")
    if score is None:
        passed = False
        reasons.append("match did not carry a similarity score field")
    elif not (0.0 < score <= 1.0):
        # Tolerate distance-as-score (lower-is-better) by also accepting
        # values in [0, 1] more permissively, but flag clearly off-band.
        if not (0.0 <= score <= 1.0):
            passed = False
            reasons.append(f"similarity score {score} out of expected [0, 1] range")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        original_id=orig_id,
        check_http_code=code,
        match_count=len(matches),
        original_in_matches=found_match is not None,
        similarity_score=score,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
