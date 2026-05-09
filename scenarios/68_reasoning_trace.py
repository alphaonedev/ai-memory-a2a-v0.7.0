#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 68 — Reasoning-trace persistence.

Grok 4.2 reasoning trace is captured into metadata.reasoning field on store;
verify recall --include-content surfaces the trace.
"""
import os, sys, pathlib, urllib.parse
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid
from grok_driver import grok_chat  # noqa: E402

SCENARIO_ID = "68"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN = f"ai:s68-openclaw-{new_uuid()[:6]}"
    ns = f"s68-{new_uuid()[:6]}"

    log("phase A: ask Grok 4.2 to reason about a problem")
    # Allow generous timeout — `grok-4.20-0309-reasoning` can take >60s on
    # multi-step puzzles; bump to 180s and retry once on transient SSL/Read
    # timeouts before failing the scenario.
    os.environ.setdefault("XAI_TIMEOUT_S", "180")
    out: dict = {}
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            out = grok_chat(
                prompt="If a 12L jug + 7L jug + 5L jug, how do you measure exactly 6 liters?",
                system_msg="Show your reasoning steps explicitly.")
            if out and (out.get("text") or out.get("error")):
                break
        except Exception as exc:  # SSL/network blips
            last_err = exc
            log(f"  grok_chat attempt {attempt+1} raised {type(exc).__name__}: {exc}")
    if not out:
        h.emit(passed=False, reason=f"grok_chat unreachable: {last_err}",
               reasons=[f"grok unreachable: {last_err}"])
        return
    if out.get("error"):
        log(f"  grok error: {out['error']}")

    log("phase B: store with metadata.reasoning")
    _, doc = h.write_memory(
        h.node1_ip, OPEN, ns, title="puzzle-12-7-5",
        content=out["text"], include_status=True,
        metadata={"reasoning": out["reasoning"], "model": out["model"]},
    )
    mid = (doc or {}).get("body", {}).get("id") if isinstance(doc, dict) else None
    h.settle(3, "persist")

    log("phase C: recall --include-content; expect reasoning visible")
    q = urllib.parse.urlencode({"namespace": ns, "include_content": "true",
                                "include_metadata": "true", "limit": 10})
    rc, resp = h.http_on(h.node1_ip, "GET", f"/api/v1/memories?{q}",
                         agent_id=OPEN)
    rows = (resp or {}).get("memories", []) if isinstance(resp, dict) else []
    found_reasoning = ""
    for m in rows:
        if (m or {}).get("id") == mid:
            found_reasoning = ((m or {}).get("metadata") or {}).get("reasoning") or ""
            break

    reasons: list[str] = []
    passed = True
    if not mid: passed = False; reasons.append("store failed")
    if len(out["reasoning"] or "") < 10:
        passed = False; reasons.append("Grok returned no reasoning trace")
    if len(found_reasoning) < 10:
        passed = False; reasons.append("recall did not surface metadata.reasoning")
    elif found_reasoning != out["reasoning"]:
        passed = False
        reasons.append(f"reasoning altered on round-trip (orig={len(out['reasoning'])}B, recalled={len(found_reasoning)}B)")

    h.emit(passed=passed, reason="; ".join(reasons),
           memory_id=mid,
           orig_reasoning_chars=len(out["reasoning"] or ""),
           recalled_reasoning_chars=len(found_reasoning),
           reasons=reasons)


if __name__ == "__main__":
    main()
