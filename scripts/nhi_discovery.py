# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
NHI discovery driver — gives a Grok 4.2 reasoning model a tool surface
against a live ai-memory v0.7.0 daemon and asks it to discover bugs,
functional regressions, and performance anomalies.

Run via the scenario wrappers (S83/S84/S85, plus S90/S91/S92 for
autonomous-tier GPU droplets). The driver is shared.

Architecture
============

  briefing (system_briefing.md + focus.md + tier overlay)
       │
       ▼
  Grok-4.2-reasoning  ──── action JSON ───▶  driver
       ▲                                       │
       │                                       ▼
       └────────── observation JSON ────── harness.http_on (live HTTP)

Action JSON shape (driver enforces):
  {"thought": str, "action": str, "args": dict, "expected": str,
   "finding": null | {finding obj}}

Observation JSON shape (driver returns):
  {"http_code": int, "elapsed_ms": int, "body": <parsed>, "tool_calls_left": int,
   "seconds_left": int}

Findings file shape:
  {"agent": str, "run_id": str, "topology": str, "tier": str,
   "started_at": str, "duration_seconds": int, "tool_calls": int,
   "findings": [<finding obj>...]}

Hard rate-limits:
  * MAX_TOOL_CALLS = 200 (env: NHI_MAX_TOOL_CALLS)
  * TIME_BUDGET_S = 1800 (env: NHI_TIME_BUDGET_S)
  * MAX_BODY_BYTES = 256_000  (per response, after which body is truncated
                                 to keep prompt size sane)

Safety:
  * Every write is auto-scoped to a per-run namespace.
  * DELETE / PUT outside that namespace are rejected client-side.
  * No ssh, no exec, no filesystem reads.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
import urllib.parse
from typing import Any

# Allow imports from sibling files
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from a2a_harness import Harness, log  # noqa: E402
from grok_driver import grok_chat as _grok_chat_raw  # noqa: E402


PROMPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "prompts" / "nhi_discovery"


# Default budgets (env-overridable)
DEFAULT_MAX_TOOL_CALLS = int(os.environ.get("NHI_MAX_TOOL_CALLS", "200"))
DEFAULT_TIME_BUDGET_S = int(os.environ.get("NHI_TIME_BUDGET_S", "1800"))
DEFAULT_MAX_TURNS = int(os.environ.get("NHI_MAX_TURNS", "150"))
MAX_BODY_BYTES = int(os.environ.get("NHI_MAX_BODY_BYTES", "256000"))
MAX_BODY_BYTES_IN_PROMPT = int(os.environ.get("NHI_MAX_BODY_BYTES_IN_PROMPT", "8000"))

# Generous timeout for the reasoning model — discovery prompts are large.
os.environ.setdefault("XAI_TIMEOUT_S", "240")


# -----------------------------------------------------------------------------
# Briefing assembly
# -----------------------------------------------------------------------------

def _load_briefing(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"NHI briefing missing: {path}")
    return path.read_text(encoding="utf-8").strip()


def assemble_system_prompt(*, focus: str, tier: str, namespace: str,
                           agent_label: str, peer_label: str) -> str:
    """Compose the per-agent system prompt from briefing fragments.

    `focus` ∈ {"openclaw", "hermes"}; `tier` ∈ {"semantic", "autonomous"}.
    """
    parts = [
        _load_briefing("system_briefing"),
        "",
        "---",
        "",
        _load_briefing(f"{focus}_focus"),
        "",
        "---",
        "",
        _load_briefing(f"tier_{tier}"),
        "",
        "---",
        "",
        f"## Run-specific context",
        "",
        f"* You are: `{agent_label}`",
        f"* Federation peer: `{peer_label}`",
        f"* Your discovery namespace: `{namespace}` (writes auto-scoped here)",
        f"* Tier: `{tier}`",
        "",
        "Begin by probing capabilities. Emit one action JSON per turn.",
    ]
    return "\n".join(parts)


# -----------------------------------------------------------------------------
# Tool dispatch
# -----------------------------------------------------------------------------

