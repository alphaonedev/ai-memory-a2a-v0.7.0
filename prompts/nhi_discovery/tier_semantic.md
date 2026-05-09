# Tier overlay — semantic (no local LLM)

This daemon is running in **semantic tier**: embedder + reranker are
local, but there is NO local LLM. Features that route to an LLM
(autonomous-tier-only) will return 501/skipped:

* `auto_tag` (LLM tagger)
* `consolidate` (LLM merger)
* `expand_query` (semantic mode is OK; LLM-rewrite mode is not)
* `detect_contradiction` (LLM judge)
* `smart_load` (LLM-driven inclusion)

If you call one of these and get 501 / `{"error": "tier_required":"autonomous"}`,
that is **expected** — log a `category: "spec", severity: "info"`
finding only if the error shape is unexpected (e.g. raw 500, or
`tier_required` field missing).

Available semantic-tier features that you SHOULD probe deeply:

* `search` (vector + lexical)
* `recall` (6-factor scoring)
* `kg_query`, `find_paths`, `kg_timeline`
* `register_agent`, `link()`, signed-link verify
* `audit_chain`, `quota_status`
* `subscribe` / `notify` / DLQ
* `archive`, `forget`, `gc`
* `governance` (pending → approve/reject flow)
* `import` / `export`
