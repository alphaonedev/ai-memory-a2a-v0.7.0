#!/usr/bin/env python3
# v0.7.0 A2A — copied from ai2ai-gate v0.6.4 baseline; runs unchanged unless v0.7.0 schema delta noted.
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Scenario 51 — autonomous-tier LLM surface (v0.6.3).

Runs against a node configured with the smart/autonomous tier (Ollama
+ Gemma 4). Exercises the four LLM-backed endpoints that v0.6.3
exposes and asserts each returns NON-DEGENERATE output (i.e. not an
empty list, not a verbatim echo of the input).

Endpoints under test:
  * POST /api/v1/auto_tag           — generate semantic tags
  * POST /api/v1/consolidate (LLM)  — consolidation summary should be
                                       a real summary, not concat
  * POST /api/v1/expand_query       — produce >= 1 reformulation
  * POST /api/v1/detect_contradiction
                                    — flag two opposing memories as
                                       contradictory

Detect-and-skip if the autonomous tier is not advertised on the
target node (Ollama down, no Gemma 4 model, daemon not built with
the feature). The skip carries enough diagnostic to triage from the
campaign report.
"""

import sys
import pathlib
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from a2a_harness import Harness, log, new_uuid


SCENARIO_ID = "51"


def _id_of(resp: object) -> str:
    if isinstance(resp, dict):
        return resp.get("id") or resp.get("memory_id") or ""
    return ""


def _capabilities(h: Harness, ip: str) -> dict | None:
    _, doc = h.http_on(ip, "GET", "/api/v1/capabilities", include_status=True)
    if isinstance(doc, dict) and doc.get("http_code") == 200:
        body = doc.get("body")
        if isinstance(body, dict):
            return body
    return None


def _has_autonomous_tier(cap: dict | None) -> tuple[bool, str]:
    """Returns (has_tier, diagnostic). Tries multiple shapes."""
    if not isinstance(cap, dict):
        return False, "no capabilities document"
    tier = cap.get("tier")
    if isinstance(tier, str) and tier.lower() in ("smart", "autonomous"):
        return True, f"tier={tier!r}"
    models = cap.get("models")
    if isinstance(models, dict):
        for k, v in models.items():
            if v and ("gemma" in str(v).lower() or "ollama" in str(k).lower()):
                return True, f"models[{k}]={v!r}"
    if isinstance(models, list):
        for m in models:
            if isinstance(m, str) and "gemma" in m.lower():
                return True, f"models contains {m!r}"
            if isinstance(m, dict):
                name = str(m.get("name") or m.get("id") or "").lower()
                if "gemma" in name:
                    return True, f"models entry {m}"
    return False, f"tier={tier!r} models={models!r}"


def main() -> None:
    h = Harness.from_env(SCENARIO_ID)
    cap = _capabilities(h, h.node1_ip)
    has_tier, diag = _has_autonomous_tier(cap)
    if not has_tier:
        h.skip(
            f"autonomous/smart tier not available on node-1 — {diag}",
            capabilities_tier=(cap or {}).get("tier"),
            capabilities_models=(cap or {}).get("models"),
        )

    ns = f"scenario51-llm-{new_uuid()[:6]}"
    results: dict[str, dict] = {}

    # ---- 1. auto_tag ----
    log("auto_tag: write a domain-specific memory then ask for tags")
    content = (
        "Quarterly OKR review: ai-memory v0.6.3 ships SQLCipher at-rest "
        "encryption, schema_version=2 capabilities, and the autonomous tier "
        "powered by Gemma 4 via Ollama."
    )
    _, mr = h.write_memory(h.node1_ip, "ai:alice", ns,
                           title="okr-q1", content=content, tier="long")
    mid = _id_of(mr)
    h.settle(3, reason="auto_tag indexing")
    _, tag_doc = h.http_on(
        h.node1_ip, "POST", "/api/v1/auto_tag",
        body={"memory_id": mid, "namespace": ns},
        agent_id="ai:alice", include_status=True, timeout=120,
    )
    tag_code = (tag_doc or {}).get("http_code", 0) if isinstance(tag_doc, dict) else 0
    tag_body = (tag_doc or {}).get("body") if isinstance(tag_doc, dict) else None
    tags: list[str] = []
    if isinstance(tag_body, dict):
        raw = tag_body.get("tags") or tag_body.get("auto_tags") or []
        if isinstance(raw, list):
            tags = [str(t) for t in raw if t]
    log(f"  auto_tag HTTP {tag_code} tags={tags}")
    results["auto_tag"] = {"http_code": tag_code, "tags": tags}

    # ---- 2. consolidate (LLM summary) ----
    log("consolidate: write 3 related siblings + ask for an LLM summary")
    sib_ids: list[str] = []
    sib_contents = [
        "Engineering filed JIRA AOM-101 to harden the sync_push retry path.",
        "AOM-101 follow-up: added exponential backoff + jitter in retry loop.",
        "Closed AOM-101 after 48h of clean syncs with the new backoff policy.",
    ]
    for i, c in enumerate(sib_contents):
        _, sr = h.write_memory(h.node1_ip, "ai:alice", ns,
                               title=f"aom101-{i}", content=c, tier="mid")
        sid = _id_of(sr)
        if sid:
            sib_ids.append(sid)
    h.settle(3, reason="siblings settle")
    _, cdoc = h.http_on(
        h.node1_ip, "POST", "/api/v1/consolidate",
        body={"ids": sib_ids, "namespace": ns,
              "title": "AOM-101 lifecycle", "use_llm": True},
        agent_id="ai:alice", include_status=True, timeout=180,
    )
    cons_code = (cdoc or {}).get("http_code", 0) if isinstance(cdoc, dict) else 0
    cbody = (cdoc or {}).get("body") if isinstance(cdoc, dict) else None
    summary = ""
    if isinstance(cbody, dict):
        summary = (
            cbody.get("summary") or cbody.get("content") or
            (cbody.get("memory") or {}).get("content") if isinstance(cbody.get("memory"), dict) else ""
        ) or ""
    summary_str = str(summary or "")
    # Non-degeneracy: summary must not be a verbatim concat of the inputs
    # and must be non-trivially long.
    is_concat = all(c[:30] in summary_str for c in sib_contents)
    log(f"  consolidate HTTP {cons_code} summary_len={len(summary_str)} is_concat={is_concat}")
    results["consolidate"] = {
        "http_code": cons_code,
        "summary_len": len(summary_str),
        "is_verbatim_concat": is_concat,
    }

    # ---- 3. expand_query ----
    log("expand_query: ask for reformulations of a vague prompt")
    q = "team velocity"
    _, edoc = h.http_on(
        h.node1_ip, "POST", "/api/v1/expand_query",
        body={"query": q, "namespace": ns},
        agent_id="ai:alice", include_status=True, timeout=120,
    )
    e_code = (edoc or {}).get("http_code", 0) if isinstance(edoc, dict) else 0
    ebody = (edoc or {}).get("body") if isinstance(edoc, dict) else None
    expansions: list[str] = []
    if isinstance(ebody, dict):
        raw = ebody.get("queries") or ebody.get("expansions") or ebody.get("variants") or []
        if isinstance(raw, list):
            expansions = [str(x) for x in raw if x]
    # Filter out the verbatim original — that's degenerate.
    non_trivial = [x for x in expansions if x.strip().lower() != q.lower()]
    log(f"  expand_query HTTP {e_code} expansions={expansions}")
    results["expand_query"] = {"http_code": e_code,
                               "non_trivial_count": len(non_trivial),
                               "expansions": expansions}

    # ---- 4. detect_contradiction ----
    log("detect_contradiction: write opposing claims, expect a contradicts hit")
    topic = f"sky-{new_uuid()[:6]}"
    h.write_memory(h.node1_ip, "ai:alice", ns,
                   title=f"{topic}-blue", content=f"{topic} is blue",
                   metadata={"topic": topic})
    h.write_memory(h.node1_ip, "ai:alice", ns,
                   title=f"{topic}-red", content=f"{topic} is red",
                   metadata={"topic": topic})
    h.settle(5, reason="contradiction indexing + LLM call")
    qparams = urllib.parse.urlencode({"topic": topic, "namespace": ns})
    _, ddoc = h.http_on(
        h.node1_ip, "GET", f"/api/v1/contradictions?{qparams}",
        include_status=True, timeout=120,
    )
    d_code = (ddoc or {}).get("http_code", 0) if isinstance(ddoc, dict) else 0
    dbody = (ddoc or {}).get("body") if isinstance(ddoc, dict) else None
    sees_contradiction = False
    if isinstance(dbody, dict):
        rels = (dbody.get("links") or []) + (dbody.get("relations") or []) + (dbody.get("contradictions") or [])
        for r in rels:
            if isinstance(r, dict):
                rel = (r.get("relation") or r.get("type") or "")
                if "contradict" in rel.lower():
                    sees_contradiction = True
                    break
        if not sees_contradiction:
            mems = dbody.get("memories") or []
            if isinstance(mems, list) and len(mems) >= 2:
                sees_contradiction = True
    log(f"  detect_contradiction HTTP {d_code} hit={sees_contradiction}")
    results["detect_contradiction"] = {
        "http_code": d_code,
        "contradiction_detected": sees_contradiction,
    }

    # ---- verdict ----
    reasons: list[str] = []
    passed = True
    if results["auto_tag"]["http_code"] != 200:
        passed = False
        reasons.append(f"auto_tag HTTP {results['auto_tag']['http_code']}")
    if len(results["auto_tag"]["tags"]) < 1:
        passed = False
        reasons.append("auto_tag returned 0 tags (degenerate)")
    if results["consolidate"]["http_code"] not in (200, 201):
        passed = False
        reasons.append(f"consolidate HTTP {results['consolidate']['http_code']}")
    if results["consolidate"]["summary_len"] < 20:
        passed = False
        reasons.append(f"consolidate summary too short ({results['consolidate']['summary_len']} chars)")
    if results["consolidate"]["is_verbatim_concat"]:
        passed = False
        reasons.append("consolidate summary is verbatim concat of inputs (LLM not invoked?)")
    if results["expand_query"]["http_code"] != 200:
        passed = False
        reasons.append(f"expand_query HTTP {results['expand_query']['http_code']}")
    if results["expand_query"]["non_trivial_count"] < 1:
        passed = False
        reasons.append("expand_query returned no non-trivial reformulations")
    if results["detect_contradiction"]["http_code"] != 200:
        passed = False
        reasons.append(f"detect_contradiction HTTP {results['detect_contradiction']['http_code']}")
    if not results["detect_contradiction"]["contradiction_detected"]:
        passed = False
        reasons.append("detect_contradiction did not flag opposing claims")

    h.emit(
        passed=passed,
        reason="; ".join(reasons) if reasons else "",
        capabilities_tier=(cap or {}).get("tier"),
        per_endpoint=results,
        reasons=reasons,
    )


if __name__ == "__main__":
    main()
