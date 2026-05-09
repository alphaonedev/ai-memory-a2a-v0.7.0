# F7 — Postgres SAL coverage gaps prevent Wave 4 GREEN cert

**Severity:** P1 (release-blocker for v0.7.0 expanded scope cert)
**Surfaced by:** v0.7.0 A2A Wave 4 Round 1 (`v0.7.0-a2a-wave4-r1-20260509-1520`)
**Subject under test:** ai-memory v0.7.0 commit `e294aa3` on `round-2-fixes` (PR #643)
**Daemon backend:** `--store-url postgres://aimemory:***@10.20.0.4:5432/aimemory_w4_live` (Path B, single shared disposable DB)

## Round 1 verdict

NOT GREEN. **46 PASS / 20 FAIL / 13 SKIP** of 74 in-scope (62.2 % pass).

The daemon's own startup log states the root cause:

```
WARN ai_memory::daemon_runtime: v0.7.0 Wave-3: postgres-backed daemon — handlers that have not yet migrated to the SAL trait surface 501 Not Implemented. See docs/postgres-age-guide.md for the supported endpoint inventory.
```

The Wave-3 adapter-selection refactor (PR #643, `--store-url postgres://`) successfully boots a daemon against postgres + Apache AGE — capabilities reports `storage_backend=postgres`, the audit chain persists across daemon restart on postgres (S78 PASS), bidirectional-federation between two postgres-backed daemons works (S81 PASS), AGE projection bootstraps correctly (S77 PASS), `ai-memory schema-init` lands schema_version=28 with 16 tables, 92 functions, 62 indices, 3 extensions including `age` and `vector` (Step 2 of Wave 4 deploy). What does NOT yet work is the **handler surface**: ~20 of the 74 in-scope scenarios hit handlers that surface 501 Not Implemented, return 503, or silently behave differently because the postgres SAL trait does not yet implement the equivalent of every sqlite handler.

## Failure inventory (20 in-scope FAIL)

Buckets ranked by daemon area:

### Bucket A — recall / semantic query / hybrid (3 FAIL)

S18, S79, S65 — `/api/v1/memories?q=` recall returns 0 rows when the query is hybrid/semantic. Linked write-then-recall scenarios pass (rows are present), but the postgres backend's hybrid recall path doesn't yet stitch BM25 + pgvector cosine to surface them.

- S18 (4.5s): semantic query did not surface alice's memory; semantic query did not surface bob's memory
- S79 (20.0s): 10/10 queries returned empty top-K; mean top-5 Jaccard 0.00 < floor 0.80
- S65 (9.9s): max_depth=10 found no path A→E (find_paths via KG)

### Bucket B — pubsub / notify / inbox / subscriptions (4 FAIL)

The notify / subscription / inbox tables surface 501 or return empty on postgres.

- S32 (6.9s): bob's inbox did not deliver alice's notify
- S33 (17.1s): bob's subscription list did not include the subscribed namespace
- S58 (6.7s): hermes did not see notify with marker
- S34 (13.8s): charlie did not see approved row (pending governance)

### Bucket C — namespace standards / governance enforcement (4 FAIL)

The `permissions.mode=enforce` / inheritance walk + standard-rules layering aren't wired into the postgres SAL trait. Writes that should 403 are returning 201.

- S35 (9.9s): get-standard returned HTTP 501; parent rule not layered
- S53 (7.1s): intruder got 201 (expected 403) — F8 enforce gate not applied on postgres
- S60 (4.5s): hermes write to parent got 201 (expected 403) — inheritance owner-chain
- S80 (4.8s): hermes write to deep child got 201 (expected 403) — F1 fix on postgres path

### Bucket D — KG (knowledge graph) timeline + temporal query (3 FAIL)

`kg_query`, `kg_timeline`, `kg_invalidate` return HTTP 503 — the postgres-backed KG path does not yet route through AGE Cypher for the timeline / temporal-edge surface.

- S45 (15.1s): kg_query(past) returned HTTP 503; kg_query(now) returned HTTP 503; kg_invalidate returned HTTP 503
- S46 (13.8s): timeline endpoint returned HTTP 503; expected >=2 edge events
- S82 (6.7s): kg_query returned http_code=0; kg_query returned no path between chain endpoints

### Bucket E — taxonomy / aliases / duplicates / quotas (4 FAIL)

- S44 (17.7s): could not locate `scenario44-…/alphaone` in taxonomy response; root node missing `subtree_count`
- S47 (5.9s): second `entity_register` did not surface union aliases (alias union not persisted on postgres)
- S48 (6.7s): `check_duplicate` returned 0 matches for a near-identical input
- S61 (37.5s): openclaw `quota.used=0` (expected ≥ 700) — agent_quotas counters not wired on postgres

### Bucket F — link signing / observed_by (1 FAIL)

- S52 (3.6s): `signature_verified=False`; `observed_by=None` — daemon-attested link signing not surfaced through postgres SAL trait

### Bucket G — schema-parity oracle (test-side, expected) (1 FAIL)

- S75 (1.2s): "pg schema_version=28 (expected 15 for v0.7.0-alpha)" — the scenario's hard-coded oracle pins the v0.7.0-alpha pre-Wave-1 schema (15). After Wave 1 schema-parity bump landed (commits `8d1c…`/`b09a8fa` series), live schema is 28. **This is a stale scenario, not a daemon defect.** S75 should be updated to read its expected version from `scope-v0.7.0.json` rather than a magic constant.

## What DID pass

The load-bearing postgres tests passed:

- S77 (postgres bootstrap) — capabilities reports `storage_backend=postgres`, both daemons running with `--store-url postgres://...`
- S78 (audit chain persists across daemon restart on postgres) — Cont 2's audit emit work holds
- S70 (sqlite→postgres migration roundtrip)
- S71 (AGE / Cypher ↔ CTE equivalence)
- S72 (AGE A2A KG fanout)
- S76 (AGE perf gate, partial soft-skip on AMD instance)
- S81 (heterogeneous federation between two postgres-backed daemons)
- S57 (audit chain across A2A)
- S62 (HNSW reranker A2A)
- S67/S68 (Grok dialog loop + reasoning trace)
- S69 (token budget under load)
- S70+S71+S72 — the postgres+AGE substrate cert from prior pgvec runs reproduced cleanly on the live daemon

## Recommendation

The Wave-3 deliverable (`--store-url postgres://`) is **architecturally correct** but the SAL trait surface is **incomplete** for cert. Specifically, the postgres SAL adapter needs the handlers behind these surfaces ported from the sqlite free-function path:

- `memory_search` / `memory_recall` (hybrid + semantic) — postgres+pgvector path
- `memory_inbox` + `memory_notify`
- `memory_subscribe` / `memory_list_subscriptions`
- `memory_namespace_get_standard` / `memory_namespace_set_standard`
- `memory_pending_*` (approve / reject / list)
- `memory_kg_query` / `memory_kg_timeline` / `memory_kg_invalidate` (route via AGE Cypher)
- `memory_get_taxonomy`
- `memory_entity_register` alias-union persistence
- `memory_check_duplicate` (vector + content-hash sweep on postgres)
- `agent_quotas` counters on the HTTP `POST /api/v1/memories` path (postgres equivalent of F7 fix)
- `permissions.mode=enforce` enforcement on the postgres SAL trait (postgres equivalent of F8 fix)
- `inheritance.owner_chain` walk on postgres (postgres equivalent of F1 fix)
- `memory_link` signed-link `observed_by` + `signature_verified` round-trip on postgres

Once those surfaces are SAL-trait-complete the Wave 4 gate should turn 100 % GREEN on the same campaign. **The v0.7.0 expanded scope cert is NOT satisfied at HEAD `e294aa3`** — recommend continuation as Wave 5 / a v0.7.0.1 follow-up rather than tagging v0.7.0 GA on the current postgres surface.

## Per-scenario evidence

Run dir: `/Users/fate/ai-memory-a2a-v0.7.0/runs/v0.7.0-a2a-wave4-r1-20260509-1520/`

Each scenario has a `.json` report and `.log` stderr. The `a2a-summary.json` aggregates.

## Round 2 disposition

Per the brief's hard rule, both rounds must complete. Round 2 is queued; the **expectation** is a near-identical NOT-GREEN profile (modulo state-reset noise) since the failures are deterministic SAL coverage gaps, not flakes.
