#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Round-1 driver for the v0.7.0 A2A campaign.

Walks every scenario file in scenarios/, runs it with a per-scenario
timeout, captures stdout (JSON) → runs/<campaign>/scenario-N.json and
stderr → runs/<campaign>/scenario-N.log.

Aggregates pass/fail/skip into runs/<campaign>/a2a-summary.json
matching the schema render_pages.py expects.
"""
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
    # Default
    return 120


def run_one(sid: str, scenario_path: Path, run_dir: Path) -> dict:
    json_path = run_dir / f"scenario-{sid}.json"
    log_path = run_dir / f"scenario-{sid}.log"
    timeout = per_scenario_timeout(sid)
    t0 = time.time()
    try:
        r = subprocess.run(
            ["python3", str(scenario_path)],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(REPO),
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
    campaign_id = os.environ.get("CAMPAIGN_ID")
    if not campaign_id:
        print("CAMPAIGN_ID env var required", file=sys.stderr)
        sys.exit(2)

    only = os.environ.get("ONLY", "").strip()
    skip = set(s.strip() for s in os.environ.get("SKIP", "").split(",") if s.strip())

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
        if sid in skip:
            print(f"  [{sid}] SKIP (operator override)", file=sys.stderr)
            doc = {"scenario": sid, "pass": None, "skipped": True,
                   "reason": "operator override", "elapsed_sec": 0}
            (run_dir / f"scenario-{sid}.json").write_text(json.dumps(doc, sort_keys=True, indent=2))
            results.append(doc)
            continue
        print(f"  [{sid}] running ({f.name}) ...", file=sys.stderr, flush=True)
        doc = run_one(sid, f, run_dir)
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
    overall = (n_fail == 0 and n_pass + n_skip == n_total)

    summary = {
        "campaign_id": campaign_id,
        "round": "Round 1",
        "started_utc": started,
        "finished_utc": finished,
        "wall_seconds": round(elapsed, 1),
        "total": n_total,
        "passed": n_pass,
        "failed": n_fail,
        "skipped": n_skip,
        "overall_pass": overall,
        "subject_under_test": "ai-memory v0.7.0 (round-2-fixes @ dfb184f)",
        "topology": {
            "openclaw": "104.236.52.203 (10.20.0.2)",
            "hermes": "142.93.72.46 (10.20.0.3)",
            "postgres": "68.183.157.68 (10.20.0.4)",
            "tls_mode": "off",
            "agent_group": "openclaw_hermes",
        },
        "scenarios": [
            {
                "scenario": r.get("scenario"),
                "pass": r.get("pass"),
                "skipped": r.get("skipped", False),
                "reason": r.get("reason", ""),
                "elapsed_sec": r.get("elapsed_sec", 0),
            }
            for r in results
        ],
    }
    (run_dir / "a2a-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))

    print(f"\n=== Round 1 complete ===", file=sys.stderr)
    print(f"  total : {n_total}", file=sys.stderr)
    print(f"  pass  : {n_pass}", file=sys.stderr)
    print(f"  fail  : {n_fail}", file=sys.stderr)
    print(f"  skip  : {n_skip}", file=sys.stderr)
    print(f"  wall  : {elapsed:.1f}s", file=sys.stderr)
    print(f"  verdict: {'GREEN' if overall else 'NOT GREEN'}", file=sys.stderr)


if __name__ == "__main__":
    main()
