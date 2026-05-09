#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 67 — Grok-driven dialog loop.

openclaw asks "what's M's status?" via memory_notify → hermes Grok-4.2-reasoning
answers via memory_store → openclaw reads → openclaw asks follow-up → >=3 turns;
verify each turn's metadata.reasoning is populated and timestamps are monotonic.
"""
import sys, pathlib, json, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid
from grok_driver import grok_chat  # noqa: E402

SCENARIO_ID = "67"
TURNS = 3


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    suffix = new_uuid()[:6]
    OPEN = f"ai:s67-openclaw-{suffix}"
    HERM = f"ai:s67-hermes-{suffix}"
    ns = f"s67-{suffix}"
    correlation = new_uuid("dlg-")

    # Seed M on hermes for the question to refer to.
    log("phase A: seed memory M on hermes")
    _, seed = h.write_memory(h.node2_ip, HERM, ns, title="M",
                             content="M is the project Apollo launch readiness review.",
                             include_status=True)
    m_id = (seed or {}).get("body", {}).get("id") if isinstance(seed, dict) else None

    turns: list[dict] = []
    last_q = "What is M's status right now?"

    for turn_idx in range(TURNS):
        log(f"--- turn {turn_idx} (openclaw asks) ---")
        # openclaw drafts the message via Grok 4.2 reasoning.
        ask = grok_chat(prompt=last_q,
                        system_msg="You are openclaw, asking hermes about memory M.")
        # v0.7 notify contract: target_agent_id (not "to"); payload (or
        # `content` alias) instead of "body".
        notify_body = {
            "target_agent_id": HERM, "title": f"q-{turn_idx}", "payload": ask["text"],
            "metadata": {
                "scenario": SCENARIO_ID, "turn": turn_idx,
                "correlation_id": correlation,
                "reasoning": ask["reasoning"], "model": ask["model"],
            },
        }
        rc_n, _ = h.http_on(h.node1_ip, "POST", "/api/v1/notify",
                            body=notify_body, agent_id=OPEN, include_status=True)
        h.settle(4, "delivery")

        # hermes drafts a reply via Grok and memory_stores it.
        reply = grok_chat(prompt=ask["text"],
                          system_msg="You are hermes. M is: project Apollo launch readiness review.")
        _, sd = h.write_memory(
            h.node2_ip, HERM, ns, title=f"a-{turn_idx}",
            content=reply["text"], include_status=True,
            metadata={"correlation_id": correlation, "turn": turn_idx,
                      "reasoning": reply["reasoning"], "model": reply["model"]},
        )
        a_id = (sd or {}).get("body", {}).get("id") if isinstance(sd, dict) else None

        turns.append({
            "turn": turn_idx,
            "q": ask["text"][:160],
            "q_reasoning_chars": len(ask["reasoning"] or ""),
            "a": reply["text"][:160],
            "a_reasoning_chars": len(reply["reasoning"] or ""),
            "a_id": a_id,
            "ts_ns": time.time_ns(),
        })
        last_q = f"Follow-up {turn_idx + 1}: tell me more about " + reply["text"][:40]

    reasons: list[str] = []
    passed = True
    if len(turns) < 3:
        passed = False; reasons.append(f"only {len(turns)} turns")
    monotonic = all(turns[i]["ts_ns"] < turns[i+1]["ts_ns"] for i in range(len(turns)-1))
    if not monotonic:
        passed = False; reasons.append("turn timestamps not monotonic")
    for t in turns:
        if t["q_reasoning_chars"] < 10:
            passed = False; reasons.append(f"turn {t['turn']}: q reasoning empty")
        if t["a_reasoning_chars"] < 10:
            passed = False; reasons.append(f"turn {t['turn']}: a reasoning empty")
        if not t["a_id"]:
            passed = False; reasons.append(f"turn {t['turn']}: a_id missing")

    h.emit(passed=passed, reason="; ".join(reasons),
           turns=turns, correlation_id=correlation, m_id=m_id,
           reasons=reasons)


if __name__ == "__main__":
    main()
