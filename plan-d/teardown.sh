#!/usr/bin/env bash
# Plan D — clean shutdown + DB drop for a clean re-run.
# DESTRUCTIVE — drops federation_meta on f2 and wipes /Users/fate/v07/test-cell.
# Guard with PLAN_D_CONFIRM=YES.
set -euo pipefail

if [ "${PLAN_D_CONFIRM:-}" != "YES" ]; then
  echo "refusing to teardown without PLAN_D_CONFIRM=YES" >&2
  exit 2
fi

# shellcheck disable=SC1090
. ~/.env

BASE="${BASE:-/Users/fate/v07/test-cell}"

# Stop tmux sessions.
for s in am-alice am-bob am-charlie am-dave ic-alice ic-bob ic-charlie ic-dave; do
  tmux kill-session -t "$s" 2>/dev/null || true
done

# Kill any stray IronClaw / ai-memory processes parented to BASE.
pkill -f "${BASE}/" 2>/dev/null || true

# Drop f2 DB.
ssh f2 "sudo -n -u postgres psql -d postgres <<SQL
DROP DATABASE IF EXISTS ${FED_PG_DB};
DROP ROLE IF EXISTS ${FED_PG_USER};
SQL
"

# Wipe Mac Mini test-cell directory.
rm -rf "${BASE}"

echo "[plan-d] teardown complete."
