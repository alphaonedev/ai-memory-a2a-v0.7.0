#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Round-1 / Round-2 driver for the v0.7.0 A2A campaign.

Walks every scenario file in scenarios/, runs it with a per-scenario
timeout, captures stdout (JSON) → runs/<campaign>/scenario-N.json and
stderr → runs/<campaign>/scenario-N.log.

Reads scripts/scope-v0.7.0.json at startup and emits clean SKIP records
for scenarios in skip_3_agent / skip_mcp_stdio / skip_other lists
WITHOUT executing them. In-scope scenarios are dispatched normally.

Aggregates pass/fail/skip into runs/<campaign>/a2a-summary.json
matching the schema render_pages.py expects.

Wave 4 (2026-05-08): supports --backend-kind {sqlite|postgres|mixed}.
When postgres or mixed, the runner sets A2A_BACKEND_KIND for each
scenario's child env so S77-S82 dispatch on the postgres production
path; sqlite mode keeps the existing 56/68 baseline behavior. The
runner itself does NOT re-deploy daemons — that is the responsibility
of scripts/deploy_wave4.sh which is invoked separately by the orchestrator.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone


REPO = Path(__file__).resolve().parent.parent
SCENARIOS = REPO / "scenarios"
SCOPE_PATH = REPO / "scripts" / "scope-v0.7.0.json"


def load_scope() -> dict:
    """Read scripts/scope-v0.7.0.json and return a normalized dispatch map.

    Track-aware: when A2A_TRACK=Q (the v0.7.0 GPU cert quad-openclaw
    topology), the q_track_in_scope scenarios (S14, S25, S39 — which
    require a distinct 3rd-and-4th daemon node) move from skip_3_agent
    into in_scope. Other tracks see the legacy 2-node CPU behavior.

    Returns:
        {
            "in_scope": set[str],
            "skips": {scenario_id: skip_reason, ...},
            "campaign_scope": "...",
            "track": "...",
        }
    """
    with open(SCOPE_PATH, "r", encoding="utf-8") as f:
        m = json.load(f)
    track = os.environ.get("A2A_TRACK", "").strip().upper()
    in_scope = {s["id"] for s in m.get("in_scope", [])}
    if track == "Q":
        for s in m.get("q_track_in_scope", []):
            in_scope.add(s["id"])
    skips: dict[str, str] = {}
    skip_keys = ("skip_3_agent", "skip_mcp_stdio", "skip_other")
    for key in skip_keys:
        for s in m.get(key, []):
            sid = s["id"]
            # On Q track, scenarios that moved into q_track_in_scope are
            # NOT skipped despite still being listed in skip_3_agent (we
            # keep both lists for documentation symmetry).
            if track == "Q" and sid in in_scope:
                continue
            skips[sid] = s.get("rationale", f"skipped via {key}")
    return {
        "in_scope": in_scope,
        "skips": skips,
        "campaign_scope": m.get("campaign_scope", ""),
        "track": track or "default",
    }


def scenario_id_from_filename(p: Path) -> str:
    # 1b_write_read_http.py -> "1b" ; 70_pg_migration_roundtrip.py -> "70"
    m = re.match(r"^(\d+[a-z]?)_", p.name)
    return m.group(1) if m else p.stem


def per_scenario_timeout(sid: str) -> int:
    """Per-scenario wall-time budget in seconds."""
    # postgres + AGE + perf scenarios need more headroom
    if sid in {"70", "71", "72", "73", "75", "76"}:
        return 300
    if sid in {"74"}:
        return 120
    # Grok-driven scenarios may stall on xAI latency
    if sid in {"67", "68"}:
        return 240
    # Bulk + concurrency
    if sid in {"4", "13", "40", "62", "69"}:
        return 180
    # 1000-row burst takes >120s under federation quorum_writes=2.
    if sid in {"61"}:
        return 300
    # Wave 4 postgres-mode scenarios — restart, AGE bench, recall sweep
    if sid in {"77"}:
        return 90  # capabilities probe + cmdline check; lightweight
    if sid in {"78"}:
        return 240  # daemon restart + audit fsync settle
    if sid in {"79"}:
        return 240  # 50 seeds + 10 recall queries
    if sid in {"80"}:
        return 120  # governance write matrix
    if sid in {"81"}:
        return 240  # bidirectional federation seed + settle
    if sid in {"82"}:
        return 180  # 10-node KG seed + AGE Cypher path query
    # NHI discovery (S83/84 single-agent, S85 bilateral) — 30-min budget
    # plus harness overhead. Override via NHI_TIME_BUDGET_S in scenario env.
    if sid in {"83", "84"}:
        return 2100   # ~35 min: NHI_TIME_BUDGET_S=1800 + harness/grok overhead
    if sid in {"85"}:
        return 2400   # ~40 min: bilateral S83+S84 in parallel + consensus
    # Default
    return 120


