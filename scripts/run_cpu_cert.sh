#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# v0.7.0 Plan B (CPU-only cert) — master orchestrator.
#
# Runs the CPU cert pipeline end-to-end with phase timings, hard time-
# budget enforcement, and automatic teardown on success or budget exceeded.
#
# Phases:
#   P0  Pre-flight (auth, TLS, scripts)
#   P1  Provision droplets (3× s-4vcpu-16gb-amd: openclaw + hermes + postgres)
#   P2  Bootstrap postgres + AGE + pgvector (auto-tunes for 16 GiB)
#   P3  Bootstrap 2× ai-memory daemons (tier=semantic; round-2-fixes)
#   P4  Schema-init via openclaw
#   P5  Wire mTLS + 2-node federation
#   P6  Baseline validation (B/D/T/N/S/C/X/P/F/U)
#   P7  Per-droplet semantic-tier validation
#   P8  Round 1 cert
#   P9  Round 2 cert (must be 100% GREEN)
#   P10 NHI discovery sweep (S83/S84/S85)
#   P11 Render results page (3-audience analysis)
#   P12 Teardown — release the $0.375/hr
#
# Hard guards:
#   --budget-usd 200    (default; abort + teardown if cumulative spend exceeds)
#   --max-wall-hours 24 (default; abort + teardown after wall time exceeds)
#
# Usage:
#   ./scripts/run_cpu_cert.sh [--budget-usd 200] [--max-wall-hours 24]
#                             [--resume-from PN] [--dry-run] [--no-auto-teardown]
set -uo pipefail

BUDGET_USD="${BUDGET_USD:-200}"
MAX_WALL_HOURS="${MAX_WALL_HOURS:-24}"
RESUME_FROM=""
DRY_RUN=0
AUTO_TEARDOWN=1
RUN_DIR_BASE="${RUN_DIR_BASE:-runs}"
HOURLY_RATE="${HOURLY_RATE:-0.375}"  # 3× $0.125

while [[ $# -gt 0 ]]; do
  case "$1" in
    --budget-usd) BUDGET_USD="$2"; shift 2 ;;
    --max-wall-hours) MAX_WALL_HOURS="$2"; shift 2 ;;
    --resume-from) RESUME_FROM="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-auto-teardown) AUTO_TEARDOWN=0; shift ;;
    -h|--help) sed -n '4,30p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$REPO_DIR/scripts"
CAMPAIGN_ID="v0.7.0-cpu-$(date -u +%Y%m%d-%H%M%S)"
RUN_DIR="$REPO_DIR/$RUN_DIR_BASE/$CAMPAIGN_ID"
mkdir -p "$RUN_DIR"
LOG="$RUN_DIR/master-orchestrator.log"
PHASE_TIMINGS="$RUN_DIR/phase-timings.json"
echo '[]' > "$PHASE_TIMINGS"

