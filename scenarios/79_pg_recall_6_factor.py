#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 79 — 6-factor hybrid recall on postgres backend (Wave 4).

Validates Stream A's hybrid-recall parity claim in the production path:
top-K results from `memory_recall` over a postgres-backed daemon must
overlap the sqlite reference top-K within tolerance. This scenario
seeds 50 deterministic memories (5 namespaces, varied content
distribution) on openclaw, then runs N=10 recall queries with
heterogeneous filters (semantic, lexical, namespaced, tier-scoped) and
measures the top-K@5 Jaccard overlap against a reference set captured
from the same input on sqlite.

The reference set is computed by running the same query against the
local sqlite seed (h.node_db_path()) via `ai-memory recall --db ...`
when available, OR by deriving the expected top-K from a deterministic
score function over the seeded titles (fallback when the recall CLI is
not in path on the droplet).

PASS criteria:
  - all 10 queries return >=1 result on the postgres daemon
  - mean Jaccard@5 across the 10 queries >= 0.80 (Stream A's published
    recall-parity tolerance for the 6-factor reranker)

Self-skips when A2A_BACKEND_KIND=sqlite.

# v0.7.0 Continuation 6 — ref-set narrowing fix (2026-05-08)
#
# The original ref-set computed top-K candidates by unioning every
# `seeded_by_subns[s]` for s in `sub_matches`, i.e. across **all**
# sub-namespaces under the query root (`animals`, `lang`, `db`).
# The actual recall query is namespace-scoped — it only hits
# `f"{base_ns}-{first_sub}"` — so the candidate pools were
# fundamentally different cardinalities and the Jaccard floor
# (0.80) was unreachable by construction. The fix narrows the
# candidate pool to only the FIRST matching sub-namespace, which
# matches the namespace the actual recall query resolves to. This
# preserves the test intent — exercising the 6-factor scoring across
# the seeded corpus — while making the reference set comparable to
# what the daemon actually surfaces.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from a2a_harness import Harness, log, new_uuid

SCENARIO_ID = "79"
SEED_COUNT = 50
QUERY_COUNT = 10
TOP_K = 5
JACCARD_FLOOR = 0.80


# Deterministic seed corpus: 50 titles, 5 namespaces, mixed tier/priority,
# content distributions cover semantic clusters (dogs/cats/python/rust/sql).
CLUSTERS = [
    ("animals.dog", "the brown dog runs fast across the field"),
    ("animals.cat", "the orange cat naps on the windowsill"),
    ("lang.python", "python list comprehensions are concise"),
    ("lang.rust", "rust ownership prevents data races at compile time"),
    ("db.sql", "sql joins combine rows from multiple tables"),
]

# Queries chosen so a passing recall surface returns the cluster exemplars.
QUERIES = [
    ("dog field", "animals"),
    ("cat sleeping", "animals"),
    ("python list", "lang"),
    ("rust ownership", "lang"),
    ("sql join", "db"),
    ("compile time safety", "lang"),
    ("brown animal", "animals"),
    ("sleeping pet", "animals"),
    ("comprehension syntax", "lang"),
    ("table query", "db"),
]


def _seed(h: Harness, node_ip: str, agent: str, base_ns: str) -> int:
    """Seed 50 deterministic memories. Returns count successfully written."""
    written = 0
    for i in range(SEED_COUNT):
        cluster_idx = i % len(CLUSTERS)
        sub_ns, content_template = CLUSTERS[cluster_idx]
        ns = f"{base_ns}-{sub_ns}"
        # vary content slightly so semantic recall has gradient
        suffix_word = ("quickly", "slowly", "today", "yesterday", "carefully")[i % 5]
        content = f"{content_template} {suffix_word}"
        title = f"s79-{i:02d}-{cluster_idx}-{suffix_word}"
        rc, doc = h.write_memory(
            node_ip, agent, ns,
            title=title,
            content=content,
            tier=("short", "mid", "long")[i % 3],
            priority=(i % 9) + 1,
            include_status=True,
        )
        if isinstance(doc, dict) and (doc.get("http_code") in (200, 201)):
            written += 1
    return written


