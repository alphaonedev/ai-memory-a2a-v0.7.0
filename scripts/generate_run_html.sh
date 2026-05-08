#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Generate a self-contained, offline-browsable index.html inside a
# runs/<campaign-id>/ directory.
#
# The page layers three kinds of content:
#   1. AI NHI narrative (pulled from analysis/run-insights.json) —
#      verdict, what-was-tested, three-audience commentary, bugs
#      with links to the issues/PRs that fixed them.
#   2. Per-phase JSON dossiers — prettified and collapsed.
#   3. Raw artifact list — every JSON in the directory.
#
# Usage:
#   ./scripts/generate_run_html.sh runs/v0.6.0.0-final-r19
#
# Or backfill everything:
#   for d in runs/*/; do ./scripts/generate_run_html.sh "$d"; done

set -euo pipefail

DIR="${1:?usage: $0 <run-directory>}"
[ -d "$DIR" ] || { echo "not a directory: $DIR" >&2; exit 1; }
SUMMARY="$DIR/campaign-summary.json"
[ -f "$SUMMARY" ] || { echo "no campaign-summary.json in $DIR — skipping" >&2; exit 0; }

OUT="$DIR/index.html"
NAME=$(basename "$DIR")

# --- repo-root insights file ----------------------------------------
# The generator is usually invoked from the repo root; fall back to
# walking up from the run directory if someone runs it from elsewhere.
INSIGHTS=""
if [ -f analysis/run-insights.json ]; then
  INSIGHTS="analysis/run-insights.json"
elif [ -f "$(dirname "$DIR")/../analysis/run-insights.json" ]; then
  INSIGHTS="$(dirname "$DIR")/../analysis/run-insights.json"
fi

# --- pull top-level fields from campaign-summary.json ---------------
REF=$(jq -r '.ai_memory_git_ref // "?"' "$SUMMARY")
COMPLETED=$(jq -r '.completed_at // "?"' "$SUMMARY")

# Historically collect_reports.sh wrote `overall_pass: true` whenever
# it found phase-1 and phase-2 JSONs, even if phase 4 failed or was
# missing. In addition, several campaigns wrote their phase-4 stdout
# log to phase4.json instead of the JSON summary (the jq summary
# line inside run-chaos.sh is mixed with log output). Derive outcome
# strictly: a phase file counts as PASS ONLY if it parses as JSON
# AND contains `.pass == true`. Anything else — parse error, missing
# file, pass:false, or missing pass field — flips OUTCOME to FAIL.
OUTCOME="PASS"
pass_of() {
  local file="$1"
  [ -f "$file" ] || { echo "FAIL"; return; }
  local v
  v=$(jq -r 'if type == "object" and (.pass == true) then "PASS" else "FAIL" end' "$file" 2>/dev/null)
  echo "${v:-FAIL}"
}
summary_pass=$(jq -r '.overall_pass // false' "$SUMMARY" 2>/dev/null || echo false)
[ "$summary_pass" = "true" ] || OUTCOME="FAIL"
for p in phase2.json phase3.json phase4.json; do
  [ "$(pass_of "$DIR/$p")" = "PASS" ] || { OUTCOME="FAIL"; break; }
done
p1_ok=true
has_p1=false
for n in "$DIR"/phase1-node-*.json; do
  [ -e "$n" ] || continue
  has_p1=true
  [ "$(pass_of "$n")" = "PASS" ] || { p1_ok=false; break; }
done
[ "$has_p1" = "true" ] && [ "$p1_ok" = "true" ] || OUTCOME="FAIL"
if [ "$OUTCOME" = "PASS" ]; then
  PASS_CLASS="pass"; PASS_LABEL="PASS"
else
  PASS_CLASS="fail"; PASS_LABEL="FAIL"
fi
PASS="$OUTCOME"

# --- HTML-escape helper ---------------------------------------------
html_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

# --- jq one-liner for a field inside the NAME's insight block, or "" -
insight_field() {
  local key="$1"
  [ -n "$INSIGHTS" ] || { printf ''; return; }
  jq -r --arg n "$NAME" --arg k "$key" \
    '(.[$n][$k] // "") | tostring' "$INSIGHTS" 2>/dev/null || printf ''
}

