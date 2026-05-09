#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Wave 4 deployment helper.
#
# Replaces the ai-memory binary on the openclaw + hermes droplets with
# the post-Continuation-3 build (which supports `ai-memory serve
# --store-url postgres://...`), restarts each daemon against its
# disposable per-scenario postgres database, and smoke-tests
# reachability + capabilities.storage_backend == "postgres".
#
# Pre-conditions (validated below; the script bails on any miss):
#   - Phase 22 droplet remediation has been run on the postgres-node
#     (fresh schema, per-scenario disposable databases exist).
#   - Continuation 3 has merged: the binary at $AI_MEMORY_BINARY_PATH
#     supports `--store-url postgres://...`.
#   - /tmp/v07-bootstrap-summary.json describes droplet IPs (openclaw,
#     hermes, postgres-node).
#   - /tmp/v07-a2a-pg-password.txt holds the postgres password.
#
# Usage:
#   scripts/deploy_wave4.sh                       # both droplets, full restart
#   scripts/deploy_wave4.sh --node openclaw       # just openclaw
#   scripts/deploy_wave4.sh --dry-run             # validate prereqs only
#
# Exits non-zero on any failure. STDOUT is operator-facing; emits a
# JSON summary at /tmp/v07-wave4-deploy-summary.json on success.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SUMMARY_PATH="/tmp/v07-bootstrap-summary.json"
PG_PASSWORD_PATH="${POSTGRES_PASSWORD_PATH:-/tmp/v07-a2a-pg-password.txt}"
AI_MEMORY_BINARY_PATH="${AI_MEMORY_BINARY_PATH:-/Users/fate/v07/v07-fixes/target/release/ai-memory}"
A2A_PORT="${A2A_PORT:-19077}"
DEPLOY_SUMMARY="/tmp/v07-wave4-deploy-summary.json"

DRY_RUN=0
ONLY_NODE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --node) ONLY_NODE="$2"; shift 2 ;;
        -h|--help)
            cat <<EOF
Usage: $0 [--dry-run] [--node openclaw|hermes]
EOF
            exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=5)

# ------------------------------------------------------------------
# 1. validate prereqs
# ------------------------------------------------------------------

err() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "===> $*"; }

[[ -r "$SUMMARY_PATH" ]] || err "missing $SUMMARY_PATH (Phase 22 prereq)"
[[ -r "$PG_PASSWORD_PATH" ]] || err "missing $PG_PASSWORD_PATH (postgres password)"
[[ -x "$AI_MEMORY_BINARY_PATH" ]] || err "AI_MEMORY_BINARY_PATH=$AI_MEMORY_BINARY_PATH not executable"

# Confirm the binary supports --store-url (Continuation 3 prereq).
note "validate ai-memory binary supports --store-url"
if ! "$AI_MEMORY_BINARY_PATH" serve --help 2>&1 | grep -q -- "--store-url"; then
    err "ai-memory binary at $AI_MEMORY_BINARY_PATH does not advertise --store-url; Continuation 3 not merged?"
fi

OPENCLAW_IP="$(jq -r '.openclaw.public_ip // .openclaw_ip // empty' "$SUMMARY_PATH")"
HERMES_IP="$(jq -r '.hermes.public_ip // .hermes_ip // empty' "$SUMMARY_PATH")"
POSTGRES_HOST="$(jq -r '.postgres.private_ip // .postgres_priv // "10.20.0.4"' "$SUMMARY_PATH")"

[[ -n "$OPENCLAW_IP" ]] || err "openclaw IP missing in $SUMMARY_PATH"
[[ -n "$HERMES_IP" ]]   || err "hermes   IP missing in $SUMMARY_PATH"

PG_PASSWORD="$(tr -d '\n\r' < "$PG_PASSWORD_PATH")"
[[ -n "$PG_PASSWORD" ]] || err "postgres password file is empty"

# URL-encode the password (single-pass via python; portable enough).
PG_PASSWORD_ENC="$(python3 -c 'import sys, urllib.parse; sys.stdout.write(urllib.parse.quote(sys.argv[1], safe=""))' "$PG_PASSWORD")"

