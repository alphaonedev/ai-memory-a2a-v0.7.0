#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Aggregate per-phase JSON reports into a single campaign summary.
# Invoked at the end of the GitHub Actions workflow with the
# phase JSONs already fetched to ./phase-reports/.

set -euo pipefail

OUT="${1:-campaign-summary.json}"
DIR="${2:-./phase-reports}"
CAMPAIGN_ID="${CAMPAIGN_ID:-unknown}"
REF="${AI_MEMORY_GIT_REF:-unknown}"

# Collect every JSON in DIR.
jq -s '.' "$DIR"/*.json 2>/dev/null > /tmp/all.json || echo '[]' > /tmp/all.json

# Overall pass: every phase must have pass=true.
OVERALL_PASS=$(jq '[.[].pass] | all' /tmp/all.json)

jq -n \
  --arg campaign "$CAMPAIGN_ID" \
  --arg ref "$REF" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson overall "$OVERALL_PASS" \
  --slurpfile phases /tmp/all.json \
  '{
    campaign_id: $campaign,
    ai_memory_git_ref: $ref,
    completed_at: $ts,
    overall_pass: $overall,
    phases: $phases[0]
  }' > "$OUT"

echo "Wrote $OUT"
cat "$OUT"