# --- render an AI NHI analysis section (tri-audience + bugs) --------
render_insights() {
  [ -n "$INSIGHTS" ] || return 0
  local has
  has=$(jq -r --arg n "$NAME" '.[$n] != null' "$INSIGHTS" 2>/dev/null || echo false)
  if [ "$has" != "true" ]; then
    cat <<HERE
      <section class="ai-insight">
        <h2>AI NHI analysis</h2>
        <p class="muted">No per-campaign narrative recorded for this run. The workflow appends entries to <code>analysis/run-insights.json</code> as each campaign completes; older runs that predate the automation show this placeholder. Raw evidence below is unaffected.</p>
      </section>
HERE
    return 0
  fi

  local headline verdict tested proved audience_nt audience_cl audience_sme next
  headline=$(insight_field headline | html_escape)
  verdict=$(insight_field verdict | html_escape)
  tested=$(insight_field what_it_tested | html_escape)
  proved=$(insight_field what_it_proved | html_escape)
  audience_nt=$(insight_field for_non_technical | html_escape)
  audience_cl=$(insight_field for_c_level | html_escape)
  audience_sme=$(insight_field for_sme | html_escape)
  next=$(insight_field next_run_change | html_escape)

  cat <<HERE
      <section class="ai-insight">
        <p class="tag">AI NHI analysis &middot; Claude Opus 4.7</p>
        <h2>${headline}</h2>
        <p class="verdict">${verdict}</p>
        <h3>What this campaign tested</h3>
        <p>${tested}</p>
        <h3>What it proved (or disproved)</h3>
        <p>${proved}</p>
        <h3>For three audiences</h3>
        <div class="audiences">
          <article>
            <h4>Non-technical end users</h4>
            <p>${audience_nt}</p>
          </article>
          <article>
            <h4>C-level decision makers</h4>
            <p>${audience_cl}</p>
          </article>
          <article>
            <h4>Engineers &amp; architects</h4>
            <p>${audience_sme}</p>
          </article>
        </div>
HERE

  # Bugs section with fix links.
  local bug_count
  bug_count=$(jq -r --arg n "$NAME" '[.[$n].bugs // []] | .[0] | length' "$INSIGHTS" 2>/dev/null || echo 0)
  if [ "$bug_count" -gt 0 ]; then
    cat <<HERE
        <h3>Bugs surfaced and where they were fixed</h3>
        <ol class="bugs">
HERE
    local i=0
    while [ "$i" -lt "$bug_count" ]; do
      local title impact cause
      title=$(jq -r --arg n "$NAME" --argjson i "$i" '(.[$n].bugs[$i].title // "") | tostring' "$INSIGHTS" | html_escape)
      impact=$(jq -r --arg n "$NAME" --argjson i "$i" '(.[$n].bugs[$i].impact // "") | tostring' "$INSIGHTS" | html_escape)
      cause=$(jq -r --arg n "$NAME" --argjson i "$i" '(.[$n].bugs[$i].root_cause // "") | tostring' "$INSIGHTS" | html_escape)
      cat <<HERE
          <li>
            <p class="bug-title">${title}</p>
            <p class="bug-field"><strong>Impact:</strong> ${impact}</p>
            <p class="bug-field"><strong>Root cause:</strong> ${cause}</p>
            <p class="bug-field"><strong>Fixed in:</strong></p>
            <ul class="bug-fixes">
HERE
      # Render every fixed_in link.
      local fix_count j=0
      fix_count=$(jq -r --arg n "$NAME" --argjson i "$i" '(.[$n].bugs[$i].fixed_in // []) | length' "$INSIGHTS" 2>/dev/null || echo 0)
      while [ "$j" -lt "$fix_count" ]; do
        local label url
        label=$(jq -r --arg n "$NAME" --argjson i "$i" --argjson j "$j" '(.[$n].bugs[$i].fixed_in[$j].label // "") | tostring' "$INSIGHTS" | html_escape)
        url=$(jq -r --arg n "$NAME" --argjson i "$i" --argjson j "$j" '(.[$n].bugs[$i].fixed_in[$j].url // "") | tostring' "$INSIGHTS" | html_escape)
        printf '              <li><a href="%s" rel="noopener">%s</a></li>\n' "$url" "$label"
        j=$((j + 1))
      done
      cat <<HERE
            </ul>
          </li>
HERE
      i=$((i + 1))
    done
    cat <<HERE
        </ol>
HERE
  fi

  if [ -n "$next" ]; then
    cat <<HERE
        <h3>What changed going into the next campaign</h3>
        <p>${next}</p>
HERE
  fi

  cat <<HERE
      </section>
HERE
}

