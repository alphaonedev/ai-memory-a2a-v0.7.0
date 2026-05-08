#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Reset state on both daemons (openclaw + hermes) by enumerating
# distinct namespaces and forgetting any that match scenario / a2a / r1
# / r2 / smoke prefixes. Does NOT delete the DB file (preserves audit
# chain).
#
# Usage:
#   ./scripts/forget_test_namespaces.sh
set -euo pipefail

reset_one() {
  local host="$1" db="$2"
  ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "root@${host}" "
    sqlite3 ${db} 'SELECT DISTINCT namespace FROM memories' | while read ns; do
      case \"\$ns\" in
        scenario*|a2a-r1*|a2a-r2*|a2a-smoke*|a2a-v07*|testns*|probe*|f1-fixture*|f2-fixture*|f3-fixture*|smoke-test|link-test)
          /usr/local/bin/ai-memory forget --namespace \"\$ns\" --db ${db} 2>&1 | tail -1 | sed \"s|^|[\$ns] |\"
          ;;
      esac
    done
    echo POST_COUNT_${host}: \$(sqlite3 ${db} 'SELECT COUNT(*) FROM memories')
  "
}

reset_one 104.236.52.203 /var/lib/ai-memory/openclaw.db
reset_one 142.93.72.46 /var/lib/ai-memory/hermes.db
