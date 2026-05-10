#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Provision the 3 CPU droplets for the v0.7.0 Plan B cert track.
#
# Topology:
#   openclaw  s-4vcpu-16gb-amd  10.20.0.2  $0.125/hr
#   hermes    s-4vcpu-16gb-amd  10.20.0.3  $0.125/hr
#   postgres  s-4vcpu-16gb-amd  10.20.0.4  $0.125/hr
#
# Spend: 3× $0.125 = $0.375/hr, ~$7 per 18h cert pass
#
# Idempotent: skips droplets that already exist.
# Usage:
#   ./scripts/provision_cpu_cert.sh [--region nyc3] [--dry-run]
set -euo pipefail

REGION="nyc3"
DRY_RUN=0
VPC_ID="f1754725-42ce-4c9e-9eb2-ca938184e248"
SSH_KEY_ID="55757076"
SIZE="s-4vcpu-16gb-amd"
IMAGE="ubuntu-22-04-x64"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '4,18p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

DROPLETS=(
  "a2a-v07-cpu-openclaw-${REGION}-1:track-CPU-openclaw"
  "a2a-v07-cpu-hermes-${REGION}-1:track-CPU-hermes"
  "a2a-v07-cpu-pg-${REGION}-1:track-CPU-pg"
)

echo "[provision-cpu] $REGION  3× $SIZE  total spend $0.375/hr"

for entry in "${DROPLETS[@]}"; do
  name="${entry%%:*}"; tag="${entry##*:}"
  if doctl compute droplet list --format Name --no-header | grep -Fxq "$name"; then
    echo "[skip] $name already exists"
    continue
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] would create $name (tag: a2a-v07-cpu,$tag)"
    continue
  fi
  echo "[create] $name (tag: a2a-v07-cpu,$tag)"
  doctl compute droplet create "$name" \
    --region "$REGION" --size "$SIZE" --image "$IMAGE" \
    --vpc-uuid "$VPC_ID" --ssh-keys "$SSH_KEY_ID" \
    --tag-names "a2a-v07-cpu,$tag" \
    --enable-monitoring --wait
done

[[ "$DRY_RUN" -eq 1 ]] && exit 0

echo
echo "[provision-cpu] done. Droplets:"
doctl compute droplet list --tag-name a2a-v07-cpu \
  --format Name,PublicIPv4,PrivateIPv4,Status --no-header
echo
echo "Next:"
echo "  ./scripts/bootstrap_postgres_gpu.sh             # auto-tunes for 16 GiB"
echo "  ./scripts/bootstrap_cpu_daemons.sh              # ai-memory tier=semantic"
echo "  ./scripts/run_cpu_cert.sh                       # full pipeline"