note "prereqs ok: openclaw=$OPENCLAW_IP hermes=$HERMES_IP postgres-host=$POSTGRES_HOST"

if [[ "$DRY_RUN" == "1" ]]; then
    note "dry-run only — exiting without deploying"
    exit 0
fi

# ------------------------------------------------------------------
# 2. helper: deploy + restart a single daemon
# ------------------------------------------------------------------

deploy_one() {
    local node_label="$1" node_ip="$2" agent_id="$3" scenario_db="$4"

    note "[$node_label] stage ai-memory binary"
    scp "${SSH_OPTS[@]}" "$AI_MEMORY_BINARY_PATH" "root@${node_ip}:/usr/local/bin/ai-memory.new"
    ssh "${SSH_OPTS[@]}" "root@${node_ip}" "
        set -e
        chmod +x /usr/local/bin/ai-memory.new
        mv /usr/local/bin/ai-memory.new /usr/local/bin/ai-memory
        /usr/local/bin/ai-memory --version || true
    "

    note "[$node_label] schema-init disposable db ${scenario_db} on ${POSTGRES_HOST}"
    local admin_url="postgres://aimemory:${PG_PASSWORD_ENC}@${POSTGRES_HOST}:5432/postgres"
    local store_url="postgres://aimemory:${PG_PASSWORD_ENC}@${POSTGRES_HOST}:5432/${scenario_db}"
    ssh "${SSH_OPTS[@]}" "root@${node_ip}" "
        set -e
        # Phase 22 should have created the disposable DB; we DROP+CREATE
        # only when --reset is asked. Default: ensure it exists, idempotent.
        psql '${admin_url}' -tAc \"SELECT 1 FROM pg_database WHERE datname='${scenario_db}'\" \
            | grep -q 1 || \
            psql '${admin_url}' -c \"CREATE DATABASE ${scenario_db} OWNER aimemory\"
        # Schema init via the binary (ai-memory serve auto-migrates on first
        # boot when AI_MEMORY_AUTO_MIGRATE=1).
        psql '${store_url}' -c \"CREATE EXTENSION IF NOT EXISTS vector;\" || true
        psql '${store_url}' -c \"CREATE EXTENSION IF NOT EXISTS age;\" || true
    "

    note "[$node_label] rewrite systemd unit to use --store-url"
    # We assume /etc/systemd/system/ai-memory.service exists (boot_*.sh
    # installed it). The replacement keeps the same Environment block
    # but swaps the ExecStart `--db <path>` flag for `--store-url
    # postgres://...`.
    ssh "${SSH_OPTS[@]}" "root@${node_ip}" "
        set -e
        UNIT=/etc/systemd/system/ai-memory.service
        [ -f \"\$UNIT\" ] || { echo 'no ai-memory.service unit on $node_label'; exit 1; }
        cp \"\$UNIT\" \"\$UNIT.wave3.bak.\$(date +%s)\"
        # Swap --db <something> for --store-url <url>. If the unit already
        # carries --store-url (re-deploy), update in place.
        if grep -q -- '--store-url' \"\$UNIT\"; then
            sed -i -E 's|--store-url [^ ]+|--store-url ${store_url}|' \"\$UNIT\"
        else
            sed -i -E 's|--db [^ ]+|--store-url ${store_url}|' \"\$UNIT\"
        fi
        # Defensive: ensure auto-migrate is enabled so first boot creates schema.
        grep -q 'AI_MEMORY_AUTO_MIGRATE=1' \"\$UNIT\" || \
            sed -i -E '/^\[Service\]/a Environment=AI_MEMORY_AUTO_MIGRATE=1' \"\$UNIT\"
        systemctl daemon-reload
        systemctl restart ai-memory
    "

    note "[$node_label] settle 8s then smoke-test"
    sleep 8

    # Smoke test 1: reachability on the daemon's local listener.
    local priv_ip
    priv_ip="$(ssh "${SSH_OPTS[@]}" "root@${node_ip}" "ip -4 -o addr show | awk '{print \$4}' | cut -d/ -f1 | grep -v 127.0.0.1 | head -n1")"
    local cap_url="http://${priv_ip}:${A2A_PORT}/api/v1/capabilities"
    local cap_body
    cap_body="$(ssh "${SSH_OPTS[@]}" "root@${node_ip}" "curl -sS --max-time 10 ${cap_url} || true")"
    [[ -n "$cap_body" ]] || err "[$node_label] capabilities probe returned empty"

    # Smoke test 2: storage_backend reports postgres.
    local backend_label
    backend_label="$(printf '%s' "$cap_body" | python3 -c '
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); sys.exit(0)
v = d.get("storage_backend") or d.get("store_backend") or d.get("backend") or ""
if not v and isinstance(d.get("storage"), dict):
    v = d["storage"].get("backend") or d["storage"].get("kind") or ""
print(v)
')"
    case "$(echo "$backend_label" | tr '[:upper:]' '[:lower:]')" in
        *postgres*|*pg*) note "[$node_label] storage_backend=$backend_label OK" ;;
        "") note "[$node_label] WARNING: capabilities surface lacks storage_backend field — Continuation 3 may not have shipped that surface; cmdline check below" ;;
        *) err "[$node_label] storage_backend=$backend_label is not postgres" ;;
    esac

    # Smoke test 3: daemon command-line carries --store-url postgres.
    local cmdline
    cmdline="$(ssh "${SSH_OPTS[@]}" "root@${node_ip}" "pgrep -af 'ai-memory serve' | head -n1")"
    [[ "$cmdline" == *"--store-url"* ]] || err "[$node_label] daemon cmdline lacks --store-url: $cmdline"
    [[ "$cmdline" == *"postgres"* ]] || err "[$node_label] daemon cmdline --store-url is not postgres scheme: $cmdline"
    note "[$node_label] cmdline OK: ${cmdline:0:160}..."

    echo "{\"node\":\"$node_label\",\"ip\":\"$node_ip\",\"db\":\"$scenario_db\",\"cmdline\":\"${cmdline:0:200}\",\"backend_label\":\"$backend_label\"}"
}

