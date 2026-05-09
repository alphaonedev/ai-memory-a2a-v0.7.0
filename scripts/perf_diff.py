#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""perf_diff — render perf-vs-baseline table for the v0.7.0 cert run.

Reads:
  baseline run dir  (postgres-backed, plain HTTP)
  candidate run dir (postgres-backed, HTTPS+mTLS)

Per scenario that PASSED in BOTH:
  - elapsed_sec delta (mtls - plain)
  - tls_handshake stats from candidate (count, p50/p99 if >=2 samples, total)

Renders a markdown table to stdout.
"""
import argparse
import json
import sys
from pathlib import Path
from statistics import median


def load_scenarios(run_dir: Path) -> dict[str, dict]:
    """Load every scenario-*.json from a run dir, keyed by scenario id."""
    out: dict[str, dict] = {}
    for p in sorted(run_dir.glob("scenario-*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print(f"  warn: cannot read {p.name}: {e}", file=sys.stderr)
            continue
        sid = d.get("scenario") or p.stem.removeprefix("scenario-")
        out[str(sid)] = d
    return out


def fmt_delta(delta: float, baseline: float) -> str:
    if baseline <= 0:
        return f"{delta:+.2f}s"
    pct = (delta / baseline) * 100
    return f"{delta:+.2f}s ({pct:+.1f}%)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True, help="baseline run directory")
    ap.add_argument("--candidate", required=True, help="candidate run directory")
    ap.add_argument("--out", default="-", help="output path (- for stdout)")
    args = ap.parse_args()

    base_dir = Path(args.baseline)
    cand_dir = Path(args.candidate)
    if not base_dir.is_dir():
        print(f"ERROR: baseline dir missing: {base_dir}", file=sys.stderr)
        return 2
    if not cand_dir.is_dir():
        print(f"ERROR: candidate dir missing: {cand_dir}", file=sys.stderr)
        return 2

    base = load_scenarios(base_dir)
    cand = load_scenarios(cand_dir)

    # Pair scenarios that PASS in both runs
    rows: list[dict] = []
    for sid in sorted(set(base.keys()) | set(cand.keys()),
                      key=lambda s: (len(s), s)):
        b = base.get(sid)
        c = cand.get(sid)
        if not b or not c:
            continue
        if b.get("pass") is not True or c.get("pass") is not True:
            continue
        b_sec = float(b.get("elapsed_sec", 0) or 0)
        c_sec = float(c.get("elapsed_sec", 0) or 0)
        delta = c_sec - b_sec
        tls = c.get("tls_handshake") or {}
        rows.append({
            "scenario": sid,
            "baseline_sec": b_sec,
            "mtls_sec": c_sec,
            "delta_sec": delta,
            "delta_pct": (delta / b_sec * 100) if b_sec > 0 else None,
            "tls_count": tls.get("count", 0),
            "tls_mean_ms": (tls.get("mean_seconds", 0) or 0) * 1000.0,
            "tls_max_ms": (tls.get("max_seconds", 0) or 0) * 1000.0,
            "tls_total_s": tls.get("total_seconds", 0) or 0,
        })

    # Aggregate stats over scenarios that passed in both
    total_baseline = sum(r["baseline_sec"] for r in rows)
    total_mtls = sum(r["mtls_sec"] for r in rows)
    total_delta = total_mtls - total_baseline
    total_handshakes = sum(r["tls_count"] for r in rows)
    total_handshake_seconds = sum(r["tls_total_s"] for r in rows)
    handshake_means = [r["tls_mean_ms"] for r in rows if r["tls_count"] > 0]
    handshake_maxes = [r["tls_max_ms"] for r in rows if r["tls_count"] > 0]

    out_lines: list[str] = []
    out_lines.append("# Perf vs plain-HTTP baseline\n")
    out_lines.append(f"- Baseline run: `{base_dir.name}` (postgres, plain HTTP)\n")
    out_lines.append(f"- Candidate run: `{cand_dir.name}` (postgres, HTTPS+mTLS)\n")
    out_lines.append(f"- Scenarios that passed in BOTH runs: **{len(rows)}**\n")
    out_lines.append("")
    out_lines.append("## Aggregate")
    out_lines.append("")
    out_lines.append(f"- Total baseline wall: **{total_baseline:.1f}s**")
    out_lines.append(f"- Total mTLS wall:     **{total_mtls:.1f}s**")
    out_lines.append(f"- Total delta:         **{fmt_delta(total_delta, total_baseline)}**")
    out_lines.append(f"- Total TLS handshakes (mTLS run): **{total_handshakes}**")
    out_lines.append(f"- Total handshake time:            **{total_handshake_seconds:.3f}s** "
                     f"({(total_handshake_seconds / total_mtls * 100 if total_mtls > 0 else 0):.2f}% "
                     f"of mTLS run wall)")
    if handshake_means:
        out_lines.append(f"- Per-scenario handshake mean (median across scenarios): "
                         f"**{median(handshake_means):.2f}ms**")
        out_lines.append(f"- Per-scenario handshake mean (max across scenarios):    "
                         f"**{max(handshake_means):.2f}ms**")
    if handshake_maxes:
        out_lines.append(f"- Per-scenario handshake max (median across scenarios):  "
                         f"**{median(handshake_maxes):.2f}ms**")
        out_lines.append(f"- Per-scenario handshake max (max across scenarios):     "
                         f"**{max(handshake_maxes):.2f}ms**")
    out_lines.append("")
    out_lines.append("## Per-scenario (PASS in both)")
    out_lines.append("")
    out_lines.append("| Scenario | Baseline | mTLS | Delta | TLS handshakes | Mean handshake | Max handshake |")
    out_lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        out_lines.append(
            f"| S{r['scenario']} "
            f"| {r['baseline_sec']:.1f}s "
            f"| {r['mtls_sec']:.1f}s "
            f"| {fmt_delta(r['delta_sec'], r['baseline_sec'])} "
            f"| {r['tls_count']} "
            f"| {r['tls_mean_ms']:.2f}ms "
            f"| {r['tls_max_ms']:.2f}ms |"
        )
    out_lines.append("")
    out_lines.append("## Caveats")
    out_lines.append("")
    out_lines.append("- pgvector + AGE CPU cost is independent of TLS — perf delta below "
                     "captures the TLS-handshake-and-data-encryption overhead only.")
    out_lines.append("- Each scenario opens fresh curl invocations from the orchestrator-host "
                     "ssh, so handshakes don't amortize via connection pooling. The TLS overhead "
                     "below is therefore an upper bound vs a long-lived federation client.")
    out_lines.append("- Wall-clock numbers include ssh round-trip latency (orchestrator <-> "
                     "droplet); the TLS handshake measurement (`time_appconnect - time_connect`) "
                     "isolates the ssl portion only.")
    out_lines.append("")

    text = "\n".join(out_lines)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