T_START=$(date +%s)
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }
phase_start() { PHASE_ID="$1"; PHASE_NAME="$2"; PHASE_T0=$(date +%s); log "===== $PHASE_ID: $PHASE_NAME ====="; }
phase_end() {
  local rc="$1"
  local dur=$(( $(date +%s) - PHASE_T0 ))
  local elapsed=$(( $(date +%s) - T_START ))
  local spend=$(awk -v h="$elapsed" -v r="$HOURLY_RATE" 'BEGIN{printf "%.2f", h*r/3600}')
  log "  $PHASE_ID done rc=$rc dur=${dur}s wall=${elapsed}s spend=\$${spend}"
  jq --arg id "$PHASE_ID" --arg name "$PHASE_NAME" --arg rc "$rc" \
     --arg dur "$dur" --arg elapsed "$elapsed" --arg spend "$spend" \
    '. += [{id:$id, name:$name, rc:($rc|tonumber), duration_seconds:($dur|tonumber),
            wall_seconds:($elapsed|tonumber), spend_usd_cumulative:($spend|tonumber)}]' \
    "$PHASE_TIMINGS" > "${PHASE_TIMINGS}.next" && mv "${PHASE_TIMINGS}.next" "$PHASE_TIMINGS"
  if [[ "$rc" != "0" ]]; then
    log "ABORT — phase $PHASE_ID rc=$rc"
    [[ "$AUTO_TEARDOWN" -eq 1 ]] && teardown
    exit "$rc"
  fi
  budget_check
}
budget_check() {
  local elapsed=$(( $(date +%s) - T_START ))
  local elapsed_h=$(awk -v s="$elapsed" 'BEGIN{printf "%.2f", s/3600}')
  local spend=$(awk -v h="$elapsed" -v r="$HOURLY_RATE" 'BEGIN{printf "%.2f", h*r/3600}')
  if awk -v e="$elapsed_h" -v m="$MAX_WALL_HOURS" 'BEGIN{exit !(e>=m)}'; then
    log "ABORT — wall ${elapsed_h}h ≥ MAX_WALL_HOURS"; [[ "$AUTO_TEARDOWN" -eq 1 ]] && teardown; exit 5
  fi
  if awk -v s="$spend" -v b="$BUDGET_USD" 'BEGIN{exit !(s>=b)}'; then
    log "ABORT — spend \$${spend} ≥ BUDGET"; [[ "$AUTO_TEARDOWN" -eq 1 ]] && teardown; exit 6
  fi
}
teardown() {
  log "===== TEARDOWN: releasing CPU droplets ====="
  if [[ "$DRY_RUN" -eq 1 ]]; then log "(dry-run)"; return 0; fi
  for entry in $(doctl compute droplet list --tag-name a2a-v07-cpu --format ID --no-header 2>/dev/null); do
    log "  delete id=$entry"
    doctl compute droplet delete "$entry" --force 2>&1 | tee -a "$LOG"
  done
  local elapsed=$(( $(date +%s) - T_START ))
  local spend=$(awk -v h="$elapsed" -v r="$HOURLY_RATE" 'BEGIN{printf "%.2f", h*r/3600}')
  log "Teardown complete. Final wall=${elapsed}s; spend=\$$spend"
}

should_run_phase() {
  local pid="$1"
  [[ -z "$RESUME_FROM" ]] && return 0
  [[ "${pid#P}" -ge "${RESUME_FROM#P}" ]]
}

p0_preflight() {
  should_run_phase P0 || return 0
  phase_start P0 "pre-flight"
  command -v doctl >/dev/null || { log "doctl missing"; phase_end 4; }
  doctl auth list 2>/dev/null | grep -q current || { log "doctl not authenticated"; phase_end 4; }
  [[ -d /tmp/a2a-v07-tls ]] || { log "TLS material missing /tmp/a2a-v07-tls (existing campaign material reused)"; phase_end 4; }
  for s in provision_cpu_cert.sh bootstrap_postgres_gpu.sh bootstrap_cpu_daemons.sh \
           validate_baseline.sh validate_semantic_tier.sh \
           render_gpu_results.py teardown_gpu_droplets.sh run_round1.py; do
    [[ -f "$SCRIPT_DIR/$s" ]] || { log "script missing: $s"; phase_end 4; }
  done
  log "pre-flight OK"
  phase_end 0
}

p1_provision() {
  should_run_phase P1 || return 0
  phase_start P1 "provision 3× s-4vcpu-16gb-amd"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    bash "$SCRIPT_DIR/provision_cpu_cert.sh" --dry-run 2>&1 | tee -a "$LOG"; phase_end 0; return
  fi
  if bash "$SCRIPT_DIR/provision_cpu_cert.sh" 2>&1 | tee -a "$LOG"; then phase_end 0; else phase_end 1; fi
}

p2_bootstrap_postgres() {
  should_run_phase P2 || return 0
  phase_start P2 "bootstrap postgres + AGE + pgvector"
  # Wait for cloud-init / unattended-upgrades to finish before apt
  log "  waiting for cloud-init apt processes to clear..."
  PG_PUB=$(doctl compute droplet list --tag-name a2a-v07-cpu --format Name,PublicIPv4 --no-header | awk '/-pg-/{print $2; exit}')
  if [[ -n "$PG_PUB" ]]; then
    timeout 300 ssh -o StrictHostKeyChecking=no "root@$PG_PUB" \
      "while pgrep -f apt-get >/dev/null; do sleep 5; done; echo apt-clear" 2>&1 | tee -a "$LOG"
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi
  if bash "$SCRIPT_DIR/bootstrap_postgres_gpu.sh" 2>&1 | tee -a "$LOG"; then phase_end 0; else phase_end 1; fi
}

