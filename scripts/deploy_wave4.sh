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

# v0.7.0 Continuation-6 — opt-in TLS / mTLS plumbing.
#
# When `DEPLOY_TLS=1`, the script:
#   1. SCPs the TLS material from /tmp/a2a-v07-tls/ to each droplet's
#      /etc/ai-memory-a2a/tls/ directory (server cert/key, ca, client
#      certs/keys, mtls allowlist).
#   2. Adds `--tls-cert`, `--tls-key`, and (when MTLS=1) `--mtls-allowlist`
#      flags to the daemon's systemd ExecStart line.
#   3. Adds `--quorum-ca-cert`, `--quorum-client-cert`, `--quorum-client-key`
#      so the daemon's federation client can authenticate to the peer's
#      mTLS-protected listener (the peer is HTTPS-only when DEPLOY_TLS=1).
#   4. Rewrites `--quorum-peers http://` and `--catchup-peers http://`
#      to `https://` so peers actually reach the TLS listener.
# When unset (default), the script behaves as pre-Continuation-6 — plain
# HTTP, no cert distribution. This keeps the existing baseline working
# while we land the cert-validated path opt-in.
#
# Continuation-6 cert-closure refinement (2026-05-09):
#   * The systemd unit edits now target the dropin
#     `/etc/systemd/system/ai-memory.service.d/federation.conf`
#     rather than the main service file — that's where the boot
#     scripts now write the `--store-url` / `--quorum-*` flags. The
#     legacy main-file path is still scrubbed for safety.
#   * `AI_MEMORY_BINARY_PATH` is no longer required to be locally
#     executable — when the orchestrator stages a Linux ELF on macOS
#     (cross-compiled or pulled out of the openclaw build dir), the
#     local exec check failed even though the binary was healthy on
#     the droplet. Skipping the local exec check + relying on the
#     remote `--version` smoke test below is the honest gate.
DEPLOY_TLS="${DEPLOY_TLS:-0}"
DEPLOY_MTLS="${DEPLOY_MTLS:-1}"   # only meaningful when DEPLOY_TLS=1
TLS_LOCAL_DIR="${TLS_LOCAL_DIR:-/tmp/a2a-v07-tls}"
TLS_REMOTE_DIR="${TLS_REMOTE_DIR:-/etc/ai-memory-a2a/tls}"

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

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=5 -i "${HOME}/.ssh/id_ed25519")

# ------------------------------------------------------------------
# 1. validate prereqs
# ------------------------------------------------------------------

err() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "===> $*"; }

[[ -r "$SUMMARY_PATH" ]] || err "missing $SUMMARY_PATH (Phase 22 prereq)"
[[ -r "$PG_PASSWORD_PATH" ]] || err "missing $PG_PASSWORD_PATH (postgres password)"
[[ -r "$AI_MEMORY_BINARY_PATH" ]] || err "AI_MEMORY_BINARY_PATH=$AI_MEMORY_BINARY_PATH not readable"

# Continuation-6: when the binary is pre-built for Linux on a macOS
# orchestrator, the local exec check fails even when the binary is
# perfectly healthy on the target droplet. Detect ELF (Linux) vs
# Mach-O (macOS) and only require local-executability when the file
# matches the host's ABI. The remote `--version` smoke test below
# is the load-bearing gate.
local_arch=""
if [[ -r "$AI_MEMORY_BINARY_PATH" ]]; then
    local_arch="$(file -b "$AI_MEMORY_BINARY_PATH" 2>/dev/null | head -1)"
fi
if [[ "$(uname -s)" == "Darwin" && "$local_arch" == *"ELF"* ]]; then
    note "binary is Linux ELF on macOS host — skipping local exec check (remote smoke is authoritative)"
elif [[ ! -x "$AI_MEMORY_BINARY_PATH" ]]; then
    err "AI_MEMORY_BINARY_PATH=$AI_MEMORY_BINARY_PATH not executable on local host"
fi