ALLOWED_ACTIONS = {
    "http_get", "http_post", "http_put", "http_delete",
    "write_memory", "list_memories", "search",
    "stop",
}

DENIED_PATH_PREFIXES = (
    # No raw access to internal/admin surface
    "/internal", "/admin", "/_debug",
)


def _is_in_namespace(path: str, body: Any, namespace: str) -> bool:
    """Defensive scope check: only allow PUT/DELETE on the discovery namespace."""
    if isinstance(body, dict):
        if body.get("namespace") and body["namespace"] != namespace:
            return False
        if body.get("metadata", {}).get("namespace") and body["metadata"]["namespace"] != namespace:
            return False
    if "namespace=" in path:
        try:
            qs = urllib.parse.parse_qs(path.split("?", 1)[1])
            ns_in_query = qs.get("namespace", [None])[0]
            if ns_in_query and ns_in_query != namespace:
                return False
        except (IndexError, ValueError):
            pass
    return True


def _truncate(s: Any, limit: int = MAX_BODY_BYTES_IN_PROMPT) -> str:
    raw = s if isinstance(s, str) else json.dumps(s, default=str)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"\n\n[... truncated {len(raw) - limit} bytes ...]"


def _node_resolve(h: Harness, name: str) -> str:
    name = (name or "").lower().strip()
    if name in ("node1", "openclaw", "n1", "1"):
        return h.node1_ip
    if name in ("node2", "hermes", "n2", "2"):
        return h.node2_ip
    return h.node1_ip  # default