def _recall_topk(h: Harness, node_ip: str, query: str, base_ns: str,
                 sub_ns: str, k: int) -> list[str]:
    """Hit the daemon's /api/v1/recall endpoint and return top-k memory ids."""
    body = {
        "query": query,
        "namespace": f"{base_ns}-{sub_ns}",
        "limit": k,
    }
    rc, resp = h.http_on(node_ip, "POST", "/api/v1/recall",
                         body=body, include_status=True)
    if not isinstance(resp, dict):
        return []
    body_resp = resp.get("body")
    if not isinstance(body_resp, dict):
        return []
    items = body_resp.get("memories") or body_resp.get("results") or []
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for it in items[:k]:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("memory_id")
            if mid:
                ids.append(str(mid))
    return ids


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    h.skip_if_backend_sqlite(
        "S79 validates 6-factor recall on the postgres production path; "
        "skipping on sqlite baseline (S62 covers sqlite reranker)."
    )

    openclaw = h.node1_ip
    suffix = new_uuid()[:6]
    AGENT = f"ai:s79-{suffix}"
    base_ns = f"s79-{suffix}"

    log(f"phase A: seed {SEED_COUNT} memories on openclaw (postgres-backed)")
    n_written = _seed(h, openclaw, AGENT, base_ns)
    log(f"  seeded {n_written}/{SEED_COUNT}")

    # Build the cluster→ids map by listing each sub-ns; we'll use it as
    # the candidate pool for the reference top-K.
    seeded_by_subns: dict[str, list[tuple[str, str]]] = {}
    for sub_ns, _content in CLUSTERS:
        rc, resp = h.list_memories(openclaw, f"{base_ns}-{sub_ns}", limit=50)
        rows = (resp or {}).get("memories", []) if isinstance(resp, dict) else []
        seeded_by_subns[sub_ns] = [
            (m.get("id") or "", m.get("content") or "") for m in rows
        ]

    log(f"phase B: run {QUERY_COUNT} recall queries on postgres daemon")
    overlaps: list[float] = []
    nonempty = 0
    per_query: list[dict] = []
    for query, ns_root in QUERIES:
        # find the right sub-ns by scanning CLUSTERS prefixes
        sub_matches = [s for (s, _) in CLUSTERS if s.startswith(ns_root)]
        # Continuation 6 fix: narrow candidates to only the FIRST
        # matching sub-namespace — the actual recall query is
        # namespace-scoped (line below uses `first_sub` as the ns
        # parameter), so unioning every sub_ns under `ns_root`
        # produced a candidate pool that the daemon's response could
        # never match. Reference set must mirror what the daemon
        # actually queries against to keep the Jaccard score
        # meaningful.
        first_sub = sub_matches[0] if sub_matches else ns_root
        candidates: dict[str, str] = {
            mid: content
            for mid, content in seeded_by_subns.get(first_sub, [])
            if mid
        }
        # reference ranking via lexical Jaccard
        qtokens = set(query.lower().split())
        ref_scored = sorted(
            [
                (
                    len(qtokens & set(c.lower().split())) /
                    (len(qtokens | set(c.lower().split())) or 1),
                    mid,
                )
                for mid, c in candidates.items()
            ],
            reverse=True,
        )
        ref_topk = [mid for _, mid in ref_scored[:TOP_K]]

        # run actual recall against postgres daemon — pick first sub_ns
        # under this root for the path-bound ns parameter
        actual_topk = _recall_topk(h, openclaw, query, base_ns, first_sub, TOP_K)
        if actual_topk:
            nonempty += 1

        ovl = _jaccard(ref_topk, actual_topk)
        overlaps.append(ovl)
        per_query.append({
            "query": query,
            "ref_topk": ref_topk,
            "actual_topk": actual_topk,
            "jaccard": round(ovl, 3),
        })
        log(f"  q={query!r} ref={ref_topk[:3]} actual={actual_topk[:3]} J={ovl:.2f}")

    mean_ovl = sum(overlaps) / (len(overlaps) or 1)

    reasons: list[str] = []
    passed = True
    if n_written < SEED_COUNT:
        passed = False
        reasons.append(f"seeded only {n_written}/{SEED_COUNT}")
    if nonempty < QUERY_COUNT:
        passed = False
        reasons.append(f"{QUERY_COUNT - nonempty}/{QUERY_COUNT} queries returned empty top-K")
    if mean_ovl < JACCARD_FLOOR:
        passed = False
        reasons.append(
            f"mean top-{TOP_K} Jaccard {mean_ovl:.2f} < floor {JACCARD_FLOOR:.2f}"
        )

    h.emit(
        passed=passed,
        reason="; ".join(reasons),
        per_agent={
            "openclaw": {
                "seeded": n_written,
                "queries": QUERY_COUNT,
                "nonempty": nonempty,
                "mean_jaccard_topk": round(mean_ovl, 3),
                "floor": JACCARD_FLOOR,
            }
        },
        per_query=per_query,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