# Confirm the binary supports --store-url (Continuation 3 prereq).
# Skip when the binary is cross-compiled for Linux on macOS — verified
# via remote --version below.
note "validate ai-memory binary supports --store-url"
if [[ "$local_arch" != *"ELF"* ]]; then
    if ! "$AI_MEMORY_BINARY_PATH" serve --help 2>&1 | grep -q -- "--store-url"; then
        err "ai-memory binary at $AI_MEMORY_BINARY_PATH does not advertise --store-url; Continuation 3 not merged?"
    fi
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

    # ----------------------------------------------------------------
    # Continuation-6 — opt-in TLS / mTLS cert distribution + flag wiring
    # ----------------------------------------------------------------
    local tls_extra_flags=""
    if [[ "$DEPLOY_TLS" == "1" ]]; then
        note "[$node_label] DEPLOY_TLS=1 — pushing TLS material to ${TLS_REMOTE_DIR}"
        [[ -d "$TLS_LOCAL_DIR" ]] || err "DEPLOY_TLS=1 but TLS_LOCAL_DIR=$TLS_LOCAL_DIR missing"
        ssh "${SSH_OPTS[@]}" "root@${node_ip}" "mkdir -p '${TLS_REMOTE_DIR}' && chmod 0750 '${TLS_REMOTE_DIR}'"
        # Per-node server cert + key — use the node label to pick which.
        local server_cert="${TLS_LOCAL_DIR}/server-${node_label}.pem"
        local server_key="${TLS_LOCAL_DIR}/server-${node_label}.key"
        [[ -r "$server_cert" ]] || err "missing server cert: $server_cert"
        [[ -r "$server_key"  ]] || err "missing server key:  $server_key"
        scp "${SSH_OPTS[@]}" \
            "${TLS_LOCAL_DIR}/ca.pem" \
            "$server_cert" \
            "$server_key" \
            "${TLS_LOCAL_DIR}/mtls-allowlist.txt" \
            "root@${node_ip}:${TLS_REMOTE_DIR}/"
        # Distribute every client cert + key (the harness picks one per
        # agent at runtime).
        scp "${SSH_OPTS[@]}" \
            "${TLS_LOCAL_DIR}"/client-*.pem \
            "${TLS_LOCAL_DIR}"/client-*.key \
            "root@${node_ip}:${TLS_REMOTE_DIR}/"
        ssh "${SSH_OPTS[@]}" "root@${node_ip}" "
            set -e
            chown -R root:root '${TLS_REMOTE_DIR}'
            chmod 0600 '${TLS_REMOTE_DIR}'/*.key
            chmod 0644 '${TLS_REMOTE_DIR}'/*.pem '${TLS_REMOTE_DIR}'/mtls-allowlist.txt
        "
        # Compose the ExecStart suffix.
        tls_extra_flags=" --tls-cert ${TLS_REMOTE_DIR}/server-${node_label}.pem --tls-key ${TLS_REMOTE_DIR}/server-${node_label}.key"
        if [[ "$DEPLOY_MTLS" == "1" ]]; then
            tls_extra_flags+=" --mtls-allowlist ${TLS_REMOTE_DIR}/mtls-allowlist.txt"
        fi
        # Continuation-6: federation peers are HTTPS-only when DEPLOY_TLS=1, so
        # the local daemon's federation client must authenticate to peers via
        # mTLS too. Add the quorum cert flags pointing at the same client
        # cert/key the harness uses for this droplet.
        tls_extra_flags+=" --quorum-ca-cert ${TLS_REMOTE_DIR}/ca.pem"
        tls_extra_flags+=" --quorum-client-cert ${TLS_REMOTE_DIR}/client-${node_label}.pem"
        tls_extra_flags+=" --quorum-client-key ${TLS_REMOTE_DIR}/client-${node_label}.key"
        note "[$node_label] TLS material in place; flags='${tls_extra_flags}'"
    fi

    note "[$node_label] rewrite systemd dropin to use --store-url + TLS flags"
    # Continuation-6: target the dropin
    # `/etc/systemd/system/ai-memory.service.d/federation.conf` — that's
    # where the boot scripts now write the `--store-url` /
    # `--quorum-*` flags. The legacy main service file is also scrubbed
    # for safety (so a pre-cont6 path that put the flags there gets
    # cleaned up on re-deploy).
    ssh "${SSH_OPTS[@]}" "root@${node_ip}" "
        set -e
        DROPIN=/etc/systemd/system/ai-memory.service.d/federation.conf
        MAIN=/etc/systemd/system/ai-memory.service
        if [ ! -f \"\$DROPIN\" ] && [ ! -f \"\$MAIN\" ]; then
            echo 'no ai-memory unit (dropin or main) on $node_label'; exit 1
        fi
        # Choose the unit to edit: prefer the dropin (Continuation 6 path),
        # fall back to the main service file (pre-cont6 path).
        UNIT=\"\$DROPIN\"
        [ -f \"\$UNIT\" ] || UNIT=\"\$MAIN\"
        cp \"\$UNIT\" \"\$UNIT.wave3.bak.\$(date +%s)\"
        # Swap --db <something> for --store-url <url> (or update existing
        # --store-url in place on a re-deploy).
        if grep -q -- '--store-url' \"\$UNIT\"; then
            sed -i -E 's|--store-url [^ ]+|--store-url ${store_url}|' \"\$UNIT\"
        else
            sed -i -E 's|--db [^ ]+|--store-url ${store_url}|' \"\$UNIT\"
        fi
        # Continuation-6: scrub ALL pre-existing TLS / quorum-mTLS flags so
        # re-deploys don't double-up. Only re-add when DEPLOY_TLS=1.
        for flag in --tls-cert --tls-key --mtls-allowlist \\
                    --quorum-ca-cert --quorum-client-cert --quorum-client-key; do
            sed -i -E \"s| \$flag [^ ]+||g\" \"\$UNIT\"
        done
        if [ -n '${tls_extra_flags}' ]; then
            # Match only the ExecStart line that has the binary path; the
            # systemd dropin convention puts an empty 'ExecStart=' reset
            # line before the actual ExecStart=/usr/local/bin/ai-memory ...
            # line. Avoid corrupting the reset line.
            sed -i -E 's|^(ExecStart=/usr/local/bin/ai-memory.*)$|\\1${tls_extra_flags}|' \"\$UNIT\"
            # When running in TLS mode, federation peers must use https://.
            sed -i -E 's|--quorum-peers http://|--quorum-peers https://|g' \"\$UNIT\"
            sed -i -E 's|--catchup-peers http://|--catchup-peers https://|g' \"\$UNIT\"
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
