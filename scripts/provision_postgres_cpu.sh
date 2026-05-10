#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Provision the CPU postgres droplet for the v0.7.0 GPU cert track Q
# topology (DO approved 4 GPUs only; postgres stays on CPU SKU).
#
# Spec:
#   SKU:       s-4vcpu-16gb-amd  ($0.125/hr)
#   Region:    nyc3 (matches openclaw quad VPC)
#   Image:     ubuntu-22-04-x64
#   Private:   10.20.0.4
#   Tag:       a2a-v07-pg, track-Q-pg
#
# Idempotent: re-running with the same name skips creation if the
# droplet already exists.
#
# Usage:
#   ./scripts/provision_postgres_cpu.sh [--region nyc3] [--dry-run]
set -euo pipefail

REGION="nyc3"
DRY_RUN=0
VPC_ID="f1754725-42ce-4c9e-9eb2-ca938184e248"
SSH_KEY_ID="55757076"
SIZE="s-4vcpu-16gb-amd"
IMAGE="ubuntu-22-04-x64"
NAME_BASE="a2a-v07-pg-cpu"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --size) SIZE="$2"; shift 2 ;;
    -h|--help) sed -n '4,21p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

NAME="${NAME_BASE}-${REGION}-1"

echo "[provision-pg-cpu] $NAME ($SIZE in $REGION; private 10.20.0.4)"

if doctl compute droplet list --format Name --no-header | grep -Fxq "$NAME"; then
  echo "[skip] $NAME already exists"
  doctl compute droplet list --format Name,PublicIPv4,PrivateIPv4,Status \
    --no-header | awk -v n="$NAME" '$1==n{print "  "$0}'
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] would create:"
  echo "  doctl compute droplet create $NAME \\"
  echo "    --region $REGION --size $SIZE --image $IMAGE \\"
  echo "    --vpc-uuid $VPC_ID --ssh-keys $SSH_KEY_ID \\"
  echo "    --tag-names a2a-v07-pg,track-Q-pg \\"
  echo "    --enable-monitoring --wait"
  echo "    (private IP target: 10.20.0.4 — assigned by VPC DHCP, may differ)"
  exit 0
fi

command -v doctl >/dev/null || { echo "doctl not on PATH" >&2; exit 4; }
doctl auth list 2>/dev/null | grep -q current || { echo "doctl not authenticated" >&2; exit 4; }

doctl compute droplet create "$NAME" \
  --region "$REGION" --size "$SIZE" --image "$IMAGE" \
  --vpc-uuid "$VPC_ID" --ssh-keys "$SSH_KEY_ID" \
  --tag-names "a2a-v07-pg,track-Q-pg" \
  --enable-monitoring --wait

doctl compute droplet list --format Name,PublicIPv4,PrivateIPv4,Status \
  --no-header | awk -v n="$NAME" '$1==n{print "  "$0}'

echo
echo "[provision-pg-cpu] done. Next:"
echo "  ./scripts/bootstrap_postgres_gpu.sh --track Q-pg"
echo "    (script auto-detects RAM and tunes accordingly; works on this 16 GiB CPU droplet)"