# --- checklist helpers ----------------------------------------------
check_item() {
  local ok="$1" text="$2" actual="${3:-}"
  local cls
  if [ "$ok" = "true" ]; then cls="pass"; else cls="fail"; fi
  if [ -n "$actual" ]; then
    printf '          <li class="%s"><span class="check">%s</span> %s <span class="muted">— %s</span></li>\n' \
      "$cls" "$([ "$ok" = "true" ] && echo '&#x2713;' || echo '&#x2717;')" "$text" "$(echo "$actual" | html_escape)"
  else
    printf '          <li class="%s"><span class="check">%s</span> %s</li>\n' \
      "$cls" "$([ "$ok" = "true" ] && echo '&#x2713;' || echo '&#x2717;')" "$text"
  fi
}

# Returns "true" or "false" for a jq boolean expression against a file.
jq_bool() {
  local file="$1" expr="$2"
  local out
  out=$(jq -r "if ${expr} then \"true\" else \"false\" end" "$file" 2>/dev/null || echo false)
  echo "${out:-false}"
}

checklist_phase1() {
  local f="$1"
  local label; label=$(basename "$f" .json | sed 's/phase1-//')
  printf '        <h4>%s</h4>\n' "$label"
  printf '        <ul class="checklist">\n'
  # Each assertion below mirrors scripts/phase1_functional.sh.
  check_item "$(jq_bool "$f" '(.stats.total // 0) >= 1')" \
    "Stats total ≥ 1 (store + list + stats round-trip)" \
    "$(jq -r '.stats.total' "$f" 2>/dev/null) memories"
  check_item "$(jq_bool "$f" '(.recall_count // 0) >= 1')" \
    "Recall returned ≥ 1 hit" \
    "$(jq -r '.recall_count' "$f" 2>/dev/null) hits"
  check_item "$(jq_bool "$f" '(.snapshot_count // 0) >= 1')" \
    "Backup snapshot file emitted" \
    "$(jq -r '.snapshot_count' "$f" 2>/dev/null) snapshot(s)"
  check_item "$(jq_bool "$f" '(.manifest_count // 0) >= 1')" \
    "Backup manifest file emitted" \
    "$(jq -r '.manifest_count' "$f" 2>/dev/null) manifest(s)"
  check_item "$(jq_bool "$f" '(.mcp_tool_count // 0) >= 30')" \
    "MCP handshake advertises ≥ 30 tools" \
    "$(jq -r '.mcp_tool_count' "$f" 2>/dev/null) tools"
  check_item "$(jq_bool "$f" '((.curator.errors // []) | map(select(. != "no LLM client configured")) | length == 0)')" \
    "Curator dry-run clean (Ollama-not-configured is accepted)" \
    "$(jq -r '.curator.errors | length' "$f" 2>/dev/null) errors"
  check_item "$(jq_bool "$f" '.pass == true')" \
    "Overall phase-1 pass flag" ""
  printf '        </ul>\n'
}

