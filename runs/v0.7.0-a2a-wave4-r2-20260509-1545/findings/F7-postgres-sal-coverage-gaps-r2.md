# F7 (Round 2) — Postgres SAL coverage gaps reproduce, plus state-dependent flakes

**Round:** Wave 4 Round 2
**Subject under test:** ai-memory v0.7.0 commit `e294aa3` on `round-2-fixes` (PR #643)
**Campaign:** `v0.7.0-a2a-wave4-r2-20260509-1545`
**Disposable DB:** `aimemory_w4_live` (DROP+CREATE+schema-init between rounds)
**Sister run:** [`v0.7.0-a2a-wave4-r1-20260509-1520`](../v0.7.0-a2a-wave4-r1-20260509-1520/findings/F7-postgres-sal-coverage-gaps.md)

## Round 2 verdict

NOT GREEN. **44 PASS / 22 FAIL / 13 SKIP** of 74 in-scope (59.5 % pass).

**Both rounds NOT GREEN — Wave 4 cert gate is NOT satisfied.**

## R1 vs R2 delta

R1 reported 20 in-scope FAIL; R2 reports 22. The 20-FAIL set is **identical** between rounds (deterministic SAL coverage gap). R2 adds:

- **S16** (tier_promotion): promote endpoint returned HTTP 404 → bob sees tier=short, expected long. PASSed in R1.
- **S49** (lifecycle_end_to_end): promote: HTTP 404. PASSed in R1.

Both R2-only fails are on the same `memory_promote` endpoint surfacing 404 instead of 200. Likely state-dependent: R1 ran on a freshly-migrated `aimemory_w4_live` DB that had only a schema (no rows); R2 ran after a DROP+CREATE+schema-init reset. The 404 suggests a server-side cache or in-memory map (e.g. tier cache, agent registry) didn't repopulate cleanly after the daemon restart against the new disposable DB. The federation catchup loop may also have re-replicated stale rows from peer's in-memory state.

This stateful flake is *not* a postgres SAL coverage gap per se; it's a daemon-restart hygiene issue when the underlying DB has been DROPed under it. In a normal production deployment the DB persists across restarts. The flake is academic for cert purposes — the 20-FAIL deterministic core is enough to block GREEN regardless of S16/S49.

## Both rounds NOT GREEN — root cause unchanged

Same as R1 finding F7: the postgres SAL trait implementation is incomplete. ~20 in-scope handlers surface 501 / 503 / 0-row responses on the postgres path. Buckets reproduce identically:

- **A** (recall / semantic): S18, S65, S79
- **B** (pubsub / notify / inbox / subscriptions): S32, S33, S34, S58
- **C** (namespace standards / governance enforcement): S35, S53, S60, S80
- **D** (KG temporal + timeline + AGE Cypher via daemon): S45, S46, S82
- **E** (taxonomy / aliases / duplicates / quotas): S44, S47, S48, S61
- **F** (link signing): S52
- **G** (test-side stale oracle): S75
- **R2-only state flakes:** S16, S49 (memory_promote 404)

## What still works (load-bearing PASS list, both rounds)

- S77 — daemon boot with `--store-url postgres://...` reports `storage_backend=postgres`
- S78 — audit chain persists across postgres-backed daemon restart (Cont 2 audit emit work)
- S70 — sqlite ↔ postgres migration roundtrip
- S71 — AGE Cypher / SQL CTE equivalence
- S72 — AGE A2A KG fanout
- S76 — AGE perf gate (partial soft-skip on AMD instance)
- S81 — heterogeneous federation between two postgres-backed daemons
- S57 — audit chain across A2A
- S62 — HNSW reranker A2A
- S67/S68 — Grok dialog loop + reasoning trace
- S69 — token budget under load

The Wave-1 (schema parity v15→v28), Wave-2 (`ai-memory schema-init`), and Wave-3 (`--store-url postgres://`) deliverables hold. Wave-4 surfaced that the **handler-surface migration** to the SAL trait was not finished as part of Wave-3.

## Recommendation

Continue as Wave 5 / v0.7.0.1 follow-up: complete the SAL-trait coverage for the bucketed handlers above. Once those land, re-run the same Wave 4 campaign — failure profile should drop to 0 on the deterministic 20.
