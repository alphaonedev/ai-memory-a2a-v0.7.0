#!/usr/bin/env bash
# Plan C — ai-memory daemon entrypoint.
# Generates daemon keypair on first start, writes config, then exec's serve.
set -euo pipefail

# Required env (validated):
: "${AI_MEMORY_AGENT_ID:?missing}"
: "${AI_MEMORY_STORE_URL:?missing}"
: "${AI_MEMORY_LISTEN_HOST:?missing}"
: "${AI_MEMORY_LISTEN_PORT:?missing}"
: "${OLLAMA_BASE_URL:?missing}"

# Optional env
TIER="${AI_MEMORY_TIER:-autonomous}"
LLM_MODEL="${AI_MEMORY_LLM_MODEL:-gemma4:e4b}"
EMBED_MODEL="${AI_MEMORY_EMBED_MODEL:-nomic-embed-text}"
PEER_URLS="${AI_MEMORY_PEER_URLS:-}"
TLS_DIR="${AI_MEMORY_TLS_DIR:-/etc/ai-memory-a2a/tls}"

mkdir -p /etc/ai-memory /root/.config/ai-memory

# Daemon keypair (refuse-by-default per Round-4 fix)
if [ ! -f /root/.config/ai-memory/keys/daemon.priv ]; then
  /usr/local/bin/ai-memory identity generate --agent-id daemon --json
fi

# config.toml — autonomous tier full
cat >/root/.config/ai-memory/config.toml <<TOML
[memory]
tier = "${TIER}"

[autonomous]
ollama_base_url = "${OLLAMA_BASE_URL}"
llm_model = "${LLM_MODEL}"
embedder_model = "${EMBED_MODEL}"
reranker_enabled = true

[audit]
enabled = true
path = "/var/log/ai-memory/audit"
redact_content = true
append_only = true

[governance]
default_mode = "enforce"

[federation]
peers = [${PEER_URLS:+\"${PEER_URLS}\"}]
quorum_writes = 2
quorum_timeout_ms = 5000
TOML
mkdir -p /etc/ai-memory
cp /root/.config/ai-memory/config.toml /etc/ai-memory/config.toml

# mTLS args (only when TLS material is mounted)
TLS_FLAGS=""
if [ -f "$TLS_DIR/server.pem" ] && [ -f "$TLS_DIR/server.key" ]; then
  TLS_FLAGS="--tls-cert $TLS_DIR/server.pem --tls-key $TLS_DIR/server.key"
  if [ -f "$TLS_DIR/mtls-allowlist.txt" ]; then
    TLS_FLAGS="$TLS_FLAGS --mtls-allowlist $TLS_DIR/mtls-allowlist.txt"
  fi
  if [ -f "$TLS_DIR/ca.pem" ]; then
    TLS_FLAGS="$TLS_FLAGS --tls-ca-cert $TLS_DIR/ca.pem"
  fi
fi

# Quorum mTLS (when peer set + client cert)
QUORUM_FLAGS=""
if [ -n "$PEER_URLS" ]; then
  QUORUM_FLAGS="--quorum-writes 2 --quorum-peers $PEER_URLS"
  if [ -f "$TLS_DIR/client.pem" ] && [ -f "$TLS_DIR/client.key" ]; then
    QUORUM_FLAGS="$QUORUM_FLAGS --quorum-client-cert $TLS_DIR/client.pem --quorum-client-key $TLS_DIR/client.key"
    [ -f "$TLS_DIR/ca.pem" ] && QUORUM_FLAGS="$QUORUM_FLAGS --quorum-ca-cert $TLS_DIR/ca.pem"
  fi
fi

echo "[entrypoint] starting ai-memory:"
echo "  agent_id=$AI_MEMORY_AGENT_ID tier=$TIER listen=$AI_MEMORY_LISTEN_HOST:$AI_MEMORY_LISTEN_PORT"
echo "  store_url=$(echo $AI_MEMORY_STORE_URL | sed -E 's|//[^@]+@|//<redacted>@|')"
echo "  ollama=$OLLAMA_BASE_URL llm=$LLM_MODEL embed=$EMBED_MODEL"
echo "  peers=${PEER_URLS:-(none)}"
echo "  tls_flags='$TLS_FLAGS'"
echo "  quorum_flags='$QUORUM_FLAGS'"

export AI_MEMORY_DB=/var/lib/ai-memory/daemon.db
export AI_MEMORY_AGENT_ID=daemon
export RUST_LOG="${RUST_LOG:-ai_memory=info}"

exec /usr/local/bin/ai-memory serve \
  --host "$AI_MEMORY_LISTEN_HOST" --port "$AI_MEMORY_LISTEN_PORT" \
  --tier "$TIER" \
  --store-url "$AI_MEMORY_STORE_URL" \
  $TLS_FLAGS \
  $QUORUM_FLAGS