checklist_phase2() {
  local f="$1"
  # 95% threshold against the OK count, same as phase2_multiagent.sh.
  local ok; ok=$(jq -r '.ok // 0' "$f" 2>/dev/null)
  local threshold=$(( ok * 95 / 100 ))
  printf '        <ul class="checklist">\n'
  check_item "$(jq_bool "$f" '(.ok // 0) == (.total_writes // 0) and (.total_writes // 0) > 0')" \
    "Burst writes returned 201" \
    "ok=$(jq -r '.ok' "$f")/$(jq -r '.total_writes' "$f") (qnm=$(jq -r '.quorum_not_met' "$f"), fail=$(jq -r '.fail' "$f"))"
  check_item "$(jq_bool "$f" "(.counts.a // 0) >= $threshold")" \
    "node-A convergence ≥ 95% of ok" \
    "a=$(jq -r '.counts.a' "$f") / threshold $threshold"
  check_item "$(jq_bool "$f" "(.counts.b // 0) >= $threshold")" \
    "node-B convergence ≥ 95% of ok" \
    "b=$(jq -r '.counts.b' "$f") / threshold $threshold"
  check_item "$(jq_bool "$f" "(.counts.c // 0) >= $threshold")" \
    "node-C convergence ≥ 95% of ok" \
    "c=$(jq -r '.counts.c' "$f") / threshold $threshold"
  check_item "$(jq_bool "$f" '(.probe1_single_peer_down // "") == "201"')" \
    "Probe 1: one peer down → 201 (quorum met via remaining peer)" \
    "got $(jq -r '.probe1_single_peer_down' "$f")"
  check_item "$(jq_bool "$f" '(.probe2_both_peers_down // "") == "503"')" \
    "Probe 2: both peers down → 503 (quorum_not_met)" \
    "got $(jq -r '.probe2_both_peers_down' "$f")"
  check_item "$(jq_bool "$f" '.pass == true')" \
    "Overall phase-2 pass flag" ""
  printf '        </ul>\n'
}

checklist_phase3() {
  local f="$1"
  printf '        <ul class="checklist">\n'
  check_item "$(jq_bool "$f" '(.src_count // 0) == 1000')" \
    "Source SQLite has 1000 seed memories" \
    "src_count=$(jq -r '.src_count' "$f")"
  check_item "$(jq_bool "$f" '(.dst_count // 0) == 1000')" \
    "Destination after reverse roundtrip has 1000 memories" \
    "dst_count=$(jq -r '.dst_count' "$f")"
  check_item "$(jq_bool "$f" '((.report_forward.errors // []) | length) == 0')" \
    "Forward migration SQLite → Postgres: errors=0" \
    "errors=$(jq -r '.report_forward.errors | length' "$f")"
  check_item "$(jq_bool "$f" '((.report_idempotent.errors // []) | length) == 0 and (.report_idempotent.memories_written // 0) == 1000')" \
    "Idempotent re-run is a no-op" \
    "writes=$(jq -r '.report_idempotent.memories_written' "$f")"
  check_item "$(jq_bool "$f" '((.report_reverse.errors // []) | length) == 0')" \
    "Reverse migration Postgres → SQLite: errors=0" \
    "errors=$(jq -r '.report_reverse.errors | length' "$f")"
  check_item "$(jq_bool "$f" '.pass == true')" \
    "Overall phase-3 pass flag" ""
  printf '        </ul>\n'
}

checklist_phase4() {
  local f="$1"
  # Phase 4 summary is JSON only when phase4_chaos.sh completed every
  # fault class. Earlier campaigns wrote the chaos STDOUT log to
  # phase4.json and left no structured summary — detect that and
  # render a degraded checklist.
  local is_json
  is_json=$(jq -e 'type == "object"' "$f" >/dev/null 2>&1 && echo true || echo false)
  printf '        <ul class="checklist">\n'
  if [ "$is_json" != "true" ]; then
    check_item "false" \
      "phase4.json did not parse as JSON — the chaos-harness summary never wrote cleanly" \
      "see raw JSON below"
    check_item "false" "Per-fault convergence_bound ≥ 0.995" "metric unavailable"
    printf '        </ul>\n'
    return 0
  fi
  local faults
  faults=$(jq -r '(.convergence_by_fault // {}) | keys[]?' "$f" 2>/dev/null)
  if [ -n "$faults" ]; then
    while IFS= read -r fault; do
      local bound
      bound=$(jq -r --arg k "$fault" '.convergence_by_fault[$k]' "$f" 2>/dev/null)
      local ok
      ok=$(awk -v b="$bound" 'BEGIN{print (b+0 >= 0.995)?"true":"false"}')
      check_item "$ok" \
        "Chaos fault class: $fault convergence_bound ≥ 0.995" \
        "got $bound"
    done <<< "$faults"
  else
    check_item "false" "Per-fault convergence_bound ≥ 0.995" \
      "no convergence_by_fault field in phase4.json"
  fi
  check_item "$(jq_bool "$f" '.pass == true')" \
    "Overall phase-4 pass flag" ""
  printf '        </ul>\n'

  # Per-cycle drill-down table — only present when phase4_chaos.sh
  # captured the per-cycle JSONL into cycles_by_fault (new in r21).
  # For each fault class, render a collapsible table showing cycle-#,
  # ok/qnm/fail, and the per-peer count. That's the data needed to
  # distinguish harness-timing from product-lag when a bound misses.
  local fault_list
  fault_list=$(jq -r '(.cycles_by_fault // {}) | keys[]?' "$f" 2>/dev/null)
  [ -z "$fault_list" ] && return 0
  printf '        <h4>Per-cycle convergence detail</h4>\n'
  while IFS= read -r fault; do
    local cycle_count
    cycle_count=$(jq --arg k "$fault" '.cycles_by_fault[$k] | length' "$f" 2>/dev/null)
    [ "${cycle_count:-0}" -gt 0 ] || continue
    printf '        <details>\n'
    printf '          <summary>%s — %s cycles</summary>\n' "$(echo "$fault" | html_escape)" "$cycle_count"
    printf '          <table class="cycle-table">\n'
    printf '            <thead><tr><th>cycle</th><th>ok</th><th>qnm</th><th>fail</th><th>node-0</th><th>node-1</th><th>node-2</th></tr></thead>\n'
    printf '            <tbody>\n'
    jq -r --arg k "$fault" '
      .cycles_by_fault[$k][] |
      "<tr><td>\(.cycle)</td><td>\(.ok)</td><td>\(.quorum_not_met)</td><td>\(.fail)</td><td>\(.count_node0)</td><td>\(.count_node1)</td><td>\(.count_node2)</td></tr>"' "$f" 2>/dev/null
    printf '            </tbody>\n'
    printf '          </table>\n'
    printf '        </details>\n'
  done <<< "$fault_list"
}

# Human-readable name for each phase number.
phase_name() {
  case "$1" in
    1) echo "functional (per-node)" ;;
    2) echo "multi-agent federation" ;;
    3) echo "cross-backend migration" ;;
    4) echo "chaos campaign" ;;
  esac
}

