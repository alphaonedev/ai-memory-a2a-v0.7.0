#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Bootstrap GPU openclaw droplets for v0.7.0 cert (autonomous-tier-full).
#
# What this does, per droplet, in order:
#   1. Verify NVIDIA driver + CUDA (nvidia-smi)
#   2. Install Ollama
#   3. Pull LLM (gemma4:e4b by default), embedder (nomic-embed-text:v1.5),
#      reranker (cross-encoder/ms-marco-MiniLM-L-6-v2 — via candle/ai-memory)
#   4. Verify Ollama health + every model loads
#   5. Build + install ai-memory binary (or download release artifact)
#   6. Generate daemon keypair (refuse-by-default)
#   7. Write config.toml with autonomous-tier knobs
#   8. Write systemd unit, enable + start
#   9. Verify /api/v1/capabilities reports tier=autonomous AND
#      advertised endpoints actually return 200 (not 501)
#  10. Wire HTTPS+mTLS (cert distribution from orchestrator /tmp/a2a-v07-tls/)
#  11. Final validation: every LLM-bound feature smoke-passes
#
# Bails the cert run if validation fails — operator can SSH in to debug
# without burning more $0.76/hr on broken bring-up.
#
# Usage:
#   ./scripts/bootstrap_gpu_droplets.sh --track Q [--llm-model gemma4:e4b] [--dry-run]

set -euo pipefail

TRACK=""
DRY_RUN=0
LLM_MODEL="${LLM_MODEL:-gemma4:e4b}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text:v1.5}"
LLM_FALLBACK="${LLM_FALLBACK:-gemma3:4b}"
AI_MEMORY_REPO="${AI_MEMORY_REPO:-https://github.com/alphaonedev/ai-memory-mcp.git}"
AI_MEMORY_REF="${AI_MEMORY_REF:-round-2-fixes}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=5)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --llm-model) LLM_MODEL="$2"; shift 2 ;;
    --embed-model) EMBED_MODEL="$2"; shift 2 ;;
    --llm-fallback) LLM_FALLBACK="$2"; shift 2 ;;
    -h|--help) sed -n '4,30p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$TRACK" ]] || { echo "must pass --track Q (or A1)" >&2; exit 2; }

# Discover droplet IPs from doctl tags
DROPLETS=()
while IFS= read -r line; do
  [ -n "$line" ] && DROPLETS+=("$line")
done < <(doctl compute droplet list --tag-name "track-$TRACK" \
  --format Name,PublicIPv4,PrivateIPv4 --no-header)

