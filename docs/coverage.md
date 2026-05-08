# Coverage matrix — S1 through S95

This table captures the v0.7.0 scenario surface. **S1–S51** are the
ai2ai-gate v0.6.4 regression baseline (copied verbatim, banner-tagged).
**S52–S69** are net-new v0.7.0 scenarios authored in this repo.
**S70–S76** are net-new postgres + Apache AGE substrate scenarios that
exercise the v0.7.0-alpha SAL trait + migration tool surface.
**S77–S95** remain reserved slots for v0.7.1+ extensions; they are not
part of the round-1 / round-2 cert gate.

The original gap inventory is at [`/tmp/v07-coverage-gap.md`](/tmp/v07-coverage-gap.md)
when the orchestrator's planning stage produces it.

## Regression baseline (S1–S51)

These scenarios were copied verbatim from `ai-memory-ai2ai-gate` v0.6.4. Their
pass behaviour under v0.7.0 confirms zero regression on the ai2ai-gate
contract. Unmodified — see [`scenarios/CHANGELOG-v0.7.0.md`](../scenarios/CHANGELOG-v0.7.0.md)
for any deltas under watch.

| ID | Slug | Capability under test |
|----|------|-----------------------|
| S1  | write_read_mcp           | per-agent MCP store + read |
| S1b | write_read_http          | per-agent HTTP store + read |
| S2  | handoff                  | shared-context handoff |
| S4  | federation_burst         | federation under burst |
| S5  | consolidation            | duplicate consolidation |
| S6  | contradiction            | contradiction detection |
| S9  | mutation                 | metadata.governance round-trip |
| S10 | deletion                 | tombstone propagation |
| S11 | link_integrity           | link end-to-end |
| S12 | agent_register           | agent_type registry |
| S13 | concurrent_contention    | racing stores |
| S14 | partition_tolerance      | quorum + partition |
| S15 | read_your_writes         | RYW guarantee |
| S16 | tier_promotion           | tier promotion path |
| S17 | stats_consistency        | stats accuracy |
| S18 | query_expansion          | memory_expand_query |
| S20 | mtls_happy_path          | mTLS write-replicate |
| S21 | mtls_anonymous_rejected  | unauth client refused |
| S22 | identity_spoofing        | X-Agent-Id immutability vs body |
| S23 | malicious_content_fuzz   | sanitization + 1MB body |
| S24 | byzantine_peer           | byzantine fault tolerance |
| S25 | clock_skew               | clock skew tolerance |
| S26 | mixed_framework          | heterogeneous frameworks |
| S27 | openclaw_legacy          | openclaw legacy MCP |
| S28 | memory_search            | semantic + keyword recall |
| S29 | archive_lifecycle        | archive_list/restore/purge |
| S30 | capabilities_handshake   | capabilities v1+v2 |
| S31 | gc_quiescence            | GC quiescence |
| S32 | inbox_notify             | memory_notify + inbox |
| S33 | subscribe_pubsub         | subscription pub/sub |
| S34 | pending_governance       | pending approve/reject |
| S35 | namespace_standards      | namespace_set/get_standard |
| S36 | session_start            | memory_session_start |
| S37 | get_links_bidirectional  | get_links bidirectional |
| S38 | export_import            | export/import round-trip |
| S39 | sync_since_delta         | sync since delta |
| S40 | bulk_write               | 1000-store bulk write |
| S41 | metrics_prometheus       | /metrics scrape |
| S42 | namespaces_enumeration   | namespace enumeration |
| S43 | capabilities_v2_schema   | v2 schema property roster |
| S44 | taxonomy_walk            | memory_get_taxonomy |
| S45 | kg_query_temporal        | kg_query temporal |
| S46 | kg_timeline              | kg_timeline |
| S47 | entity_aliases           | entity alias resolution |
| S48 | check_duplicate          | check_duplicate threshold |
| S49 | lifecycle_end_to_end     | full memory lifecycle |
| S50 | sqlcipher_at_rest        | SQLCipher at rest |
| S51 | autonomous_tier_suite    | autonomous tier curator |

## Net-new for v0.7.0 (S52–S69)