# What a given phase is there to prove. Shown on every run page so
# readers can see what the phase measures even if the phase never ran.
phase_purpose() {
  case "$1" in
    1) echo "Single-node CRUD, backup, curator dry-run, and MCP handshake on each of the three peer droplets. Establishes that ai-memory starts and is functional at the one-node level before federation is exercised." ;;
    2) echo "4 agents × 50 writes against the 3-node federation with W=2 quorum, then 90s settle and convergence count on every peer. Plus two quorum probes (one-peer-down must 201, both-peers-down must 503). Catches silent-data-loss and quorum-misclassification regressions." ;;
    3) echo "1000-memory round-trip: SQLite → Postgres, re-run for idempotency, Postgres → SQLite. Asserts zero errors and counts match. Catches migration-correctness regressions in either direction of a production upgrade path." ;;
    4) echo "packaging/chaos/run-chaos.sh on the chaos-client droplet with 50 cycles × 100 writes per fault class. Measures convergence_bound = min(count_node1, count_node2) / total_ok. Catches fault-tolerance regressions under SIGKILL of the primary, brief network partition, and related fault models." ;;
  esac
}

# --- phase dossier ---------------------------------------------------
phase_block() {
  local phase="$1"
  local files=()
  if [ "$phase" = "1" ]; then
    for f in "$DIR"/phase1-node-*.json; do [ -e "$f" ] && files+=("$f"); done
  else
    for f in "$DIR"/phase"$phase".json; do [ -e "$f" ] && files+=("$f"); done
  fi

  # Determine the phase's status. Four possible states:
  #   PASS         — all artifact files present and .pass == true
  #   FAIL         — at least one artifact present with .pass != true
  #   NOT REACHED  — zero artifacts because an earlier phase's failure
  #                  aborted the campaign before this phase could run
  #   NOT RUN      — zero artifacts because the phase was intentionally
  #                  skipped (reserved for future campaigns that can
  #                  opt out of a phase); today's campaigns don't.
  #
  # Every phase renders a section regardless of state — a red "Phase 3
  # NOT REACHED" is more informative than a silent omission, because
  # readers can see the protocol shape on every run.
  local status explanation
  if [ "${#files[@]}" -eq 0 ]; then
    # Decide NOT REACHED vs NOT RUN. Today's heuristic: if any
    # earlier phase's files exist and any of them failed, this phase
    # was aborted ("NOT REACHED"). Otherwise label NOT RUN.
    local earlier_fail=false
    for earlier in $(seq 1 $((phase - 1))); do
      if [ "$earlier" = "1" ]; then
        for f in "$DIR"/phase1-node-*.json; do
          [ -e "$f" ] || continue
          [ "$(pass_of "$f")" = "FAIL" ] && earlier_fail=true
        done
      else
        if [ -f "$DIR/phase${earlier}.json" ]; then
          [ "$(pass_of "$DIR/phase${earlier}.json")" = "FAIL" ] && earlier_fail=true
        else
          # Missing earlier phase also counts as aborted upstream.
          earlier_fail=true
        fi
      fi
    done
    if [ "$earlier_fail" = "true" ]; then
      status='NOT REACHED'
      explanation="This phase did not run because an earlier phase failed and the campaign aborted. Evidence from the phases that did run is above; the protocol would have exercised this phase next if the prior step had passed."
    else
      status='NOT RUN'
      explanation="This phase produced no artifact in this campaign. Either the campaign was an older shape that predates this phase's scripting, or the artifact commit step never landed this file on main. Raw evidence, if any, is linked below."
    fi
  else
    local phase_ok=true
    for f in "${files[@]}"; do
      [ "$(pass_of "$f")" = "PASS" ] || { phase_ok=false; break; }
    done
    status=$([ "$phase_ok" = "true" ] && echo "PASS" || echo "FAIL")
  fi

  local badge_class
  case "$status" in
    PASS) badge_class="pass" ;;
    *)    badge_class="fail" ;;
  esac

  printf '      <section class="phase">\n'
  printf '        <h2>Phase %s — %s <span class="badge %s">%s</span></h2>\n' \
    "$phase" "$(phase_name "$phase")" "$badge_class" "$status"
  printf '        <p class="phase-purpose"><strong>What this phase proves:</strong> %s</p>\n' "$(phase_purpose "$phase")"

  if [ "${#files[@]}" -eq 0 ]; then
    printf '        <p class="muted">%s</p>\n' "$explanation"
    printf '      </section>\n'
    return 0
  fi

  printf '        <h3>Test results</h3>\n'
  case "$phase" in
    1) for f in "${files[@]}"; do checklist_phase1 "$f"; done ;;
    2) checklist_phase2 "${files[0]}" ;;
    3) checklist_phase3 "${files[0]}" ;;
    4) checklist_phase4 "${files[0]}" ;;
  esac
  printf '        <h3>Raw evidence</h3>\n'
  for f in "${files[@]}"; do
    local label
    label=$(basename "$f" .json)
    printf '        <details>\n'
    printf '          <summary>%s</summary>\n' "$label"
    printf '          <pre>'
    jq --tab . "$f" 2>/dev/null | html_escape || cat "$f" | html_escape
    printf '</pre>\n'
    printf '          <p><a href="./%s">raw JSON</a></p>\n' "$(basename "$f")"
    printf '        </details>\n'
  done
  printf '      </section>\n'
}