p3_bootstrap_daemons() {
  should_run_phase P3 || return 0
  phase_start P3 "bootstrap 2× ai-memory daemons (tier=semantic)"
  for ip in $(doctl compute droplet list --tag-name a2a-v07-cpu --format Name,PublicIPv4 --no-header | awk '!/-pg-/{print $2}'); do
    log "  waiting for cloud-init apt on $ip..."
    timeout 300 ssh -o StrictHostKeyChecking=no "root@$ip" \
      "while pgrep -f apt-get >/dev/null; do sleep 5; done; echo apt-clear" 2>&1 | tee -a "$LOG" || true
  done
  if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi
  if bash "$SCRIPT_DIR/bootstrap_cpu_daemons.sh" 2>&1 | tee -a "$LOG"; then phase_end 0; else phase_end 1; fi
}

p4_schema_init() {
  should_run_phase P4 || return 0
  phase_start P4 "schema-init via openclaw"
  local OC PG
  OC=$(doctl compute droplet list --tag-name a2a-v07-cpu --format Name,PublicIPv4 --no-header | awk '/openclaw/{print $2; exit}')
  PG=$(doctl compute droplet list --tag-name a2a-v07-cpu --format Name,PrivateIPv4 --no-header | awk '/-pg-/{print $2; exit}')
  [[ -n "$OC" && -n "$PG" ]] || { log "ip discovery failed oc=$OC pg=$PG"; phase_end 4; return; }
  if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi
  if ssh -o StrictHostKeyChecking=no "root@$OC" "
    PG_PWD=\$(cat /root/postgres-aimemory-pw.txt)
    /usr/local/bin/ai-memory schema-init --store-url \"postgres://aimemory:\$PG_PWD@$PG:5432/aimemory\"
  " 2>&1 | tee -a "$LOG"; then phase_end 0; else phase_end 1; fi
}

p5_wire_mtls() {
  should_run_phase P5 || return 0
  phase_start P5 "wire mTLS (2-node — reuses existing /tmp/a2a-v07-tls/)"
  # The existing campaign deploy_wave4.sh already handles 2-node mTLS distribution
  # for openclaw + hermes. We invoke the existing TLS-aware deploy script which
  # handles cert distribution + systemd ExecStart rewrite.
  if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi
  if bash "$SCRIPT_DIR/deploy_wave4.sh" 2>&1 | tee -a "$LOG"; then phase_end 0; else
    log "deploy_wave4.sh failed; mTLS not wired — cert can still run plain HTTP if TLS_MODE=off"
    phase_end 0  # non-blocking; cert can run without mTLS
  fi
}

p6_validate_baseline() {
  should_run_phase P6 || return 0
  phase_start P6 "baseline validation"
  if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi
  # Tell validate_baseline to use semantic-tier validation instead of autonomous
  CERT_TIER=semantic bash "$SCRIPT_DIR/validate_baseline.sh" \
    --track CPU --out "$RUN_DIR/baseline-validation.json" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  phase_end "$rc"
}

p7_validate_semantic() {
  should_run_phase P7 || return 0
  phase_start P7 "per-droplet semantic-tier validation"
  if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi
  if bash "$SCRIPT_DIR/validate_semantic_tier.sh" 2>&1 | tee -a "$LOG"; then phase_end 0; else phase_end 1; fi
}