def run_one(sid: str, scenario_path: Path, run_dir: Path,
            backend_kind: str = "sqlite") -> dict:
    json_path = run_dir / f"scenario-{sid}.json"
    log_path = run_dir / f"scenario-{sid}.log"
    timeout = per_scenario_timeout(sid)
    # Inherit parent env + force A2A_BACKEND_KIND so the harness's
    # module-level read picks it up uniformly across every child.
    child_env = dict(os.environ)
    child_env["A2A_BACKEND_KIND"] = backend_kind
    t0 = time.time()
    try:
        r = subprocess.run(
            ["python3", str(scenario_path)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(REPO), env=child_env,
        )
        elapsed = time.time() - t0
        log_path.write_text(r.stderr or "")
        out = (r.stdout or "").strip()
        # Find the last JSON line in stdout (some scenarios may emit log lines first)
        last_json = None
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    last_json = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if last_json is None:
            doc = {
                "scenario": sid, "pass": False, "skipped": False,
                "reason": f"no JSON report on stdout (rc={r.returncode}); stderr tail: "
                          + (r.stderr or "")[-300:],
                "elapsed_sec": round(elapsed, 1),
                "harness_error": True,
            }
        else:
            doc = last_json
            doc["elapsed_sec"] = round(elapsed, 1)
        json_path.write_text(json.dumps(doc, sort_keys=True, indent=2))
        return doc
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - t0
        # captured.stdout / .stderr may be bytes
        stderr_text = (e.stderr or b"")
        if isinstance(stderr_text, bytes):
            stderr_text = stderr_text.decode("utf-8", "replace")
        log_path.write_text(stderr_text)
        doc = {
            "scenario": sid, "pass": False, "skipped": False,
            "reason": f"scenario exceeded {timeout}s timeout",
            "elapsed_sec": round(elapsed, 1),
            "harness_error": "timeout",
        }
        json_path.write_text(json.dumps(doc, sort_keys=True, indent=2))
        return doc
    except Exception as exc:
        elapsed = time.time() - t0
        doc = {
            "scenario": sid, "pass": False, "skipped": False,
            "reason": f"runner exception: {type(exc).__name__}: {exc}",
            "elapsed_sec": round(elapsed, 1),
            "harness_error": "runner_exception",
        }
        json_path.write_text(json.dumps(doc, sort_keys=True, indent=2))
        return doc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Round driver for v0.7.0 A2A campaign (sqlite or postgres backend)."
    )
    parser.add_argument(
        "--backend-kind",
        choices=("sqlite", "postgres", "mixed"),
        default=os.environ.get("A2A_BACKEND_KIND", "sqlite"),
        help=(
            "Daemon storage backend topology. 'sqlite' (default, Wave 1-3 baseline) "
            "runs daemons with --db <sqlite-path>. 'postgres' (Wave 4) requires "
            "Continuation 3 + scripts/deploy_wave4.sh to have run. 'mixed' is "
            "openclaw=sqlite + hermes=postgres for heterogeneous federation tests."
        ),
    )
    args = parser.parse_args()
    backend_kind = args.backend_kind

    campaign_id = os.environ.get("CAMPAIGN_ID")
    if not campaign_id:
        print("CAMPAIGN_ID env var required", file=sys.stderr)
        sys.exit(2)
    round_label = os.environ.get("ROUND_LABEL", "Round 1")

    only = os.environ.get("ONLY", "").strip()
    skip_extra = set(s.strip() for s in os.environ.get("SKIP", "").split(",") if s.strip())

    scope = load_scope()
    in_scope: set[str] = scope["in_scope"]
    scope_skips: dict[str, str] = scope["skips"]
    campaign_scope = scope["campaign_scope"]
    print(
        f"scope: {len(in_scope)} in-scope; {len(scope_skips)} pre-classified skips; "
        f"backend_kind={backend_kind}",
        file=sys.stderr,
    )

    run_dir = REPO / "runs" / campaign_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "findings").mkdir(exist_ok=True)

    files = sorted(SCENARIOS.glob("*.py"),
                   key=lambda p: tuple(int(x) if x.isdigit() else x
                                       for x in re.split(r"(\d+)", p.name) if x))
    if only:
        wanted = set(s.strip() for s in only.split(",") if s.strip())
        files = [f for f in files if scenario_id_from_filename(f) in wanted]

    print(f"campaign={campaign_id} scenarios={len(files)} run_dir={run_dir}", file=sys.stderr)

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t_start = time.time()

    results: list[dict] = []
    for f in files:
        sid = scenario_id_from_filename(f)
        # Scope-based skip: do not execute, emit a clean per-scenario JSON.
        if sid in scope_skips:
            reason = scope_skips[sid]
            print(f"  [{sid}] SKIP (scope) — {reason[:80]}", file=sys.stderr)
            doc = {
                "scenario": sid,
                "pass": None,
                "skipped": True,
                "skip_reason": reason,
                "agent_group": "openclaw_hermes",
                "campaign_scope": campaign_scope,
                "elapsed_sec": 0,
            }
            (run_dir / f"scenario-{sid}.json").write_text(
                json.dumps(doc, sort_keys=True, indent=2))
            results.append(doc)
            continue
        if sid in skip_extra:
            print(f"  [{sid}] SKIP (operator override)", file=sys.stderr)
            doc = {"scenario": sid, "pass": None, "skipped": True,
                   "skip_reason": "operator override",
                   "agent_group": "openclaw_hermes",
                   "campaign_scope": campaign_scope,
                   "elapsed_sec": 0}
            (run_dir / f"scenario-{sid}.json").write_text(json.dumps(doc, sort_keys=True, indent=2))
            results.append(doc)
            continue
        if sid not in in_scope:
            # No classification at all — skip-defensive (manifest is SOT).
            print(f"  [{sid}] SKIP (not in manifest)", file=sys.stderr)
            doc = {"scenario": sid, "pass": None, "skipped": True,
                   "skip_reason": "scenario not listed in scripts/scope-v0.7.0.json",
                   "agent_group": "openclaw_hermes",
                   "campaign_scope": campaign_scope,
                   "elapsed_sec": 0}
            (run_dir / f"scenario-{sid}.json").write_text(json.dumps(doc, sort_keys=True, indent=2))
            results.append(doc)
            continue
        print(f"  [{sid}] running ({f.name}) ...", file=sys.stderr, flush=True)
        doc = run_one(sid, f, run_dir, backend_kind=backend_kind)
        verdict = "SKIP" if doc.get("skipped") else ("PASS" if doc.get("pass") else "FAIL")
        reason = doc.get("reason") or ""
        if reason and len(reason) > 100:
            reason = reason[:100] + "..."
        print(f"  [{sid}] {verdict} ({doc.get('elapsed_sec', '?')}s) {reason}",
              file=sys.stderr, flush=True)
        results.append(doc)

    elapsed = time.time() - t_start
    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")

    n_total = len(results)
    n_pass = sum(1 for r in results if r.get("pass") is True)
    n_fail = sum(1 for r in results if r.get("pass") is False and not r.get("skipped"))
    n_skip = sum(1 for r in results if r.get("skipped"))

    # GREEN definition: NO in-scope scenario reported pass=False.
    # In-scope scenarios that legitimately self-skip (e.g. S20/S21 only
    # run under TLS_MODE=mtls and the campaign is tls=off) are counted
    # as SKIP, not FAIL — they don't block GREEN.
    in_scope_results = [r for r in results if r.get("scenario") in in_scope]
    n_in_scope = len(in_scope_results)
    n_in_scope_pass = sum(1 for r in in_scope_results if r.get("pass") is True)
    n_in_scope_fail = sum(1 for r in in_scope_results if r.get("pass") is False and not r.get("skipped"))
    n_in_scope_skip = sum(1 for r in in_scope_results if r.get("skipped"))
    overall = (n_in_scope_fail == 0 and n_in_scope > 0 and
               n_in_scope_pass + n_in_scope_skip == n_in_scope)

    summary = {
        "campaign_id": campaign_id,
        "round": round_label,
        "started_utc": started,
        "finished_utc": finished,
        "wall_seconds": round(elapsed, 1),
        "total": n_total,
        "passed": n_pass,
        "failed": n_fail,
        "skipped": n_skip,
        "in_scope_total": n_in_scope,
        "in_scope_passed": n_in_scope_pass,
        "in_scope_failed": n_in_scope_fail,
        "overall_pass": overall,
        "campaign_scope": campaign_scope,
        "backend_kind": backend_kind,
        "subject_under_test": "ai-memory v0.7.0 (round-2-fixes @ e0d2086, post F1+F2 fixes)",
        "topology": {
            "openclaw": "104.236.52.203 (10.20.0.2)",
            "hermes": "142.93.72.46 (10.20.0.3)",
            "postgres": "68.183.157.68 (10.20.0.4)",
            "tls_mode": "off",
            "agent_group": "openclaw_hermes",
            "backend_kind": backend_kind,
        },
        "scenarios": [
            {
                "scenario": r.get("scenario"),
                "pass": r.get("pass"),
                "skipped": r.get("skipped", False),
                "reason": r.get("reason", "") or r.get("skip_reason", ""),
                "elapsed_sec": r.get("elapsed_sec", 0),
                "in_scope": r.get("scenario") in in_scope,
            }
            for r in results
        ],
    }
    (run_dir / "a2a-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))

    print(f"\n=== {round_label} complete ===", file=sys.stderr)
    print(f"  total       : {n_total}", file=sys.stderr)
    print(f"  in-scope    : {n_in_scope}", file=sys.stderr)
    print(f"  in-scope ✓ : {n_in_scope_pass}", file=sys.stderr)
    print(f"  in-scope ✗ : {n_in_scope_fail}", file=sys.stderr)
    print(f"  skipped     : {n_skip}", file=sys.stderr)
    print(f"  wall        : {elapsed:.1f}s", file=sys.stderr)
    print(f"  verdict     : {'GREEN' if overall else 'NOT GREEN'}", file=sys.stderr)


if __name__ == "__main__":
    main()
