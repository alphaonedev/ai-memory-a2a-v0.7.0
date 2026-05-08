#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Wrapper around `ai-memory audit verify` for S57 + post-run integrity checks.
#
# Usage:
#   verify_audit_chain.sh <node_ip> [audit_dir]
#
# audit_dir defaults to /var/log/ai-memory/audit/ (matches AI_MEMORY_AUDIT_DIR
# in the campaign env). Exits 0 on PASS, 2 on chain break, 3 on missing dir.

set -euo pipefail

NODE_IP="${1:?usage: verify_audit_chain.sh <node_ip> [audit_dir]}"
DIR="${2:-/var/log/ai-memory/audit/}"

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)

# 1) presence
if ! ssh "${SSH_OPTS[@]}" "root@${NODE_IP}" "test -d ${DIR}"; then
  echo "FAIL: audit dir ${DIR} missing on ${NODE_IP}" >&2
  exit 3
fi

# 2) verify
if ! ssh "${SSH_OPTS[@]}" "root@${NODE_IP}" \
    "ai-memory audit verify --dir ${DIR}"; then
  echo "FAIL: ai-memory audit verify --dir ${DIR} returned non-zero on ${NODE_IP}" >&2
  exit 2
fi

# 3) summary
ssh "${SSH_OPTS[@]}" "root@${NODE_IP}" "ai-memory audit verify --dir ${DIR} --json" \
    | jq -r '"OK chain_length=\(.chain_length // .entries // 0) head=\(.head_hash // .head // "?")"'