# --- render ----------------------------------------------------------
{
cat <<EOF
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Campaign ${NAME} — ai-memory ship-gate</title>
  <meta name="description" content="AI NHI analysis + per-phase evidence for ai-memory-mcp ship-gate campaign ${NAME} (ref ${REF}).">
  <style>
    :root {
      --fg: #1a1a1a; --bg: #fff; --muted: #666;
      --pass: #1a8b3c; --fail: #b32222; --pass-bg: #e6f6ea; --fail-bg: #fceaea;
      --border: #e0e0e0; --code-bg: #f7f7f9; --accent: #3952c8; --accent-bg: #eef1fd;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --fg: #eaeaea; --bg: #181818; --muted: #a0a0a0;
        --pass: #4adc76; --fail: #ff6a6a; --pass-bg: #153d23; --fail-bg: #401818;
        --border: #2a2a2a; --code-bg: #202028; --accent: #8da3ff; --accent-bg: #1c2140;
      }
    }
    * { box-sizing: border-box; }
    body { font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: var(--fg); background: var(--bg); margin: 0; padding: 0; }
    main { max-width: 960px; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
    header { border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 1.5rem; }
    header p.crumb { color: var(--muted); font-size: 13px; margin: 0 0 .25rem; }
    h1 { margin: .25rem 0 .75rem; font-size: 1.85rem; }
    h2 { margin: 1.75rem 0 .5rem; font-size: 1.2rem; border-bottom: 1px solid var(--border); padding-bottom: .25rem; }
    h3 { margin: 1.25rem 0 .4rem; font-size: 1rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; font-weight: 600; }
    h4 { margin: .5rem 0 .25rem; font-size: 1rem; }
    dl.meta { display: grid; grid-template-columns: max-content 1fr; gap: .35rem 1rem; margin: 0; }
    dl.meta dt { color: var(--muted); }
    dl.meta dd { margin: 0; }
    .badge { display: inline-block; padding: .15rem .55rem; border-radius: 999px; font-size: 12px; font-weight: 600; letter-spacing: .03em; text-transform: uppercase; margin-left: .4rem; vertical-align: middle; }
    .badge.pass { color: var(--pass); background: var(--pass-bg); }
    .badge.fail { color: var(--fail); background: var(--fail-bg); }
    .phase-purpose { font-size: 14px; color: var(--muted); margin: .25rem 0 .75rem; padding: .35rem .75rem; border-left: 3px solid var(--border); background: var(--code-bg); border-radius: 0 4px 4px 0; }
    .phase-purpose strong { color: var(--fg); }
    .run-focus { background: var(--code-bg); border-left: 4px solid var(--accent); padding: 1rem 1.25rem; margin: 1.5rem 0; border-radius: 0 6px 6px 0; }
    .run-focus h2 { border: none; margin: 0 0 .5rem; padding: 0; font-size: 1.1rem; }
    .run-focus p { margin: .3rem 0; }
    .run-focus p.label { color: var(--accent); font-size: 12px; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; margin: 0 0 .25rem; }
    .ai-insight { background: var(--accent-bg); border-radius: 8px; padding: 1.25rem 1.5rem; margin: 1.5rem 0 2rem; border-left: 4px solid var(--accent); }
    .ai-insight h2 { border: none; padding: 0; margin-top: .25rem; }
    .ai-insight p.tag { color: var(--accent); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; font-weight: 600; margin: 0 0 .25rem; }
    .ai-insight p.verdict { font-size: 1.05rem; margin: .5rem 0 1rem; }
    .audiences { display: grid; grid-template-columns: 1fr; gap: .75rem; margin-top: .5rem; }
    @media (min-width: 720px) { .audiences { grid-template-columns: repeat(3, 1fr); } }
    .audiences article { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: .85rem 1rem; }
    .audiences article h4 { margin-top: 0; color: var(--accent); font-size: 14px; letter-spacing: .02em; }
    .audiences article p { margin: .25rem 0 0; font-size: 14px; }
    ol.bugs { margin: .5rem 0; padding-left: 1.25rem; }
    ol.bugs > li { margin-bottom: .75rem; }
    ol.bugs p.bug-title { font-weight: 600; margin: .25rem 0; }
    ol.bugs p.bug-field { margin: .25rem 0; font-size: 14px; }
    ol.bugs ul.bug-fixes { margin: .25rem 0 .5rem 1rem; padding: 0; list-style: disc; font-size: 14px; }
    ul.checklist { list-style: none; padding: 0; margin: .25rem 0 .75rem; }
    ul.checklist li { padding: .2rem 0 .2rem 0; font-size: 14px; display: flex; gap: .5rem; align-items: baseline; flex-wrap: wrap; }
    ul.checklist li .check { display: inline-block; width: 1.25rem; text-align: center; font-weight: 700; font-size: 15px; }
    ul.checklist li.pass .check { color: var(--pass); }
    ul.checklist li.fail .check { color: var(--fail); }
    ul.checklist li.fail { background: var(--fail-bg); border-radius: 4px; padding-left: .35rem; padding-right: .35rem; }
    table.cycle-table { width: 100%; border-collapse: collapse; margin: .5rem 0; font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    table.cycle-table th, table.cycle-table td { padding: .2rem .6rem; text-align: right; border-bottom: 1px solid var(--border); }
    table.cycle-table th:first-child, table.cycle-table td:first-child { text-align: left; }
    table.cycle-table th { background: var(--code-bg); font-weight: 600; }
    details { border: 1px solid var(--border); border-radius: 6px; padding: .25rem .75rem; margin: .5rem 0; }
    details[open] { padding-bottom: .5rem; }
    summary { cursor: pointer; padding: .45rem 0; font-weight: 500; }
    pre { background: var(--code-bg); padding: .75rem; border-radius: 6px; overflow-x: auto; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; margin: .5rem 0; }
    a { color: var(--accent); }
    a:hover { text-decoration: underline; }
    .muted { color: var(--muted); font-size: 14px; }
    footer { color: var(--muted); font-size: 12px; border-top: 1px solid var(--border); padding-top: 1rem; margin-top: 3rem; }
  </style>
</head>
<body>
  <main>
    <header>
      <p class="crumb"><a href="../">../ runs index</a> &middot; <a href="https://alphaonedev.github.io/ai-memory-ship-gate/runs/${NAME}/">rendered on Pages</a></p>
      <h1>Campaign ${NAME} <span class="badge ${PASS_CLASS}">${PASS_LABEL}</span></h1>
      <dl class="meta">
        <dt>ai-memory ref</dt><dd><code>${REF}</code></dd>
        <dt>Completed at</dt><dd>${COMPLETED}</dd>
        <dt>Overall pass</dt><dd>${PASS}</dd>
      </dl>
    </header>
EOF

# "Run focus" block — pulled from the insight headline + verdict.
# Answers "what was this run trying to achieve, and what did it
# demonstrate?" up front, before the reader scrolls into the phase
# test results. Hidden for runs with no insight entry.
if [ -n "$INSIGHTS" ]; then
  has_insight=$(jq -r --arg n "$NAME" '.[$n] != null' "$INSIGHTS" 2>/dev/null || echo false)
  if [ "$has_insight" = "true" ]; then
    headline_top=$(insight_field headline | html_escape)
    tested_top=$(insight_field what_it_tested | html_escape)
    proved_top=$(insight_field what_it_proved | html_escape)
    cat <<EOF_FOCUS
      <section class="run-focus">
        <p class="label">Run focus</p>
        <h2>${headline_top}</h2>
        <p><strong>What this campaign set out to test:</strong> ${tested_top}</p>
        <p><strong>What it demonstrated:</strong> ${proved_top}</p>
        <p class="muted">Detailed tri-audience analysis is below, followed by per-phase test results for all four phases of the protocol — including any phase that did not run in this campaign.</p>
      </section>
EOF_FOCUS
  fi
fi

render_insights
for p in 1 2 3 4; do phase_block "$p"; done

cat <<EOF
    <section>
      <h2>All artifacts</h2>
      <p class="muted">Every JSON committed to this campaign directory. Raw, machine-readable, and stable.</p>
      <ul>
EOF
for f in "$DIR"/*.json; do
  [ -e "$f" ] || continue
  fn=$(basename "$f")
  printf '        <li><a href="./%s">%s</a></li>\n' "$fn" "$fn"
done
cat <<EOF
      </ul>
    </section>
    <footer>
      Generated by <code>scripts/generate_run_html.sh</code>.
      Campaign directory:
      <a href="https://github.com/alphaonedev/ai-memory-ship-gate/tree/main/runs/${NAME}">
        alphaonedev/ai-memory-ship-gate/runs/${NAME}
      </a>.
      Methodology:
      <a href="https://alphaonedev.github.io/ai-memory-ship-gate/methodology/">
        alphaonedev.github.io/ai-memory-ship-gate/methodology
      </a>.
      Analysis data source: <code>analysis/run-insights.json</code>.
    </footer>
  </main>
</body>
</html>
EOF
} > "$OUT"

echo "wrote $OUT"