# ------------------------------------------------------------------
# 3. dispatch
# ------------------------------------------------------------------

# Per-droplet disposable database names (Phase 22 should have created
# these). The campaign harness reuses `aimemory_openclaw` /
# `aimemory_hermes` as the steady-state daemon DBs; per-scenario
# disposable DBs (`aimemory_s70`, `aimemory_sal72`, etc.) are created
# on demand by the individual scenarios.
OPENCLAW_DB="${OPENCLAW_DB:-aimemory_openclaw}"
HERMES_DB="${HERMES_DB:-aimemory_hermes}"

results=()
if [[ -z "$ONLY_NODE" || "$ONLY_NODE" == "openclaw" ]]; then
    r="$(deploy_one openclaw "$OPENCLAW_IP" "ai:openclaw@nyc3:droplet-1" "$OPENCLAW_DB")"
    results+=("$r")
fi
if [[ -z "$ONLY_NODE" || "$ONLY_NODE" == "hermes" ]]; then
    r="$(deploy_one hermes   "$HERMES_IP"   "ai:hermes@nyc3:droplet-2"   "$HERMES_DB")"
    results+=("$r")
fi

note "writing summary to $DEPLOY_SUMMARY"
{
    echo "{"
    echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"backend_kind\": \"postgres\","
    echo "  \"binary_path\": \"$AI_MEMORY_BINARY_PATH\","
    echo "  \"postgres_host\": \"$POSTGRES_HOST\","
    echo "  \"deployed\": ["
    local i=0
    for r in "${results[@]}"; do
        i=$((i+1))
        if [[ $i -lt ${#results[@]} ]]; then
            echo "    $r,"
        else
            echo "    $r"
        fi
    done
    echo "  ]"
    echo "}"
} > "$DEPLOY_SUMMARY"

note "Wave 4 deploy COMPLETE — $DEPLOY_SUMMARY"
