#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# One-shot bootstrap for the hermes droplet.
# Idempotent — safe to re-run after a partial provision.
#
# Usage:
#   boot_hermes.sh <hermes_public_ip> <openclaw_priv_ip>
#
# Required env (set by orchestrator):
#   AI_MEMORY_BINARY_PATH   local path to the prebuilt ai-memory binary
#   HERMES_REPO             git URL (default: github.com/hermesframework/hermes)
#   HERMES_REF              branch/tag (default: main)
#   AI_MEMORY_AUDIT_DIR     defaults to /var/log/ai-memory/audit/
#   A2A_PORT                defaults to 19077
#   AGENT_ID                defaults to ai:hermes@nyc3:droplet-2

set -euo pipefail

NODE_IP="${1:?usage: boot_hermes.sh <hermes_public_ip> <openclaw_priv_ip>}"
PEER_PRIV_IP="${2:?missing openclaw private IP for the A2A peer}"

HERMES_REPO="${HERMES_REPO:-https://github.com/hermesframework/hermes}"
HERMES_REF="${HERMES_REF:-main}"
AI_MEMORY_BINARY_PATH="${AI_MEMORY_BINARY_PATH:-/Users/fate/v07/v07-fixes/target/release/ai-memory}"
AI_MEMORY_AUDIT_DIR="${AI_MEMORY_AUDIT_DIR:-/var/log/ai-memory/audit/}"
A2A_PORT="${A2A_PORT:-19077}"
AGENT_ID="${AGENT_ID:-ai:hermes@nyc3:droplet-2}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)

echo "===> stage ai-memory binary on ${NODE_IP}"
scp "${SSH_OPTS[@]}" "${AI_MEMORY_BINARY_PATH}" "root@${NODE_IP}:/usr/local/bin/ai-memory"

echo "===> install runtime deps + hermes on ${NODE_IP}"
# v0.7.0 SHIP CAMPAIGN Phase D Round 4e fix — see boot_openclaw.sh
# for the full env-injection rationale. Companion: NODE_INDEX=2,
# AGENT_TYPE=hermes.
ssh "${SSH_OPTS[@]}" "root@${NODE_IP}" bash -s -- \
    "${HERMES_REPO}" "${HERMES_REF}" \
    "${AI_MEMORY_AUDIT_DIR}" "${A2A_PORT}" "${AGENT_ID}" "${PEER_PRIV_IP}" \
    < <(
        echo "NODE_INDEX=2"
        echo "PEER_URLS=\"http://${PEER_PRIV_IP}:${A2A_PORT}\""
        echo "ROLE=agent"
        echo "AGENT_TYPE=hermes"
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
echo "OK: hermes boot complete on ${NODE_IP}"
