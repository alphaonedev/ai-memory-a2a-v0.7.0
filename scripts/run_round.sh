#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Wrapper that exports the v0.7.0 A2A campaign env vars and dispatches
# scripts/run_round1.py for either Round 1 or Round 2.
#
# Usage:
#   CAMPAIGN_ID=v0.7.0-a2a-r1-YYYYMMDD-HHMM ROUND_LABEL="Round 1" ./scripts/run_round.sh
#
# The runner reads scripts/scope-v0.7.0.json and emits SKIP for
# out-of-scope scenarios without executing them.

set -euo pipefail

REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")"/.. &>/dev/null && pwd)"

# Topology — fixed for the v0.7.0 A2A campaign.
export NODE1_IP="${NODE1_IP:-104.236.52.203}"      # openclaw public
export NODE2_IP="${NODE2_IP:-142.93.72.46}"        # hermes public
export NODE3_IP="${NODE3_IP:-${NODE2_IP}}"         # alias to hermes
export NODE4_IP="${NODE4_IP:-${NODE2_IP}}"         # alias to hermes
export NODE1_PRIV="${NODE1_PRIV:-10.20.0.2}"
export NODE2_PRIV="${NODE2_PRIV:-10.20.0.3}"
export NODE3_PRIV="${NODE3_PRIV:-${NODE2_PRIV}}"
export NODE4_PRIV="${NODE4_PRIV:-${NODE2_PRIV}}"
export OPENCLAW_PUBLIC_IP="${OPENCLAW_PUBLIC_IP:-${NODE1_IP}}"
export OPENCLAW_PRIVATE_IP="${OPENCLAW_PRIVATE_IP:-${NODE1_PRIV}}"
export HERMES_PUBLIC_IP="${HERMES_PUBLIC_IP:-${NODE2_IP}}"
export HERMES_PRIVATE_IP="${HERMES_PRIVATE_IP:-${NODE2_PRIV}}"
export AGENT_GROUP="${AGENT_GROUP:-openclaw_hermes}"
export A2A_BASE_PORT="${A2A_BASE_PORT:-19077}"
export TLS_MODE="${TLS_MODE:-off}"

# Postgres + AGE substrate (used by S70-S76).
export POSTGRES_HOST="${POSTGRES_HOST:-10.20.0.4}"
export POSTGRES_NODE_IP="${POSTGRES_NODE_IP:-68.183.157.68}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_DB="${POSTGRES_DB:-aimemory}"
export POSTGRES_USER="${POSTGRES_USER:-aimemory}"
export POSTGRES_PASSWORD_PATH="${POSTGRES_PASSWORD_PATH:-/tmp/v07-a2a-pg-password.txt}"

# xAI for S67/S68
if [ -z "${XAI_API_KEY:-}" ] && [ -r "${HOME}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${HOME}/.env"
  set +a
fi
export XAI_MODEL="${XAI_MODEL:-grok-4.20-0309-reasoning}"

: "${CAMPAIGN_ID:?CAMPAIGN_ID is required}"
: "${ROUND_LABEL:=Round 1}"
export CAMPAIGN_ID ROUND_LABEL

cd "${REPO}"
python3 scripts/run_round1.py
