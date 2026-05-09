#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Provision DigitalOcean GPU droplets for the v0.7.0 autonomous-tier
# cert track. Idempotent: re-running with the same TRACK skips any
# droplet that already exists.
#
# Usage:
#   ./scripts/provision_gpu_droplets.sh --track A1 [--region nyc3] [--dry-run]
#
# Tracks:
#   A1  single openclaw-on-GPU (smoke test, $0.76/hr)
#       1× gpu-4000adax1-20gb autonomous tier
#   Q   quad-openclaw (selected for v0.7.0 GPU cert)
#       4× gpu-4000adax1-20gb autonomous tier (4-node openclaw mesh)
#       + reuses existing CPU hermes droplet (semantic tier, optional federation peer)
#       + reuses existing postgres+AGE droplet (shared SAL backend)
#
# Cost @ Q track: 4× $0.76/hr = $3.04/hr GPU only
# 17h cert pass: ~$52 GPU + ~$2 hermes/postgres CPU + $50 xAI ≈ $104
# Within $200 hard budget with ~$96 retry/upgrade headroom.
#
# Cost guard: bails before booking if estimated 24h spend would exceed
# DO_BUDGET_USD (default 200). Set DO_BUDGET_USD=0 to disable.

set -euo pipefail

TRACK=""
REGION="nyc3"
DRY_RUN=0
VPC_ID="f1754725-42ce-4c9e-9eb2-ca938184e248"
SSH_KEY_ID="55757076"
GPU_IMAGE="gpu-h100x1-base"   # NVIDIA AI/ML Ready (CUDA + drivers); works on RTX 4000 Ada
GPU_SIZE="gpu-4000adax1-20gb"
GPU_HOURLY="0.76"
DO_BUDGET_USD="${DO_BUDGET_USD:-200}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --gpu-size) GPU_SIZE="$2"; shift 2 ;;
    --image) GPU_IMAGE="$2"; shift 2 ;;
    -h|--help)
      sed -n '4,30p' "$0"
      exit 0
      ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

case "$TRACK" in
  A1) DROPLETS=("a2a-v07-gpu-openclaw-${REGION}-1:10.20.0.10") ;;
  Q)  DROPLETS=("a2a-v07-gpu-openclaw-${REGION}-1:10.20.0.10"
                "a2a-v07-gpu-openclaw-${REGION}-2:10.20.0.11"
                "a2a-v07-gpu-openclaw-${REGION}-3:10.20.0.12"
                "a2a-v07-gpu-openclaw-${REGION}-4:10.20.0.13") ;;
  *) echo "must pass --track A1 (single GPU smoke) or Q (quad-openclaw cert)" >&2; exit 2 ;;
esac

# Cost estimate before any cloud action
n=${#DROPLETS[@]}
est_24h=$(awk -v n="$n" -v r="$GPU_HOURLY" 'BEGIN{printf "%.2f", n*r*24}')
echo "[budget] track=$TRACK droplets=$n hourly=\$$GPU_HOURLY 24h_est=\$$est_24h budget=\$$DO_BUDGET_USD"
if [[ "$DO_BUDGET_USD" != "0" ]]; then
  awk -v est="$est_24h" -v cap="$DO_BUDGET_USD" 'BEGIN{exit !(est>cap)}' && {
    echo "[budget] ERROR: 24h estimate \$$est_24h exceeds cap \$$DO_BUDGET_USD" >&2
    exit 3
  }
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] would create:"
  for entry in "${DROPLETS[@]}"; do
    name="${entry%%:*}"; priv="${entry##*:}"
    echo "  doctl compute droplet create $name \\"
    echo "    --region $REGION --size $GPU_SIZE --image $GPU_IMAGE \\"
    echo "    --vpc-uuid $VPC_ID --ssh-keys $SSH_KEY_ID \\"
    echo "    --tag-names a2a-v07-gpu,track-$TRACK \\"
    echo "    --enable-monitoring --wait"
    echo "    (private IP target: $priv — assigned by VPC DHCP, may differ)"
  done
  exit 0
fi

# Real provisioning
need_doctl() { command -v doctl >/dev/null || { echo "doctl not on PATH" >&2; exit 4; }; }
need_doctl
doctl auth list 2>/dev/null | grep -q current || { echo "doctl not authenticated" >&2; exit 4; }

created=()
for entry in "${DROPLETS[@]}"; do
  name="${entry%%:*}"
  if doctl compute droplet list --format Name --no-header | grep -Fxq "$name"; then
    echo "[skip] $name already exists"
    continue
  fi
  echo "[create] $name"
  doctl compute droplet create "$name" \
    --region "$REGION" --size "$GPU_SIZE" --image "$GPU_IMAGE" \
    --vpc-uuid "$VPC_ID" --ssh-keys "$SSH_KEY_ID" \
    --tag-names "a2a-v07-gpu,track-$TRACK" \
    --enable-monitoring --wait
  created+=("$name")
done

echo
echo "[summary] track=$TRACK created=${#created[@]} (skipped existing)"
for n in "${created[@]}"; do
  doctl compute droplet list --format Name,PublicIPv4,PrivateIPv4,Status \
    --no-header | awk -v n="$n" '$1==n{print "  "$0}'
done

cat <<EOF

Next steps:
  1. ./scripts/bootstrap_gpu_droplets.sh --track $TRACK
       (installs Ollama + bakes models + deploys ai-memory + wires mTLS)
  2. A2A_BACKEND_KIND=postgres TLS_MODE=mtls NHI_TIME_BUDGET_S=1800 \\
       ./scripts/run_round.sh --campaign v0.7.0-gpu-${TRACK}-r1 \\
       --in-scope-from-manifest
  3. Repeat for r2 once r1 is GREEN.
  4. ./scripts/render_gpu_results.py --track $TRACK \\
       --r1-dir runs/v0.7.0-gpu-${TRACK}-r1-<ts> \\
       --r2-dir runs/v0.7.0-gpu-${TRACK}-r2-<ts> \\
       --out docs/gpu-cert.md
  5. Always: ./scripts/teardown_gpu_droplets.sh --track $TRACK
       (release the GPUs the moment the campaign closes; \$$GPU_HOURLY/hr each)
EOF