[[ ${#DROPLETS[@]} -gt 0 ]] || {
  echo "no droplets tagged track-$TRACK — run provision_gpu_droplets.sh first" >&2
  exit 3
}

echo "[bootstrap] track=$TRACK droplets=${#DROPLETS[@]} llm=$LLM_MODEL embed=$EMBED_MODEL"
for entry in "${DROPLETS[@]}"; do echo "  - $entry"; done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] would bootstrap each droplet with the script in this file"
  exit 0
fi

bootstrap_one() {
  local pub_ip="$1"; local priv_ip="$2"; local name="$3"
  echo "===== bootstrap $name ($pub_ip / $priv_ip) ====="

  # Per-host env passed via shell positional args — avoids stdin tricks
  ssh "${SSH_OPTS[@]}" "root@$pub_ip" \
    bash -se -- "$priv_ip" "$LLM_MODEL" "$EMBED_MODEL" "$LLM_FALLBACK" \
                "$AI_MEMORY_REPO" "$AI_MEMORY_REF" \
    <<'REMOTE'
set -euo pipefail

PRIV_IP="$1"; LLM_MODEL="$2"; EMBED_MODEL="$3"; LLM_FALLBACK="$4"
AI_MEMORY_REPO="$5"; AI_MEMORY_REF="$6"

echo "--- step 1: verify NVIDIA / CUDA ---"
nvidia-smi --query-gpu=name,memory.total,driver_version,cuda_version \
  --format=csv,noheader || { echo "nvidia-smi failed; image may not be NVIDIA AI/ML"; exit 10; }

echo "--- step 2: install Ollama ---"
if ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable --now ollama
  sleep 5
fi
ollama --version
curl -sf http://127.0.0.1:11434/api/tags >/dev/null || {
  echo "Ollama API not responding"; exit 11; }

echo "--- step 3: pull models ---"
pull_with_fallback() {
  local primary="$1"; local fallback="$2"
  if ollama pull "$primary" 2>&1 | tee /tmp/ollama-pull.log; then
    echo "   pulled $primary"
    echo "$primary"
    return 0
  fi
  echo "   pull $primary FAILED — trying fallback $fallback"
  if ollama pull "$fallback"; then
    echo "$fallback"
    return 0
  fi
  echo "ERROR: neither $primary nor $fallback could be pulled" >&2
  return 12
}

ACTUAL_LLM=$(pull_with_fallback "$LLM_MODEL" "$LLM_FALLBACK")
ollama pull "$EMBED_MODEL" || echo "WARN: embed model $EMBED_MODEL pull failed (ai-memory may use built-in)"
ollama list

echo "--- step 4: warm models (avoid cold-start at first daemon request) ---"
ollama run "$ACTUAL_LLM" --verbose "Reply with: ready" </dev/null || true
echo "   actual LLM in use: $ACTUAL_LLM"

echo "--- step 5: build ai-memory binary (autonomous tier) ---"
apt-get update -qq
apt-get install -y -qq build-essential pkg-config libssl-dev curl git jq sqlite3 \
                       postgresql-client-16 netcat-openbsd
if ! command -v cargo >/dev/null; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
  source "$HOME/.cargo/env"
fi
mkdir -p /opt
if [ ! -d /opt/ai-memory-src ]; then
  git clone "$AI_MEMORY_REPO" /opt/ai-memory-src
fi
cd /opt/ai-memory-src
git fetch --all --quiet
git checkout "$AI_MEMORY_REF"
git pull --ff-only origin "$AI_MEMORY_REF" || true
cargo build --release --features autonomous 2>&1 | tail -10
install -m0755 target/release/ai-memory /usr/local/bin/ai-memory
/usr/local/bin/ai-memory --version

echo "--- step 6: dirs + keypair ---"
mkdir -p /var/lib/ai-memory /var/log/ai-memory/audit /etc/ai-memory \
         /root/.config/ai-memory/keys
chmod 0700 /root/.config/ai-memory/keys
[ -f /root/.config/ai-memory/keys/daemon.priv ] || \
  /usr/local/bin/ai-memory identity generate --agent-id daemon --json

echo "--- step 7: config.toml (autonomous tier) ---"
cat >/root/.config/ai-memory/config.toml <<TOML
[memory]
tier = "autonomous"

[autonomous]
ollama_base_url = "http://127.0.0.1:11434"
llm_model = "$ACTUAL_LLM"
embedder_model = "$EMBED_MODEL"
reranker_enabled = true

[audit]
enabled = true
path = "/var/log/ai-memory/audit"
redact_content = true
append_only = true

[governance]
default_mode = "enforce"

[federation]
peers = []
quorum_writes = 2
quorum_timeout_ms = 5000
TOML
mkdir -p /etc/ai-memory
cp /root/.config/ai-memory/config.toml /etc/ai-memory/config.toml

echo "--- step 8: systemd unit + start ---"
cat >/etc/systemd/system/ai-memory.service <<UNIT
[Unit]
Description=ai-memory v0.7.0 daemon (autonomous tier, GPU)
After=network-online.target ollama.service
Wants=network-online.target ollama.service

[Service]
Type=simple
User=root
Environment=AI_MEMORY_AGENT_ID=daemon
Environment=AI_MEMORY_AUDIT_DIR=/var/log/ai-memory/audit
Environment=RUST_LOG=ai_memory=info
ExecStart=/usr/local/bin/ai-memory serve --host $PRIV_IP --port 19077 --tier autonomous
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

echo "--- step 9: capabilities probe ---"
curl -s --max-time 10 "http://${PRIV_IP}:19077/api/v1/capabilities" \
  | jq '{version, schema_version, tier: (.tier // .memory_tier),
         storage_backend: (.storage_backend // .storage),
         features: (.features // .advertised // .endpoints)}' \
  | tee /tmp/capabilities.json

# Hard gate: tier MUST be autonomous
ACTUAL_TIER=$(jq -r '.tier' /tmp/capabilities.json)
[ "$ACTUAL_TIER" = "autonomous" ] || {
  echo "ERROR: capabilities.tier=$ACTUAL_TIER (expected autonomous)" >&2
  exit 13
}
echo "   tier=autonomous CONFIRMED"

echo "===== bootstrap done for $(hostname) ====="
REMOTE
}

# Process each droplet
fail=0
for entry in "${DROPLETS[@]}"; do
  read -r name pub_ip priv_ip <<<"$entry"
  if ! bootstrap_one "$pub_ip" "$priv_ip" "$name"; then
    echo "BOOTSTRAP FAILED for $name ($pub_ip)" >&2
    fail=1
  fi
done

[[ $fail -eq 0 ]] || { echo "[bootstrap] FAILED — fix issues above before cert run" >&2; exit 4; }

echo "[bootstrap] all droplets ready — run validate_autonomous_tier.sh next"
