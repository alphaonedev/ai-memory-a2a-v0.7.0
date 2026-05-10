#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Wire mTLS + 4-node federation peer mesh on the GPU openclaw quad after
# bootstrap_gpu_droplets.sh has finished. Distributes per-node server +
# client certs from /tmp/a2a-v07-tls-gpu/ and rewrites each daemon's
# systemd unit to include the mTLS + quorum-mTLS flags.
#
# Run AFTER bootstrap (which deploys daemon + ollama + plain-HTTP unit)
# and BEFORE validate_baseline.sh.
#
# Usage:
#   ./scripts/wire_mtls_quad.sh --track Q [--dry-run]
set -euo pipefail

TRACK=""
DRY_RUN=0
TLS_DIR="/tmp/a2a-v07-tls-gpu"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --tls-dir) TLS_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '4,16p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$TRACK" ]] || { echo "must pass --track Q" >&2; exit 2; }
[[ -d "$TLS_DIR" ]] || { echo "TLS material dir missing: $TLS_DIR" >&2; exit 3; }
[[ -f "$TLS_DIR/ca.pem" ]] || { echo "ca.pem missing in $TLS_DIR" >&2; exit 3; }
[[ -f "$TLS_DIR/mtls-allowlist.txt" ]] || { echo "mtls-allowlist.txt missing in $TLS_DIR" >&2; exit 3; }

# Discover droplet metadata sorted by name (so node-1, -2, -3, -4 line up
# with the cert numbering)
DROPLETS=()
while IFS= read -r line; do
  [ -n "$line" ] && DROPLETS+=("$line")
done < <(doctl compute droplet list --tag-name "track-$TRACK" \
  --format Name,PublicIPv4,PrivateIPv4 --no-header | sort -k1,1)

