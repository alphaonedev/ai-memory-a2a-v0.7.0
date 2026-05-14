#!/usr/bin/env bash
# Plan D — fan out the 4-domain ai-memory + IronClaw mesh on the Mac Mini.
# Idempotent — re-runs reuse existing TLS material and reload daemons.
set -eo pipefail

# shellcheck disable=SC1090
. ~/.env

BASE="${BASE:-/Users/fate/v07/test-cell}"
GRAND_SLAM="${GRAND_SLAM:-/Users/fate/v07/grand-slam}"
SHARED_TARGET="${SHARED_TARGET:-/Users/fate/v07/v07-fixes/.cargo-shared-target}"
IRONCLAW_TAR="${IRONCLAW_TAR:-/Users/fate/v07/v07-fixes/.local-runs/ironclaw-install/ironclaw-aarch64-apple-darwin}"
NAMES=(alice bob charlie dave)

# Step 1 — build ai-memory release with sal + sal-postgres.
if [ ! -x "${GRAND_SLAM}/target/release/ai-memory" ] || [ ! -x "${SHARED_TARGET}/release/ai-memory" ]; then
  echo "[plan-d-mac] building ai-memory release"
  ( cd "${GRAND_SLAM}" && \
    TMPDIR=/Users/fate/v07/v07-fixes/.local-runs/tmp \
    CARGO_TARGET_DIR="${SHARED_TARGET}" \
    AI_MEMORY_NO_CONFIG=1 \
    cargo build --release --features sal,sal-postgres )
  mkdir -p "${GRAND_SLAM}/target/release"
  ln -sf "${SHARED_TARGET}/release/ai-memory" "${GRAND_SLAM}/target/release/ai-memory"
fi

# Step 2 — install IronClaw binaries to ~/.local/bin.
mkdir -p ~/.local/bin
install -m 0755 "${IRONCLAW_TAR}/ironclaw"        ~/.local/bin/ironclaw
install -m 0755 "${IRONCLAW_TAR}/sandbox_daemon"  ~/.local/bin/sandbox_daemon
grep -q '$HOME/.local/bin' ~/.zshrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc

# Step 3 — TLS material (CA + 4 node ECDSA certs + allowlist).
mkdir -p "${BASE}/tls"
cp -n "${BASH_SOURCE%/*}/../plan-d/tls/gen-tls.sh" "${BASE}/tls/gen-tls.sh" 2>/dev/null || true
NODES="${NAMES[*]}" bash "${BASE}/tls/gen-tls.sh"

# Step 4 — per-domain dirs + configs + operator key.
mkdir -p "${BASE}"/{alice,bob,charlie,dave}/{audit,logs}
if [ ! -f "${BASE}/operator.key" ]; then
  "${GRAND_SLAM}/target/release/ai-memory" rules keygen --out "${BASE}/operator.key"
  chmod 0600 "${BASE}/operator.key"
fi
bash "${BASE}/gen-configs.sh"

# Step 5 — launch the 4 ai-memory daemons.
bash "${BASE}/launch-daemons.sh"

# Wait for each /api/v1/health to return 200.
echo "[plan-d-mac] waiting for daemons to settle..."
for port in 9077 9078 9079 9080; do
  for try in 1 2 3 4 5 6 7 8 9 10; do
    case $port in 9077) n=alice;; 9078) n=bob;; 9079) n=charlie;; 9080) n=dave;; esac
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 4 \
           --cacert "${BASE}/tls/ca.pem" \
           --cert "${BASE}/tls/node-${n}.pem" \
           --key  "${BASE}/tls/node-${n}.key" \
           "https://127.0.0.1:${port}/api/v1/health" || true)
    [ "$code" = "200" ] && { echo "  $n@$port OK"; break; }
    sleep 2
  done
done

# Step 6 — register IronClaw provider + MCP for every domain.
for name in "${NAMES[@]}"; do
  HOME="${BASE}/${name}/home" \
  DATABASE_URL="postgres://${FED_PG_USER}:${FED_PG_PASSWORD}@${FED_PG_HOST}:${FED_PG_PORT}/${FED_PG_DB}?sslmode=disable&options=-csearch_path%3Dic_${name}%2Cpublic" \
    ~/.local/bin/ironclaw models set-provider openai_compatible --model grok-4.20-0309-reasoning >/dev/null
  HOME="${BASE}/${name}/home" \
  DATABASE_URL="postgres://${FED_PG_USER}:${FED_PG_PASSWORD}@${FED_PG_HOST}:${FED_PG_PORT}/${FED_PG_DB}?sslmode=disable&options=-csearch_path%3Dic_${name}%2Cpublic" \
    ~/.local/bin/ironclaw mcp add memory --transport stdio \
      --command "${GRAND_SLAM}/target/release/ai-memory" \
      --arg=--db --arg="${BASE}/${name}/a2a.db" \
      --arg=mcp --arg=--tier --arg=autonomous \
      --env "AI_MEMORY_AGENT_ID=ai:${name}" \
      --env "HOME=${BASE}/${name}/home" >/dev/null || true
done

# Step 7 — launch IronClaw quad (best-effort; v0.28.1 may need manual
# session bootstrap depending on which channels are enabled).
bash "${BASE}/launch-ironclaw.sh"

echo "[plan-d-mac] done."