| ID | Slug | New capability |
|----|------|----------------|
| S52 | a2a_link_signed             | attest_level=self_signed link signature, length(sig)=64, observed_by=daemon, both directions |
| S53 | a2a_enforce_owner           | F8 enforce default + sanitized 403 + decision_counts.enforce |
| S54 | identity_force_flag         | identity --force, refuse-by-default, legacy --no-overwrite no-op |
| S55 | smart_load_veto             | F14 keyword-veto router on both nodes (12/13 floor + cross-node identical) |
| S56 | tools_verbose_env           | AI_MEMORY_TOOLS_VERBOSE env wired (≥ 1.4× size, count identical) |
| S57 | audit_chain_a2a             | audit chain enabled, verify rc=0, tamper rc≠0, restart continuity |
| S58 | memory_notify_a2a           | cross-droplet notify → inbox |
| S59 | subscription_webhook_a2a    | subscribe + retry-ladder + DLQ on 5xx |
| S60 | inheritance_xagent          | governance inherit=true cross-agent |
| S61 | quota_isolation             | per-agent quota burn isolation |
| S62 | hnsw_reranker_a2a           | HNSW + reranker semantic top-K |
| S63 | consolidate_a2a             | consolidated_from_agents preserves both ids |
| S64 | capabilities_v1v2           | accept=v1 vs v2, schema_version + 8 families agree |
| S65 | find_paths_a2a              | max_depth gate + cycle detection |
| S66 | agent_id_immutable          | metadata.agent_id immutable across update |
| S67 | grok_dialog_loop            | Grok 4.2 ≥3-turn dialog via memory_notify + memory_store |
| S68 | reasoning_trace             | metadata.reasoning trace round-trips |
| S69 | token_budget_under_load     | doctor --tokens ≤ 3500 trimmed under 2× 500-store load |

## Postgres + Apache AGE substrate (S70–S76)

These scenarios exercise the v0.7.0-alpha postgres surface, which is
**NOT** the live storage backend yet — `ai-memory serve --store-url
postgres://...` is deferred to v0.7.1. What v0.7.0-alpha actually ships:

**In scope for v0.7.0:**
- One-shot migration tool: `ai-memory migrate --from sqlite://X --to postgres://Y`
  (and the reverse direction). UPSERT-based; idempotent on rerun.
- SAL trait + adapters (sqlx + pgvector for postgres, behind feature
  `sal-postgres`). Direct trait access via cargo tests / psql is the
  v0.7.0 way of touching postgres.
- Apache AGE Cypher path for the four KG operations (`memory_kg_query`,
  `memory_kg_timeline`, `memory_kg_invalidate`, `memory_find_paths`) with
  recursive-CTE fallback when AGE is absent.
- pgvector HNSW index for the SAL `recall` path; cosine distance
  normalised to similarity via `1 - distance` in the adapter.

**Out of scope for v0.7.0 (deferred to v0.7.1+):**
- Live daemon-on-postgres (`ai-memory serve --store-url postgres://...`).
- `MemoryStore::link()` on postgres — returns `UnsupportedCapability("LINKS")`.
- `MemoryStore::register_agent()` on postgres — returns
  `UnsupportedCapability("AGENT_REGISTRATION")`.
- 4 of the 6 SQLite recall scoring factors (no `access_count`,
  `confidence`, `tier_bonus`, `recency`) — postgres recall returns the
  HNSW similarity component only.
- Schema parity. Postgres ships at schema_version=15; SQLite is at v28.
  See the schema-parity gap table below.

| ID | Slug | Surface under test |
|----|------|--------------------|
| S70 | pg_migration_roundtrip      | sqlite→postgres→sqlite migration round-trip + idempotent rerun + sha256 content equivalence |
| S71 | age_cte_equivalence         | AGE Cypher path ≡ recursive-CTE fallback for kg_query/kg_timeline/kg_invalidate/find_paths |
| S72 | age_a2a_kg                  | A2A: openclaw migrates KG, hermes reads same fingerprint via shared postgres+AGE |
| S73 | pg_sal_contract             | upstream `tests/sal_contract.rs` against live postgres droplet (`cargo test --features sal-postgres`) |
| S74 | pg_unsupported_capability   | `link()` and `register_agent()` return `UnsupportedCapability` cleanly (no panic, no silent ok) |
| S75 | pg_schema_parity            | snapshot postgres@v15 vs sqlite@v28; enumerate the 13 missing migrations and which features they gate |
| S76 | age_perf_gate               | AGE p95 ≥ 30% faster than CTE p95 at depth=5 on a 1000-entity / 5000-edge corpus (README bench gate) |

### Schema parity gap (postgres v15 ↔ sqlite v28)

S75 pins this snapshot. The 13 missing migrations gate the following
features which are therefore **not exercisable on postgres in v0.7.0**:

| Migration | Feature gated |
|-----------|---------------|
| v16 | governance inheritance (cross-agent inherit=true) |
| v17 | webhook subscriptions |
| v18 | audit log chain |
| v19 | transcripts |
| v20 | signed events |
| v21 | agent quotas |
| v22 | link `attest_level` column |
| v23 | A2A correlation table |
| v24 | smart-load veto state |
| v25 | KG temporal-index v2 |
| v26 | tier-promotion metadata |
| v27 | subscription DLQ |
| v28 | `consolidated_from_agents` array |

If the postgres adapter advances past v15 inside the v0.7.0 release
window, S75 fails — that's the trip-wire we want.

## v0.7.1+ reserve (S77–S95)

S77–S95 are reserved slot ids for follow-up scenarios authored after the
v0.7.0 cert gate lands. **Not part of the round-1 / round-2 100% gate.**
Likely fillers: cross-region replication, KMS-backed signing, BYO-key,
multi-tenant quota, ipv6 mTLS, live daemon-on-postgres, large-corpus
rerank, soak endurance burst, etc.