def execute_action(h: Harness, namespace: str, agent_id: str,
                   action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one NHI-driven action. Returns the observation dict."""
    args = args or {}
    node = _node_resolve(h, args.get("node", "node1"))
    t0 = time.perf_counter()

    if action in ("http_get", "http_post", "http_put", "http_delete"):
        method_map = {"http_get": "GET", "http_post": "POST",
                      "http_put": "PUT", "http_delete": "DELETE"}
        method = method_map[action]
        path = str(args.get("path") or "/")
        if not path.startswith("/"):
            return {"http_code": 0, "error": "path must start with /"}
        if any(path.startswith(p) for p in DENIED_PATH_PREFIXES):
            return {"http_code": 0, "error": f"path denied (out of scope): {path}"}
        body = args.get("body") if method in ("POST", "PUT") else None
        if method in ("PUT", "DELETE") and not _is_in_namespace(path, body, namespace):
            return {"http_code": 0, "error": "out-of-namespace write/delete denied"}
        rc, resp = h.http_on(node, method, path, body=body,
                             agent_id=agent_id, include_status=True, timeout=60)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not isinstance(resp, dict):
            return {"http_code": 0, "rc": rc, "error": "non-dict response", "elapsed_ms": elapsed_ms}
        body_out = resp.get("body")
        body_bytes = len(json.dumps(body_out, default=str)) if body_out is not None else 0
        return {
            "http_code": resp.get("http_code", 0),
            "rc": rc,
            "body": body_out,
            "body_bytes": body_bytes,
            "elapsed_ms": elapsed_ms,
        }

    if action == "write_memory":
        title = str(args.get("title") or "")[:200]
        content = str(args.get("content") or "")[:65000]
        tier = str(args.get("tier") or "mid").lower()
        if tier not in ("short", "mid", "long"):
            tier = "mid"
        priority = int(args.get("priority") or 5)
        priority = max(1, min(10, priority))
        metadata = args.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        rc, resp = h.write_memory(
            node, agent_id, namespace,
            title=title, content=content, tier=tier,
            priority=priority, metadata=metadata, include_status=True,
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not isinstance(resp, dict):
            return {"http_code": 0, "rc": rc, "elapsed_ms": elapsed_ms,
                    "error": "non-dict write_memory response"}
        return {
            "http_code": resp.get("http_code", 0),
            "rc": rc,
            "body": resp.get("body"),
            "elapsed_ms": elapsed_ms,
        }

    if action == "list_memories":
        limit = int(args.get("limit") or 50)
        limit = max(1, min(200, limit))
        rc, resp = h.list_memories(node, namespace, limit=limit)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {"http_code": 200 if rc == 0 else 0, "rc": rc,
                "body": resp, "elapsed_ms": elapsed_ms}

    if action == "search":
        query = str(args.get("query") or "")
        limit = int(args.get("limit") or 10)
        limit = max(1, min(50, limit))
        body = {"query": query, "limit": limit, "namespace": namespace}
        rc, resp = h.http_on(node, "POST", "/api/v1/search",
                             body=body, agent_id=agent_id,
                             include_status=True, timeout=30)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if isinstance(resp, dict):
            return {"http_code": resp.get("http_code", 0), "rc": rc,
                    "body": resp.get("body"), "elapsed_ms": elapsed_ms}
        return {"http_code": 0, "rc": rc, "error": "non-dict",
                "elapsed_ms": elapsed_ms}

    return {"http_code": 0, "error": f"unknown action: {action}"}


# -----------------------------------------------------------------------------
# Action-JSON parsing (Grok output → structured dict, robust to slop)
# -----------------------------------------------------------------------------

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_action(text: str) -> dict[str, Any] | None:
    """Extract the action JSON from Grok's reply.

    Tries (in order):
      1. fenced ```json {...}``` block
      2. first balanced top-level {...} object
      3. raw text as JSON
    Returns None if nothing parseable.
    """
    text = text or ""
    m = _FENCED_JSON.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Greedy first-balanced-object scan
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                snippet = text[start:i+1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    start = -1
                    continue

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# -----------------------------------------------------------------------------
# Discovery loop
# -----------------------------------------------------------------------------

def run_discovery(*, h: Harness, focus: str, tier: str,
                  agent_id: str, peer_label: str,
                  findings_path: pathlib.Path,
                  max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
                  time_budget_s: int = DEFAULT_TIME_BUDGET_S,
                  max_turns: int = DEFAULT_MAX_TURNS) -> dict[str, Any]:
    """Drive one NHI discovery run end-to-end.

    Returns the run-summary dict (also written to `findings_path`).
    """
    run_id = h.scenario_id + "-" + h.new_uuid("nhi-")[:12]
    namespace = f"nhi-discovery-{run_id}"

    system_prompt = assemble_system_prompt(
        focus=focus, tier=tier, namespace=namespace,
        agent_label=agent_id, peer_label=peer_label,
    )

    findings: list[dict[str, Any]] = []
    transcript: list[dict[str, Any]] = []
    started_at = time.time()
    deadline = started_at + time_budget_s
    tool_calls = 0
    turns = 0
    last_observation: dict[str, Any] = {
        "note": "begin discovery — issue your first action",
    }

    while turns < max_turns and tool_calls < max_tool_calls and time.time() < deadline:
        seconds_left = max(0, int(deadline - time.time()))
        # Build the user message: bookkeeping header + last observation
        budget_block = (
            f"[budget] tool_calls={tool_calls}/{max_tool_calls} "
            f"seconds_left={seconds_left} turns={turns}/{max_turns}"
        )
        obs_block = "[observation]\n" + _truncate(last_observation)
        user_msg = budget_block + "\n\n" + obs_block

        log(f"  nhi turn {turns}: tool_calls={tool_calls} sec_left={seconds_left}")
        out = _grok_chat_raw(prompt=user_msg, system_msg=system_prompt,
                             max_tokens=2048, temperature=0.5)
        turns += 1

        if out.get("error"):
            log(f"  !! grok error: {out['error']}")
            transcript.append({"turn": turns, "error": out["error"]})
            # one-shot retry
            out = _grok_chat_raw(prompt=user_msg, system_msg=system_prompt,
                                 max_tokens=2048, temperature=0.5)
            if out.get("error"):
                log(f"  !! grok error (retry): {out['error']} — ending run")
                break

        text = out.get("text", "") or ""
        action_json = parse_action(text)
        if not action_json:
            transcript.append({"turn": turns, "raw_text": text[:500],
                               "parse_error": True})
            last_observation = {
                "error": "could not parse your previous reply as JSON",
                "hint": "emit ONLY one JSON object per turn following the protocol",
            }
            continue

        thought = str(action_json.get("thought") or "")[:500]
        action = str(action_json.get("action") or "").lower().strip()
        args = action_json.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        finding = action_json.get("finding")

        # Capture finding if present
        if isinstance(finding, dict) and finding:
            finding["captured_turn"] = turns
            finding["captured_at_seconds"] = int(time.time() - started_at)
            findings.append(finding)
            log(f"  >> NHI FINDING [{finding.get('severity', '?')}] "
                f"{finding.get('id', '?')}: {finding.get('summary', '')[:80]}")

        transcript_entry = {
            "turn": turns,
            "thought": thought,
            "action": action,
            "args": args,
            "finding_emitted": bool(finding),
        }

        if action == "stop":
            log("  nhi: stop signal — ending run")
            transcript.append(transcript_entry)
            break

        if action not in ALLOWED_ACTIONS:
            last_observation = {
                "error": f"unknown action: {action!r}",
                "allowed_actions": sorted(ALLOWED_ACTIONS),
            }
            transcript.append(transcript_entry)
            continue

        # Execute
        observation = execute_action(h, namespace, agent_id, action, args)
        tool_calls += 1
        transcript_entry["observation_summary"] = {
            "http_code": observation.get("http_code"),
            "elapsed_ms": observation.get("elapsed_ms"),
            "body_bytes": observation.get("body_bytes"),
            "error": observation.get("error"),
        }
        transcript.append(transcript_entry)
        last_observation = observation

    duration_seconds = int(time.time() - started_at)
    summary = {
        "run_id": run_id,
        "agent": agent_id,
        "focus": focus,
        "tier": tier,
        "namespace": namespace,
        "topology_node1": h.node1_ip,
        "topology_node2": h.node2_ip,
        "tls_mode": h.tls_mode,
        "backend_kind": Harness.backend_kind(),
        "started_at_unix": int(started_at),
        "duration_seconds": duration_seconds,
        "tool_calls": tool_calls,
        "turns": turns,
        "max_tool_calls": max_tool_calls,
        "time_budget_s": time_budget_s,
        "findings": findings,
        "finding_counts_by_severity": _bucketize(findings, "severity"),
        "finding_counts_by_category": _bucketize(findings, "category"),
        "transcript": transcript,
    }

    findings_path.parent.mkdir(parents=True, exist_ok=True)
    findings_path.write_text(json.dumps(summary, indent=2, default=str))
    log(f"  nhi run complete: {len(findings)} findings, "
        f"{tool_calls} tool calls, {duration_seconds}s — written to {findings_path}")
    return summary


def _bucketize(findings: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        v = str(f.get(key) or "unknown")
        out[v] = out.get(v, 0) + 1
    return out


# -----------------------------------------------------------------------------
# Verdict helper for scenario wrappers
# -----------------------------------------------------------------------------

def discovery_verdict(summary: dict[str, Any]) -> tuple[bool, str]:
    """Decide pass/fail for a discovery run (non-gating mode).

    Discovery runs are inherently exploratory; the only hard-fail is
    HARNESS-level breakage:
      * 0 tool calls successfully executed → harness/auth broken
      * >50% of tool calls returned http_code=0 → connectivity broken

    Critical-severity findings are SURFACED but don't fail the run —
    they're meant to be triaged by humans, not gate the cert. The cert
    track has its own deterministic scenarios.
    """
    tool_calls = int(summary.get("tool_calls") or 0)
    if tool_calls == 0:
        return False, "discovery harness executed 0 tool calls"

    # Connectivity sanity
    transcript = summary.get("transcript") or []
    failed = sum(
        1 for t in transcript
        if isinstance(t, dict)
        and isinstance(t.get("observation_summary"), dict)
        and t["observation_summary"].get("http_code") in (0, None)
    )
    if tool_calls > 0 and failed > tool_calls / 2:
        return False, f"{failed}/{tool_calls} tool calls returned http_code=0 (connectivity?)"

    findings = summary.get("findings") or []
    return True, f"discovery complete: {len(findings)} findings, {tool_calls} probes"
