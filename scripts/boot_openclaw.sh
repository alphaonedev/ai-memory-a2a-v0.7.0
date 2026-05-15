#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# One-shot bootstrap for the openclaw droplet.
# Idempotent — safe to re-run after a partial provision.
#
# Usage:
#   boot_openclaw.sh <openclaw_public_ip> <hermes_priv_ip>
#
# Required env (set by orchestrator):
#   AI_MEMORY_BINARY_PATH   local path to the prebuilt ai-memory binary
#   OPENCLAW_REPO           git URL (default: github.com/openclawai/openclaw)
#   OPENCLAW_REF            branch/tag (default: main)
#   AI_MEMORY_AUDIT_DIR     defaults to /var/log/ai-memory/audit/
#   A2A_PORT                defaults to 19077
#   AGENT_ID                defaults to ai:openclaw@nyc3:droplet-1

set -euo pipefail

NODE_IP="${1:?usage: boot_openclaw.sh <openclaw_public_ip> <hermes_priv_ip>}"
PEER_PRIV_IP="${2:?missing hermes private IP for the A2A peer}"

OPENCLAW_REPO="${OPENCLAW_REPO:-https://github.com/openclawai/openclaw}"
OPENCLAW_REF="${OPENCLAW_REF:-main}"
AI_MEMORY_BINARY_PATH="${AI_MEMORY_BINARY_PATH:-/Users/fate/v07/v07-fixes/target/release/ai-memory}"
AI_MEMORY_AUDIT_DIR="${AI_MEMORY_AUDIT_DIR:-/var/log/ai-memory/audit/}"
A2A_PORT="${A2A_PORT:-19077}"
AGENT_ID="${AGENT_ID:-ai:openclaw@nyc3:droplet-1}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)

echo "===> stage ai-memory binary on ${NODE_IP}"
scp "${SSH_OPTS[@]}" "${AI_MEMORY_BINARY_PATH}" "root@${NODE_IP}:/usr/local/bin/ai-memory"

echo "===> install runtime deps + openclaw on ${NODE_IP}"
# v0.7.0 SHIP CAMPAIGN Phase D Round 4e fix — setup_node.sh requires
# NODE_INDEX + PEER_URLS + ROLE + AGENT_TYPE + AGENT_ID env. Inject
# them all by prepending assignments to the bash heredoc piped through
# ssh stdin. PEER_URLS is the single hermes peer (2-node deployment
# under workflow_dispatch; setup_node.sh's 4-node mode degrades to
# W=2 quorum mode against the available peer list).
ssh "${SSH_OPTS[@]}" "root@${NODE_IP}" bash -s -- \
    "${OPENCLAW_REPO}" "${OPENCLAW_REF}" \
    "${AI_MEMORY_AUDIT_DIR}" "${A2A_PORT}" "${AGENT_ID}" "${PEER_PRIV_IP}" \
    < <(
        echo "NODE_INDEX=1"
        echo "PEER_URLS=\"http://${PEER_PRIV_IP}:${A2A_PORT}\""
        echo "ROLE=agent"
        echo "AGENT_TYPE=openclaw"
        echo "AGENT_ID=\"${AGENT_ID}\""
        echo "AI_MEMORY_AUDIT_DIR=\"${AI_MEMORY_AUDIT_DIR}\""
        echo "A2A_PORT=\"${A2A_PORT}\""
        echo "TLS_MODE=\"${TLS_MODE:-mtls}\""
        echo "XAI_API_KEY=\"${XAI_API_KEY:-}\""
        echo "XAI_MODEL=\"${XAI_MODEL:-grok-4-0709}\""
        cat "${SCRIPT_DIR}/setup_node.sh"
    )

echo "===> verify"
ssh "${SSH_OPTS[@]}" "root@${NODE_IP}" "ai-memory --version && systemctl is-active ai-memory && ss -ltnp | grep -E ':(${A2A_PORT})\b' || true"
echo "OK: openclaw boot complete on ${NODE_IP}"
