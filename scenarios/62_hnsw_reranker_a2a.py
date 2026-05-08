#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 62 — HNSW + reranker A2A.

openclaw stores 1000 items; hermes recalls topK via semantic; topK consistency
vs ground-truth.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "62"
N = 1000
TOPK = 10

# Ground-truth queries that exactly match a small set of seed contents.
GROUND_TRUTH = [
    ("photosynthesis", "Photosynthesis converts light into chemical energy in chloroplasts."),
    ("relativity",     "General relativity describes gravity as curvature of spacetime."),
    ("kafka",          "Kafka's The Trial is a study of bureaucratic horror."),
]


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    OPEN, HERM = "ai:openclaw@nyc3:droplet-1", "ai:hermes@nyc3:droplet-2"
    ns = f"s62-{new_uuid()[:6]}"

    log(f"phase A: openclaw stores {N} items including 3 ground-truth seeds")
    # Ground truth seeds
    seed_ids: dict[str, str] = {}
    for q, content in GROUND_TRUTH:
        _, doc = h.write_memory(h.node1_ip, OPEN, ns, title=f"seed-{q}",
                                content=content, include_status=True)
        if isinstance(doc, dict):
            mid = (doc.get("body") or {}).get("id")
            if mid: seed_ids[q] = mid

    # Filler
    def filler(i: int):
        _, _ = h.write_memory(h.node1_ip, OPEN, ns, title=f"f{i}",
                              content=f"filler item {i} {new_uuid()[:8]}",
                              include_status=False)
    h.run_parallel(lambda i: filler(i), [(i,) for i in range(N - len(GROUND_TRUTH))], max_workers=12)
    h.settle(8, "embed + index settle")

    log("phase B: hermes recalls topK via semantic for each ground-truth query")
    recalls: dict[str, dict] = {}
    for q, _ in GROUND_TRUTH:
        rc, resp = h.http_on(
            h.node2_ip, "GET",
            f"/api/v1/memories/recall?namespace={ns}&q={q}&mode=semantic&top_k={TOPK}",
            agent_id=HERM,
        )
        rows = (resp or {}).get("memories", []) if isinstance(resp, dict) else []
        ids = [(m or {}).get("id") for m in rows][:TOPK]
        rank = ids.index(seed_ids[q]) + 1 if seed_ids.get(q) in ids else 0
        recalls[q] = {"top_k": len(ids), "rank_of_seed": rank, "rc": rc}

    reasons: list[str] = []
    passed = True
    for q, rec in recalls.items():
        if rec["rank_of_seed"] == 0:
            passed = False
            reasons.append(f"seed for '{q}' not in top-{TOPK}")
        elif rec["rank_of_seed"] > 3:
            passed = False
            reasons.append(f"seed for '{q}' ranked {rec['rank_of_seed']} (expected <=3)")

    h.emit(passed=passed, reason="; ".join(reasons),
           recalls=recalls, namespace=ns, reasons=reasons)


if __name__ == "__main__":
    main()