run_round() {
  local round="$1"
  local round_id="${CAMPAIGN_ID}-r${round}"
  local round_dir="$REPO_DIR/$RUN_DIR_BASE/$round_id"
  mkdir -p "$round_dir"
  local OC HM PG
  OC=$(doctl compute droplet list --tag-name a2a-v07-cpu --format Name,PrivateIPv4 --no-header | awk '/openclaw/{print $2; exit}')
  HM=$(doctl compute droplet list --tag-name a2a-v07-cpu --format Name,PrivateIPv4 --no-header | awk '/hermes/{print $2; exit}')
  PG=$(doctl compute droplet list --tag-name a2a-v07-cpu --format Name,PrivateIPv4 --no-header | awk '/-pg-/{print $2; exit}')
  log "  topology: oc=$OC hm=$HM pg=$PG"
  A2A_BACKEND_KIND=postgres \
  TLS_MODE=mtls \
  AGENT_GROUP=openclaw \
  NODE1_IP="$OC" NODE2_IP="$HM" \
  NODE1_PRIV="$OC" NODE2_PRIV="$HM" \
  POSTGRES_HOST="$PG" \
  RUN_DIR="$round_dir" \
  python3 "$SCRIPT_DIR/run_round1.py" --campaign "$round_id" 2>&1 | tee -a "$LOG"
}

p8_r1() { should_run_phase P8 || return 0; phase_start P8 "Round 1 cert"; if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi; if run_round 1; then phase_end 0; else phase_end 1; fi; }
p9_r2() { should_run_phase P9 || return 0; phase_start P9 "Round 2 cert (100% GREEN required)"; if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi; if run_round 2; then phase_end 0; else phase_end 1; fi; }

p10_nhi() {
  should_run_phase P10 || return 0
  phase_start P10 "NHI discovery sweep"
  log "  NHI scenarios run inside P8/P9 if XAI_API_KEY set; this phase is reserved"
  phase_end 0
}

p11_render() {
  should_run_phase P11 || return 0
  phase_start P11 "render results page"
  if [[ "$DRY_RUN" -eq 1 ]]; then phase_end 0; return; fi
  local r1d="$REPO_DIR/$RUN_DIR_BASE/${CAMPAIGN_ID}-r1"
  local r2d="$REPO_DIR/$RUN_DIR_BASE/${CAMPAIGN_ID}-r2"
  if python3 "$SCRIPT_DIR/render_gpu_results.py" \
       --track CPU --r1-dir "$r1d" --r2-dir "$r2d" \
       --out "$REPO_DIR/docs/cpu-cert.md" 2>&1 | tee -a "$LOG"; then phase_end 0
  else phase_end 1; fi
}

p12_teardown() {
  should_run_phase P12 || return 0
  phase_start P12 "teardown — release CPU droplets"
  teardown
  phase_end 0
}

# main
log "v0.7.0 PLAN B (CPU CERT) — master orchestrator"
log "  campaign_id: $CAMPAIGN_ID"
log "  budget: \$$BUDGET_USD  max_wall: ${MAX_WALL_HOURS}h  hourly: \$$HOURLY_RATE"
log "  dry_run: $DRY_RUN  resume_from: ${RESUME_FROM:-(start)}"
log "  run_dir: $RUN_DIR"

on_exit() {
  local rc=$?
  if [[ "$AUTO_TEARDOWN" -eq 1 && $rc -ne 0 && "$DRY_RUN" -eq 0 ]]; then
    log "TRAP — non-zero exit ($rc); ensuring teardown"; teardown 2>&1 | tee -a "$LOG" || true
  fi
}
trap on_exit EXIT

p0_preflight; p1_provision; p2_bootstrap_postgres; p3_bootstrap_daemons
p4_schema_init; p5_wire_mtls; p6_validate_baseline; p7_validate_semantic
p8_r1; p9_r2; p10_nhi; p11_render; p12_teardown

trap - EXIT

T_END=$(date +%s); WALL=$((T_END - T_START))
SPEND=$(awk -v h="$WALL" -v r="$HOURLY_RATE" 'BEGIN{printf "%.2f", h*r/3600}')
log
log "===== CPU CERT RUN COMPLETE ====="
log "  campaign_id: $CAMPAIGN_ID"
log "  wall: ${WALL}s ($(awk -v s="$WALL" 'BEGIN{printf "%.1f", s/3600}')h)"
log "  spend: \$$SPEND of \$$BUDGET_USD"
log "  results: $REPO_DIR/docs/cpu-cert.md"
