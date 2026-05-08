#!/usr/bin/env python3
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
render_pages.py — produce docs/index.html from runs/<campaign-id>/a2a-summary.json.

Reads:
  runs/<campaign-id>/a2a-summary.json   (one or many — newest wins for top-of-fold)
  runs/<campaign-id>/<scenario>.json    (per-scenario reports)

Writes:
  docs/index.html                       (full page; replaces the placeholder)
  docs/runs/<campaign-id>.md            (mkdocs page per campaign)
  docs/runs/index.md                    (campaign index table)

The HTML/CSS vocabulary mirrors test-hub's `releases/v0.7.0/index.html`:
classes `verdict.ship`, `metric`, `cards`, `card`, `head`, `id`, `sev.closed`,
`sev.p2`, `lede`, `note.block`, `eyebrow`, `links-row`, `container`; CSS vars
`--good`, `--p1`, `--p2`, `--p3`, `--bad`.

The page template lives next to this script as `_index_template.html` (created
on first run). Re-running with new run data is idempotent.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone


REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"
DOCS = REPO / "docs"


def load_summary(run_dir: Path) -> dict:
    f = run_dir / "a2a-summary.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except json.JSONDecodeError:
        return {}


def load_scenarios(run_dir: Path) -> list[dict]:
    out = []
    for f in sorted(run_dir.glob("scenario-*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            pass
    return out


def verdict_class(passed: bool | None) -> str:
    if passed is None:    return "hold"
    return "ship" if passed else "block"


def render_card(rec: dict) -> str:
    sid = rec.get("scenario", "?")
    p   = rec.get("pass")
    sk  = rec.get("skipped", False)
    if sk:        sev = "p3"; verb = "SKIP"
    elif p:       sev = "closed"; verb = "PASS"
    else:         sev = "p2"; verb = "FAIL"
    reason = rec.get("reason") or ""
    if reason and len(reason) > 240:
        reason = reason[:237] + "..."
    return (
        f'<div class="card finding"><div class="head">'
        f'<span class="id">S{sid}</span><span class="sev {sev}">{verb}</span></div>'
        f'<p>{reason or "&nbsp;"}</p></div>'
    )


def find_round_run(runs: list[Path], label: str) -> Path | None:
    """Find the most recent run with the given round label (Round 1 or Round 2)."""
    for r in runs:
        s = load_summary(r)
        if s.get("round") == label:
            return r
    return None


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "runs").mkdir(parents=True, exist_ok=True)

    runs = sorted([d for d in RUNS.iterdir() if d.is_dir() and (d / "a2a-summary.json").exists()],
                  key=lambda d: d.name, reverse=True)

    if not runs:
        # First-deploy placeholder.
        html = PLACEHOLDER
    else:
        # Newest run drives the per-scenario card grid.
        latest = runs[0]
        summary = load_summary(latest)
        scenarios = load_scenarios(latest)
        n_total = summary.get("total") or len(scenarios)
        # Prefer in-scope counters from the new schema; fall back gracefully.
        n_in_scope = summary.get("in_scope_total") or summary.get("total") or len(scenarios)
        n_in_scope_pass = summary.get("in_scope_passed", summary.get("passed", 0))
        n_in_scope_fail = summary.get("in_scope_failed", summary.get("failed", 0))
        n_pass = summary.get("passed", n_in_scope_pass) or 0
        n_fail = summary.get("failed", n_in_scope_fail) or 0
        n_skip = summary.get("skipped", 0) or 0
        cards_html = "\n".join(render_card(r) for r in scenarios)

        # Compute verdicts for both rounds independently (Round 1 + Round 2
        # may live in two different run directories).
        r1 = find_round_run(runs, "Round 1")
        r2 = find_round_run(runs, "Round 2")
        r1_pass = load_summary(r1).get("overall_pass") if r1 else None
        r2_pass = load_summary(r2).get("overall_pass") if r2 else None

        # Hero note: GREEN-gate banner when both rounds pass; otherwise nothing.
        gate_note = ""
        if r1_pass is True and r2_pass is True:
            gate_note = (
                '<div class="note block" style="border-left-color:var(--good);'
                'background:rgba(110,231,255,0.06);color:var(--text);'
                'max-width:780px;margin:1.5rem auto 0;text-align:left">'
                '<strong>Two-round GREEN gate satisfied.</strong> '
                'Both Round 1 and Round 2 reported 100% PASS on every '
                'in-scope scenario for the v0.7.0 A2A 2-agent topology '
                '(openclaw ↔ hermes). Out-of-scope scenarios that require '
                'a 3rd distinct daemon or the MCP-stdio path are deferred '
                'to v0.7.1.</div>'
            )

        html = PAGE.format(
            run_id=latest.name,
            generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            verdict_label_round1=("ship" if r1_pass else ("hold" if r1 is None else "block")).upper(),
            verdict_label_round2=("ship" if r2_pass else ("hold" if r2 is None else "block")).upper(),
            verdict_class_round1=verdict_class(r1_pass) if r1 is not None else "hold",
            verdict_class_round2=verdict_class(r2_pass) if r2 is not None else "hold",
            n_total=n_total,
            n_in_scope=n_in_scope,
            n_pass=n_pass, n_fail=n_fail, n_skip=n_skip,
            agents=2, droplets=2,
            audit_chain=summary.get("audit_chain_length") or "—",
            gate_note=gate_note,
            cards=cards_html or "<em>No scenarios in this run yet.</em>",
        )

    (DOCS / "index.html").write_text(html)

    # Minimal docs/index.md mkdocs view (mirrors the same data table)
    md_lines = ["# v0.7.0 A2A campaign", ""]
    if not runs:
        md_lines += ["_No runs published yet._"]
    else:
        md_lines += [f"Latest run: **{runs[0].name}**", ""]
        s = load_summary(runs[0])
        md_lines += [f"- scenarios passed: {s.get('passed','—')}/{s.get('total','—')}",
                     f"- overall_pass: `{s.get('overall_pass')}`",
                     f"- generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}"]
    (DOCS / "index.md").write_text("\n".join(md_lines) + "\n")

    # Per-campaign mkdocs pages
    for r in runs:
        s = load_summary(r)
        out = DOCS / "runs" / f"{r.name}.md"
        out.write_text(
            f"# Campaign {r.name}\n\n"
            f"- scenarios passed: {s.get('passed','—')}/{s.get('total','—')}\n"
            f"- overall_pass: `{s.get('overall_pass')}`\n"
            f"- generated: `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`\n"
        )

    idx = DOCS / "runs" / "index.md"
    lines = ["# Campaign runs", "",
             "| Campaign | Round | Pass | Fail | Total | Verdict |",
             "|---|---|---|---|---|---|"]
    for r in runs:
        s = load_summary(r)
        verdict = "PASS" if s.get("overall_pass") else "FAIL"
        lines.append(f"| [{r.name}](./{r.name}.md) | {s.get('round','?')} | "
                     f"{s.get('passed','—')} | {s.get('failed','—')} | "
                     f"{s.get('total','—')} | {verdict} |")
    if not runs:
        lines.append("| _no runs yet_ |  |  |  |  |  |")
    idx.write_text("\n".join(lines) + "\n")
    print(f"render_pages: wrote {DOCS / 'index.html'} + {len(runs)} run pages")


PAGE = r"""<!--
  Copyright 2026 AlphaOne LLC
  SPDX-License-Identifier: Apache-2.0
  Generated by scripts/render_pages.py — DO NOT hand-edit.
  Latest run: {run_id} (regenerated {generated}).
-->
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ai-memory · v0.7.0 A2A · {run_id}</title>
<style>
:root{{--bg:#000;--bg-card:#111;--bg-elev:#161616;--bg-code:#1a1a1a;--border:#262626;--border-hl:#3d3d3d;--text:#fff;--text-muted:#999;--text-dim:#666;--p1:#6ee7ff;--p2:#ffb86b;--p3:#c8a2ff;--good:#6ee7ff;--bad:#ff6b9d;--gold:#ffd700;--font-mono:'SF Mono','Cascadia Code','Fira Code',Consolas,monospace;--font-sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;--max-w:1200px}}
*,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:var(--font-sans);background:var(--bg);color:var(--text);line-height:1.6}}
a{{color:var(--text);text-decoration:none}}a:hover{{color:var(--p1)}}
code{{font-family:var(--font-mono);font-size:.85rem;background:var(--bg-code);padding:.1em .35em;border-radius:4px;color:var(--p1)}}
.container{{max-width:var(--max-w);margin:0 auto;padding:0 1.5rem}}
.hero{{padding:4rem 1.5rem 2.5rem;text-align:center;background:radial-gradient(ellipse at center,rgba(110,231,255,0.08),transparent 60%);border-bottom:1px solid var(--border)}}
.hero h1{{font-size:clamp(2rem,4.5vw,3.25rem);margin-bottom:.85rem;letter-spacing:-0.02em;line-height:1.1}}
.hero .subtitle{{font-family:var(--font-mono);color:var(--text-muted);font-size:.95rem;margin-bottom:1.25rem}}
.hero p.lede{{color:var(--text-muted);max-width:780px;margin:1rem auto 0;font-size:1rem}}
.eyebrow{{display:inline-block;font-size:.72rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--text-muted);margin-bottom:.5rem;padding:.2rem .6rem;border:1px solid var(--border-hl);border-radius:999px}}
.verdict{{display:inline-block;font-family:var(--font-mono);font-size:.95rem;letter-spacing:.08em;text-transform:uppercase;padding:.5em 1.1em;border-radius:6px;margin-bottom:1rem;font-weight:700}}
.verdict.ship{{color:var(--good);background:rgba(110,231,255,0.12);border:1px solid rgba(110,231,255,0.4)}}
.verdict.hold{{color:var(--p2);background:rgba(255,184,107,0.15);border:1px solid rgba(255,184,107,0.5)}}
.verdict.block{{color:var(--bad);background:rgba(255,107,157,0.12);border:1px solid rgba(255,107,157,0.45)}}
.verdict-row{{display:flex;gap:.6rem;justify-content:center;flex-wrap:wrap;margin-bottom:1rem}}
.verdict .label{{font-size:.65rem;display:block;letter-spacing:.12em;color:var(--text-dim);margin-bottom:.15rem;font-weight:500}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;margin-top:2rem}}
.metric{{background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:1.1rem 1.25rem;text-align:left}}
.metric .label{{font-family:var(--font-mono);font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--text-dim);margin-bottom:.4rem}}
.metric .value{{font-family:var(--font-mono);font-size:1.4rem;font-weight:700;color:var(--text)}}
.metric .value.good{{color:var(--good)}}
.metric .value.warn{{color:var(--p2)}}
section{{padding:3rem 0;border-bottom:1px solid var(--border)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:.85rem;margin-top:1.5rem}}
.card{{background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:1.15rem 1.25rem}}
.card .head{{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;margin-bottom:.55rem}}
.card .id{{font-family:var(--font-mono);font-weight:700;color:var(--p1);font-size:.95rem}}
.card .sev{{font-family:var(--font-mono);font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;padding:.2em .55em;border-radius:4px}}
.card .sev.closed{{color:var(--good);background:rgba(110,231,255,0.12)}}
.card .sev.p2{{color:var(--bad);background:rgba(255,107,157,0.12)}}
.card .sev.p3{{color:var(--text-dim);background:rgba(102,102,102,0.15)}}
.card p{{color:var(--text-muted);font-size:.9rem;margin-bottom:.55rem}}
.note.block{{background:var(--bg-elev);border-left:3px solid var(--bad);padding:.85rem 1.1rem;border-radius:0 6px 6px 0;font-size:.88rem;color:var(--text-muted);margin:1rem 0}}
.links-row{{display:flex;gap:.85rem;flex-wrap:wrap;font-family:var(--font-mono);font-size:.82rem;margin-top:1rem}}
.links-row a{{padding:.5rem .95rem;border:1px solid var(--border-hl);border-radius:6px;color:var(--text-muted)}}
footer{{padding:3rem 1.5rem;text-align:center;font-size:.85rem;color:var(--text-dim);border-top:1px solid var(--border)}}
</style></head><body>
<section class="hero">
  <span class="eyebrow">v0.7.0 A2A · 2-round consecutive 100% GREEN gate · openclaw ↔ hermes via Grok 4.2</span>
  <h1>v0.7.0 A2A campaign — {run_id}</h1>
  <p class="subtitle">Two 16 GB DigitalOcean droplets · ai-memory v0.7.0 · grok-4.20-0309-reasoning</p>
  <div class="verdict-row">
    <div class="verdict {verdict_class_round1}"><span class="label">Round 1</span>{verdict_label_round1}</div>
    <div class="verdict {verdict_class_round2}"><span class="label">Round 2</span>{verdict_label_round2}</div>
  </div>
  <p class="lede">Generated {generated}.</p>
  {gate_note}
  <div class="metrics container">
    <div class="metric"><div class="label">Scenarios planned</div><div class="value">{n_total}</div></div>
    <div class="metric"><div class="label">In-scope</div><div class="value">{n_in_scope}</div></div>
    <div class="metric"><div class="label">Passed</div><div class="value good">{n_pass}</div></div>
    <div class="metric"><div class="label">Failed</div><div class="value warn">{n_fail}</div></div>
    <div class="metric"><div class="label">Skipped</div><div class="value">{n_skip}</div></div>
    <div class="metric"><div class="label">Agents</div><div class="value">{agents}</div></div>
    <div class="metric"><div class="label">Droplets</div><div class="value">{droplets}</div></div>
    <div class="metric"><div class="label">Audit chain</div><div class="value">{audit_chain}</div></div>
  </div>
</section>
<section><div class="container">
  <span class="eyebrow">Per-scenario evidence</span>
  <h2 style="font-family:var(--font-mono);font-size:1.4rem;margin-bottom:.75rem">All scenarios in {run_id}</h2>
  <div class="cards">{cards}</div>
</div></section>
<footer>Generated by <code>scripts/render_pages.py</code>. Source: <a href="https://github.com/alphaonedev/ai-memory-a2a-v0.7.0">alphaonedev/ai-memory-a2a-v0.7.0</a>.</footer>
</body></html>
"""

PLACEHOLDER = r"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><title>ai-memory · v0.7.0 A2A · awaiting first run</title>
<style>
:root{--bg:#000;--text:#fff;--text-muted:#999;--p1:#6ee7ff;--p2:#ffb86b;--good:#6ee7ff;--font-mono:'SF Mono','Cascadia Code','Fira Code',Consolas,monospace;--font-sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}
body{font-family:var(--font-sans);background:var(--bg);color:var(--text);text-align:center;padding:8rem 2rem;line-height:1.6}
h1{font-family:var(--font-mono);font-size:1.8rem;margin-bottom:1rem;color:var(--p1)}
p{color:var(--text-muted);max-width:640px;margin:0.6rem auto}
.verdict{display:inline-block;font-family:var(--font-mono);font-size:.95rem;letter-spacing:.08em;text-transform:uppercase;padding:.5em 1.1em;border-radius:6px;font-weight:700;margin:0 .3rem;color:var(--p2);background:rgba(255,184,107,0.15);border:1px solid rgba(255,184,107,0.5)}
.verdict .label{font-size:.65rem;display:block;letter-spacing:.12em;color:#666;margin-bottom:.15rem;font-weight:500}
</style></head><body>
<h1>v0.7.0 A2A · scaffold ready · awaiting Round 1</h1>
<p>The campaign harness is in place. Once <code>two-rounds.yml</code> dispatches Round 1 and writes the first <code>runs/&lt;campaign-id&gt;/a2a-summary.json</code>, this page regenerates with verdicts + per-scenario cards.</p>
<div><div class="verdict"><span class="label">Round 1</span>pending</div><div class="verdict"><span class="label">Round 2</span>pending</div></div>
<p style="margin-top:2rem">Source: <a href="https://github.com/alphaonedev/ai-memory-a2a-v0.7.0" style="color:var(--p1)">alphaonedev/ai-memory-a2a-v0.7.0</a></p>
</body></html>
"""


if __name__ == "__main__":
    main()
