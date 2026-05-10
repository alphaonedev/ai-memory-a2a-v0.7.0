#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Tear down v0.7.0 GPU cert droplets. ALWAYS run after the cert window
# closes — they bill at $0.76/hr each.
#
# Usage:
#   ./scripts/teardown_gpu_droplets.sh --track A1 [--dry-run]
#   ./scripts/teardown_gpu_droplets.sh --all  # delete every droplet tagged a2a-v07-gpu
set -euo pipefail

TRACK=""
ALL=0
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="$2"; shift 2 ;;
    --all) ALL=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '4,12p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

if [[ "$ALL" -eq 1 ]]; then
  TAG_FILTER="a2a-v07-gpu"
elif [[ -n "$TRACK" ]]; then
  TAG_FILTER="track-$TRACK"
else
  echo "must pass --track A1|A2 or --all" >&2
  exit 2
fi

IDS=()
while IFS= read -r line; do
  [ -n "$line" ] && IDS+=("$line")
done < <(doctl compute droplet list --tag-name "$TAG_FILTER" \
  --format ID,Name --no-header | awk '{print $1":"$2}')

if [[ ${#IDS[@]} -eq 0 ]]; then
  echo "[teardown] no droplets matched tag=$TAG_FILTER"
  exit 0
fi

echo "[teardown] matched ${#IDS[@]} droplets:"
for entry in "${IDS[@]}"; do
  echo "  - id=${entry%%:*} name=${entry##*:}"
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] would delete the above"
  exit 0
fi

read -r -p "Confirm DELETE these ${#IDS[@]} droplets? (yes/NO): " conf
if [[ "$conf" != "yes" ]]; then
  echo "[teardown] aborted"
  exit 0
fi

for entry in "${IDS[@]}"; do
  id="${entry%%:*}"
  name="${entry##*:}"
  echo "[delete] $name (id=$id)"
  doctl compute droplet delete "$id" --force
done

echo "[teardown] done"
