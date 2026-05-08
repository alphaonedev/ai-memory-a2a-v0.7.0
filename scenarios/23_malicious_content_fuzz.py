#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 23 — Malicious memory content fuzzing.

Four adversarial payloads, each round-trip verified on node-1:
  a) SQL-like string — stored literally, no server-side SQLi.
  b) HTML/script — stored literally, no render eval.
  c) oversize ~1 MB — accept + round-trip faithfully OR clean 4xx reject.
  d) unicode + zero-width + RTL bytes — byte-for-byte round-trip.

Exploits Python's ability to build these payloads cleanly (vs. bash
shell-quoting which made this scenario a hazard).
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log


SCENARIO_ID = "23"
NS = "scenario23-fuzz"

PAYLOADS = [
    ("sql", "fuzz-sql",
     "Robert'); DROP TABLE memories; -- <-- literal, do not execute",
     "faithful"),
    ("html", "fuzz-html",
     '<script>alert("xss-probe")</script><img src=x onerror="alert(1)"/>',
     "faithful"),
    ("oversize", "fuzz-oversize", "A" * 1_048_576, "reject_or_faithful"),
    ("unicode", "fuzz-unicode",
     "\u202Evoila\u200B kö-é-\u00A0\U0001F600 end",
     "faithful"),
]


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    reasons: list[str] = []
    passed = True
    results: dict[str, dict] = {}

    for key, title, content, expect in PAYLOADS:
        log(f"payload {key}: {len(content)} bytes")
        rc, write_doc = h.write_memory(
            h.node1_ip, "ai:alice", NS,
            title=title, content=content, tier="long",
            metadata={"fuzz": key},
            include_status=True,
        )
        wcode = (write_doc or {}).get("http_code", 0) if isinstance(write_doc, dict) else 0

        # Read back.
        _, resp = h.list_memories(h.node1_ip, NS, limit=20)
        round_content = ""
        if isinstance(resp, dict):
            for m in resp.get("memories") or []:
                if isinstance(m, dict) and (m.get("title") or "") == title:
                    round_content = m.get("content") or ""
                    break

        results[key] = {
            "write_http": wcode,
            "input_bytes": len(content),
            "roundtrip_bytes": len(round_content),
        }

        if expect == "faithful":
            if wcode not in (200, 201):
                passed = False
                reasons.append(f"{key}: write returned HTTP {wcode}")
            elif round_content != content:
                passed = False
                reasons.append(
                    f"{key}: round-trip content corrupted (in={len(content)}B out={len(round_content)}B)"
                )
        elif expect == "reject_or_faithful":
            if wcode in (413, 400, 422, 431):
                log(f"  {key}: server rejected oversize with HTTP {wcode} (acceptable)")
            elif wcode in (200, 201):
                if round_content != content:
                    passed = False
                    reasons.append(
                        f"{key}: accepted but round-trip corrupted (silent truncation/re-encoding)"
                    )
                else:
                    log(f"  {key}: accepted AND round-tripped faithfully")
            else:
                passed = False
                reasons.append(f"{key}: unexpected HTTP {wcode}")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        payloads=results,
        payloads_note="accept+faithful OR 4xx reject both acceptable for oversize",
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
