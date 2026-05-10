#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Bootstrap the 2 CPU ai-memory daemons (openclaw + hermes) for the
# v0.7.0 Plan B cert track. tier=semantic (no Ollama, no LLM).
#
# Per droplet:
#   - apt deps + Rust toolchain
#   - clone alphaonedev/ai-memory-mcp at round-2-fixes (HEAD includes G1/G2/G3)
#   - cargo build --release
#   - install binary, generate daemon keypair
#   - config.toml: tier=semantic, postgres URL, federation peer, quorum=2
#   - systemd unit (plain HTTP for now; mTLS wired by wire_mtls_cpu.sh)
#
# Usage:
#   ./scripts/bootstrap_cpu_daemons.sh [--dry-run]
set -euo pipefail

DRY_RUN=0
AI_MEMORY_REPO="${AI_MEMORY_REPO:-https://github.com/alphaonedev/ai-memory-mcp.git}"
AI_MEMORY_REF="${AI_MEMORY_REF:-round-2-fixes}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=5)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --ref) AI_MEMORY_REF="$2"; shift 2 ;;
    --repo) AI_MEMORY_REPO="$2"; shift 2 ;;
    -h|--help) sed -n '4,18p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# Discover droplets + postgres
mapfile -t OC_LINE < <(doctl compute droplet list --tag-name "a2a-v07-cpu" \
  --format Name,PublicIPv4,PrivateIPv4 --no-header 2>/dev/null \
  | awk '/openclaw/{print}')
mapfile -t HM_LINE < <(doctl compute droplet list --tag-name "a2a-v07-cpu" \
  --format Name,PublicIPv4,PrivateIPv4 --no-header 2>/dev/null \
  | awk '/hermes/{print}')
PG_LINE=$(doctl compute droplet list --tag-name "a2a-v07-cpu" \
  --format Name,PublicIPv4,PrivateIPv4 --no-header 2>/dev/null \
  | awk '/-pg-/{print; exit}')

