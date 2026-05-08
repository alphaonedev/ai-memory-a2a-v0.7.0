#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 18 — Semantic query expansion.

alice + bob write semantically-related-but-keyword-distinct memories.
charlie issues /api/v1/recall with a semantic prompt and must see both
markers. Tests HNSW + embedder across the federated mesh.

RecallQuery reads the query string via `?context=<text>` (per
ai-memory-mcp src/models.rs:210).

v3r25 evidence showed alice's memory surfaced on charlie but bob's
did not (asymmetric). Root cause was fanout-or-embed race on node-3
for the second write — 15 s flat-wait was enough for alice (first
write, more post-settle time) but sometimes not for bob (written
closer to the settle boundary).

Fix in this iteration: poll for BOTH rows to be present on node-3's
list_memories before issuing the semantic recall. Adds an upper
wait of ~30 s (much less in practice). Without both rows present on
the querying node, the semantic test has no chance.
"""

import sys
import time
import urllib.parse
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "18"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    ns = "scenario18-semantic"
    tag_a = f"alice-sunrise-{new_uuid()[:8]}"
    tag_b = f"bob-daybreak-{new_uuid()[:8]}"
    content_a = f"Lyra starts every sunrise by walking the hills before most people are awake. Marker={tag_a}"
    content_b = f"Before dawn Lyra enjoys brisk uphill strides along the ridge line trails. Marker={tag_b}"
    query = "morning outdoor exercise routine"

    log("alice writes A on node-1")
    h.write_memory(h.node1_ip, "ai:alice", ns, title="dawn-walk",
                   content=content_a, tier="long")
    log("bob writes B on node-2")
    h.write_memory(h.node2_ip, "ai:bob", ns, title="ridge-strides",
                   content=content_b, tier="long")

    # v0.6.2 (S18 iteration): poll for BOTH writes to be visible on node-3
    # BEFORE issuing the semantic recall. Previously a 15 s flat settle
    # left a race window where the second write could be missing or
    # unembedded on charlie's node, asymmetrically failing the second
    # marker while the first passed.
    log("polling node-3 for both writes to propagate (max 30 s)")
    saw_a_pre = 0
    saw_b_pre = 0
    for attempt in range(30):
        _, listing = h.http_on(h.node3_ip, "GET",
                               f"/api/v1/memories?namespace={ns}&limit=50")
        mems = []
        if isinstance(listing, dict):
            mems = listing.get("memories") or []
        saw_a_pre = sum(1 for m in mems
                        if isinstance(m, dict) and tag_a in (m.get("content") or ""))
        saw_b_pre = sum(1 for m in mems
                        if isinstance(m, dict) and tag_b in (m.get("content") or ""))
        if saw_a_pre >= 1 and saw_b_pre >= 1:
            log(f"  both writes visible after {attempt+1} s")
            break
        time.sleep(1)
    else:
        log(f"  WARN: after 30 s list on node-3 still has alice={saw_a_pre} bob={saw_b_pre}")

    # Additional 3 s for embedding refresh + HNSW index update on node-3.
    # sync_push completes the insert under the DB lock, then regenerates
    # the embedding + updates HNSW asynchronously (handlers.rs:3039+) —
    # short pad above that async window.
    h.settle(3, reason="embedder + HNSW catch-up")

    # v0.6.2 (S18 iteration): direct DB probe on node-3. If the row has
    # LENGTH(embedding)=0 or NULL, we know sync_push's embedding_refresh
    # didn't run OR failed silently for that row. This is the single
    # question that distinguishes "embedding not set on peer" from
    # "embedding set but cosine < 0.3".
    probe_sql = (
        "SELECT title, COALESCE(LENGTH(embedding), 0), "
        "(CASE WHEN embedding IS NULL THEN 'NULL' ELSE 'BYTES' END) "
        f"FROM memories WHERE namespace = '{ns}' ORDER BY title;"
    )
    r = h.ssh_exec(
        h.node3_ip,
        f"sqlite3 -cmd '.timeout 5000' /var/lib/ai-memory/a2a.db \"{probe_sql}\"",
        timeout=15,
    )
    embedding_diag = (r.stdout or "").strip().replace("\n", " | ")
    log(f"  node-3 DB embedding probe: {embedding_diag!r}")

    log("charlie queries on node-3 with semantically-related prompt")
    q = urllib.parse.urlencode({"context": query, "namespace": ns, "limit": 20})
    _, resp = h.http_on(h.node3_ip, "GET", f"/api/v1/recall?{q}")
    memories = []
    recall_mode = None
    if isinstance(resp, dict):
        memories = resp.get("memories") or []
        recall_mode = resp.get("mode")  # "hybrid" | "keyword" per PR #366
    elif isinstance(resp, list):
        memories = resp

    def count_marker(tag: str) -> int:
        return sum(1 for m in memories
                   if isinstance(m, dict) and tag in (m.get("content") or ""))

    saw_a = count_marker(tag_a)
    saw_b = count_marker(tag_b)
    log(f"  recall mode={recall_mode} returned {len(memories)} rows")
    log(f"  charlie sees alice's memory: {saw_a} (expected >=1)")
    log(f"  charlie sees bob's memory: {saw_b} (expected >=1)")

    reasons: list[str] = []
    passed = True
    if saw_a < 1:
        passed = False
        reasons.append("semantic query did not surface alice's memory")
    if saw_b < 1:
        passed = False
        reasons.append("semantic query did not surface bob's memory")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        query=query,
        recall_mode=recall_mode,
        rows_in_recall=len(memories),
        diag_list_alice_present=saw_a_pre,
        diag_list_bob_present=saw_b_pre,
        diag_node3_embedding_probe=embedding_diag,
        writers=[
            {"agent": "ai:alice", "marker": tag_a, "seen_by_charlie": saw_a},
            {"agent": "ai:bob", "marker": tag_b, "seen_by_charlie": saw_b},
        ],
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
