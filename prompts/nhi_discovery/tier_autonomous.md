# Tier overlay — autonomous (full local LLM)

This daemon is running in **autonomous tier**: embedder + reranker +
local LLM (Ollama) are all loaded and GPU-accelerated. Every advertised
feature should be live; no `tier_required` skips are expected.

Probe the LLM-bound features as a **first-class** concern — these are
the v0.7.1 cert-track features that semantic-tier deployments cannot
exercise:

* `auto_tag` — store a memory; assert tags appear in metadata after
  ≤2s. Tags should be domain-relevant, not generic.
* `consolidate` — store 5 redundant memories about the same fact,
  trigger consolidate, assert ≥3 are merged into ≤2 canonical rows.
* `expand_query` (LLM mode) — issue an obscure query, assert the
  expansion includes synonyms/related terms.
* `detect_contradiction` — store fact A, then store fact ¬A, query
  `detect_contradiction(A)` — does it surface ¬A?
* `smart_load` — load_family with LLM inclusion judgment. Does the
  loaded set obey size budget? Does it include high-relevance / exclude
  low-relevance correctly?
* `memory_inbox` LLM summary — if inbox supports an LLM-summary mode,
  probe it.

Also profile **LLM latency**:

* p50/p99 of `auto_tag` round-trip
* p50/p99 of `consolidate` round-trip
* GPU memory pressure at sustained load (read `/api/v1/diagnostics`
  if exposed)

Bug-class hot spots specific to autonomous tier:

* LLM dispatch deadlock under concurrent writes (Round-3 F6 territory).
* Token budget cap misfires (truncated tags, half-merged consolidations).
* Embedding ↔ LLM rerank disagreement (semantic search returns one
  order, LLM rerank returns the inverse).
* GPU OOM under burst load — does the daemon degrade gracefully (queue,
  fallback to semantic-only) or 500?