[[ ${#OC_LINE[@]} -ge 1 ]] || { echo "openclaw droplet not found" >&2; exit 3; }
[[ ${#HM_LINE[@]} -ge 1 ]] || { echo "hermes droplet not found" >&2; exit 3; }
[[ -n "$PG_LINE" ]] || { echo "postgres droplet not found" >&2; exit 3; }
read -r OC_NAME OC_PUB OC_PRIV <<<"${OC_LINE[0]}"
read -r HM_NAME HM_PUB HM_PRIV <<<"${HM_LINE[0]}"
read -r _PG_NAME _PG_PUB PG_PRIV <<<"$PG_LINE"

PG_PWD_PATH="${PG_PASSWORD_PATH:-/tmp/v07-a2a-pg-password.txt}"
[[ -f "$PG_PWD_PATH" ]] || { echo "pg password missing at $PG_PWD_PATH (postgres bootstrap should have created it)" >&2; exit 3; }
PG_PWD=$(<"$PG_PWD_PATH")

echo "[bootstrap-cpu-daemons]"
echo "  openclaw  pub=$OC_PUB priv=$OC_PRIV"
echo "  hermes    pub=$HM_PUB priv=$HM_PRIV"
echo "  postgres  priv=$PG_PRIV"
echo "  ref       $AI_MEMORY_REF"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] would bootstrap 2 daemons via SSH (parallel)"
  exit 0
fi

bootstrap_one() {
  local role="$1" pub="$2" priv="$3" peer_priv="$4" agent_id="$5" db_name="$6"
  echo "===== bootstrap $role on $pub ($priv) ====="
  ssh "${SSH_OPTS[@]}" "root@$pub" \
    bash -se -- "$priv" "$peer_priv" "$agent_id" "$db_name" "$PG_PRIV" "$PG_PWD" \
                "$AI_MEMORY_REPO" "$AI_MEMORY_REF" \
    <<'REMOTE'
set -euo pipefail
PRIV_IP="$1"; PEER_PRIV="$2"; AGENT_ID="$3"; DB_NAME="$4"
PG_PRIV="$5"; PG_PWD="$6"
AI_MEMORY_REPO="$7"; AI_MEMORY_REF="$8"

echo "--- step 1: apt deps + Rust ---"
apt-get update -qq
apt-get install -y -qq build-essential pkg-config libssl-dev curl git jq sqlite3 \
                       postgresql-client-16 netcat-openbsd 2>&1 | tail -5
if ! command -v cargo >/dev/null; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
  source "$HOME/.cargo/env"
fi
. "$HOME/.cargo/env" 2>/dev/null || true

echo "--- step 2: clone + build ai-memory ---"
mkdir -p /opt
if [ ! -d /opt/ai-memory-src ]; then
  git clone "$AI_MEMORY_REPO" /opt/ai-memory-src
fi
cd /opt/ai-memory-src
git fetch --all --quiet
git checkout "$AI_MEMORY_REF"
git pull --ff-only origin "$AI_MEMORY_REF" || true
git log --oneline -3
cargo build --release 2>&1 | tail -8
install -m0755 target/release/ai-memory /usr/local/bin/ai-memory
/usr/local/bin/ai-memory --version

echo "--- step 3: dirs + keypair ---"
mkdir -p /var/lib/ai-memory /var/log/ai-memory/audit /etc/ai-memory \
         /root/.config/ai-memory/keys
chmod 0700 /root/.config/ai-memory/keys
[ -f /root/.config/ai-memory/keys/daemon.priv ] || \
  /usr/local/bin/ai-memory identity generate --agent-id daemon --json

echo "--- step 4: pg password file (mode 600) ---"
echo -n "$PG_PWD" > /root/postgres-aimemory-pw.txt
chmod 0600 /root/postgres-aimemory-pw.txt

echo "--- step 5: config.toml (tier=semantic) ---"
cat >/root/.config/ai-memory/config.toml <<TOML
[memory]
tier = "semantic"

[audit]
enabled = true
path = "/var/log/ai-memory/audit"
redact_content = true
append_only = true

[governance]
default_mode = "enforce"

[federation]
peers = ["http://${PEER_PRIV}:19077"]
quorum_writes = 2
quorum_timeout_ms = 5000
TOML
mkdir -p /etc/ai-memory
cp /root/.config/ai-memory/config.toml /etc/ai-memory/config.toml

echo "--- step 6: postgres URL env file (no password in cmdline) ---"
cat >/etc/ai-memory/store-url.env <<ENV
AI_MEMORY_STORE_URL=postgres://aimemory:${PG_PWD}@${PG_PRIV}:5432/aimemory
ENV
chmod 0600 /etc/ai-memory/store-url.env

echo "--- step 7: systemd unit (plain HTTP; mTLS wired in next phase) ---"
cat >/etc/systemd/system/ai-memory.service <<UNIT
[Unit]
Description=ai-memory v0.7.0 daemon (${AGENT_ID}, tier=semantic, postgres SAL)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=/etc/ai-memory/store-url.env
Environment=AI_MEMORY_AGENT_ID=daemon
Environment=AI_MEMORY_AUDIT_DIR=/var/log/ai-memory/audit
Environment=RUST_LOG=ai_memory=info
ExecStart=/usr/local/bin/ai-memory serve --host ${PRIV_IP} --port 19077 --store-url \${AI_MEMORY_STORE_URL}
Restart=on-failure
RestartSec=2
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now ai-memory
sleep 5
systemctl is-active ai-memory

echo "--- step 8: capabilities probe ---"
curl -s --max-time 10 "http://${PRIV_IP}:19077/api/v1/capabilities" \
  | jq '{version, schema_version, tier: (.tier // .memory_tier),
         storage_backend: (.storage_backend // .storage // "?"),
         permissions_mode: (.permissions.mode // .permissions_mode // "?")}'

echo "===== bootstrap done for $(hostname) ====="
REMOTE
}

# Bootstrap both in parallel
bootstrap_one openclaw "$OC_PUB" "$OC_PRIV" "$HM_PRIV" "ai:openclaw@nyc3:droplet-1" "openclaw" \
  > /tmp/bootstrap-openclaw.log 2>&1 &
PID_OC=$!
bootstrap_one hermes "$HM_PUB" "$HM_PRIV" "$OC_PRIV" "ai:hermes@nyc3:droplet-2" "hermes" \
  > /tmp/bootstrap-hermes.log 2>&1 &
PID_HM=$!

echo "  waiting for both bootstraps (PID_OC=$PID_OC PID_HM=$PID_HM)"
wait $PID_OC; OC_RC=$?
wait $PID_HM; HM_RC=$?

echo
echo "=== bootstrap summary ==="
tail -25 /tmp/bootstrap-openclaw.log
echo
tail -25 /tmp/bootstrap-hermes.log
echo
echo "openclaw rc=$OC_RC  hermes rc=$HM_RC"

[[ "$OC_RC" -eq 0 && "$HM_RC" -eq 0 ]] || { echo "BOOTSTRAP FAILED" >&2; exit 4; }

echo
echo "Both daemons up. Next: schema-init, then wire mTLS."