[[ ${#DROPLETS[@]} -eq 4 ]] || {
  echo "expected 4 droplets in track-$TRACK; found ${#DROPLETS[@]}" >&2
  exit 3
}

echo "[wire-mtls] track=$TRACK droplets=${#DROPLETS[@]} tls_dir=$TLS_DIR"

# Build per-node peer list: each openclaw's federation peers are the other 3
declare -a PRIV_IPS=()
for entry in "${DROPLETS[@]}"; do
  read -r _name _pub priv <<<"$entry"
  PRIV_IPS+=("$priv")
done

peers_for_node() {
  local self_idx="$1"
  local out=""
  for i in 0 1 2 3; do
    if [ "$i" != "$self_idx" ]; then
      [ -n "$out" ] && out+=" "
      out+="https://${PRIV_IPS[$i]}:19077"
    fi
  done
  echo "$out"
}

wire_one() {
  local idx="$1" entry="$2"
  read -r name pub priv <<<"$entry"
  local node_num=$((idx + 1))
  local server_cert="$TLS_DIR/server-gpu-openclaw-${node_num}.pem"
  local server_key="$TLS_DIR/server-gpu-openclaw-${node_num}.key"
  local client_cert="$TLS_DIR/client-gpu-openclaw-${node_num}.pem"
  local client_key="$TLS_DIR/client-gpu-openclaw-${node_num}.key"

  for f in "$server_cert" "$server_key" "$client_cert" "$client_key"; do
    [[ -f "$f" ]] || { echo "missing TLS material: $f" >&2; return 4; }
  done

  echo "===== wire-mtls $name (idx=$idx priv=$priv pub=$pub) ====="

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] would scp: $server_cert $server_key $client_cert $client_key ca.pem mtls-allowlist.txt → root@$pub:/etc/ai-memory-a2a/tls/"
    echo "[dry-run] would set ExecStart with --tls-* + --quorum-* + peers: $(peers_for_node $idx)"
    return 0
  fi

  # SCP material into the canonical /etc/ai-memory-a2a/tls/ location used
  # by the deploy script + harness.
  ssh "${SSH_OPTS[@]}" "root@$pub" "mkdir -p /etc/ai-memory-a2a/tls && chmod 0750 /etc/ai-memory-a2a/tls"
  scp "${SSH_OPTS[@]}" \
      "$TLS_DIR/ca.pem" "$TLS_DIR/mtls-allowlist.txt" \
      "$server_cert" "$server_key" "$client_cert" "$client_key" \
      "root@$pub:/etc/ai-memory-a2a/tls/"
  ssh "${SSH_OPTS[@]}" "root@$pub" "
    cd /etc/ai-memory-a2a/tls
    chmod 0644 ca.pem mtls-allowlist.txt server-gpu-openclaw-${node_num}.pem client-gpu-openclaw-${node_num}.pem
    chmod 0600 server-gpu-openclaw-${node_num}.key client-gpu-openclaw-${node_num}.key
    ls -la
  "

  local peers="$(peers_for_node $idx)"
  local peers_csv="$(echo "$peers" | tr ' ' ',')"

  # Rewrite systemd unit with mTLS + federation + quorum-mTLS flags
  ssh "${SSH_OPTS[@]}" "root@$pub" \
    bash -se -- "$priv" "$node_num" "$peers" "$peers_csv" <<'REMOTE'
set -euo pipefail
PRIV_IP="$1"; NODE_NUM="$2"; PEERS_SPACE="$3"; PEERS_CSV="$4"
TLS=/etc/ai-memory-a2a/tls

cat >/etc/systemd/system/ai-memory.service <<UNIT
[Unit]
Description=ai-memory v0.7.0 daemon (GPU openclaw-${NODE_NUM}, autonomous tier, mTLS, 4-node mesh)
After=network-online.target ollama.service
Wants=network-online.target ollama.service

[Service]
Type=simple
User=root
Environment=AI_MEMORY_AGENT_ID=daemon
Environment=AI_MEMORY_AUDIT_DIR=/var/log/ai-memory/audit
Environment=RUST_LOG=ai_memory=info
ExecStart=/usr/local/bin/ai-memory serve \\
    --host ${PRIV_IP} --port 19077 \\
    --tier autonomous \\
    --tls-cert ${TLS}/server-gpu-openclaw-${NODE_NUM}.pem \\
    --tls-key ${TLS}/server-gpu-openclaw-${NODE_NUM}.key \\
    --tls-ca-cert ${TLS}/ca.pem \\
    --mtls-allowlist ${TLS}/mtls-allowlist.txt \\
    --quorum-writes 2 \\
    --quorum-peers ${PEERS_CSV} \\
    --quorum-client-cert ${TLS}/client-gpu-openclaw-${NODE_NUM}.pem \\
    --quorum-client-key ${TLS}/client-gpu-openclaw-${NODE_NUM}.key \\
    --quorum-ca-cert ${TLS}/ca.pem
Restart=on-failure
RestartSec=2
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl restart ai-memory
sleep 5
systemctl is-active ai-memory
echo "  daemon active. peers=${PEERS_SPACE}"

# Smoke: HTTPS + cert presented
curl --silent --max-time 5 --cacert "${TLS}/ca.pem" \
  --cert "${TLS}/client-gpu-openclaw-${NODE_NUM}.pem" \
  --key  "${TLS}/client-gpu-openclaw-${NODE_NUM}.key" \
  "https://${PRIV_IP}:19077/api/v1/capabilities" \
  | head -c 200; echo
REMOTE
}

# Wire each node — sequential to keep output ordered
fail=0
for idx in 0 1 2 3; do
  if ! wire_one "$idx" "${DROPLETS[$idx]}"; then
    fail=1
    echo "WIRE FAILED for node $idx"
  fi
done

[[ $fail -eq 0 ]] || { echo "[wire-mtls] FAILED — fix issues above" >&2; exit 4; }

echo
echo "[wire-mtls] all 4 droplets wired with mTLS + federation. Run validate_baseline.sh next."
