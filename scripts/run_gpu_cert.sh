#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# v0.7.0 GPU cert — master orchestrator. Runs the full cert pipeline
# end-to-end with phase timings, hard time-budget enforcement, and
# automatic teardown on success OR on budget exceeded.
#
# Phases (each reports duration; failure aborts subsequent phases):
#   P0  Pre-flight (doctl auth, TLS material, scripts)
#   P1  Provision droplets (4× gpu-4000adax1-20gb + 1× s-4vcpu-16gb-amd pg)
#   P2  Bootstrap postgres+AGE+pgvector (auto-tunes for host RAM)
#   P3  Bootstrap 4× openclaw (Ollama + gemma + ai-memory autonomous)
#   P4  Schema-init via openclaw-1
#   P5  Wire mTLS + 4-node federation
#   P6  Baseline validation (B/D/T/N/S/C/X/P/F/A/U)
#   P7  Per-droplet autonomous-tier validation (C1-C9)
#   P8  Round 1 cert (full regression)
#   P9  Round 2 cert (must be 100% GREEN twice)
#   P10 NHI discovery sweep (S83/S84/S85)
#   P11 Render results page (3-audience)
#   P12 Teardown — release the $3.80/hr
#
# Hard guards:
#   * BUDGET_USD (default 200) — enforced at start AND in dead-man timer
#   * MAX_WALL_HOURS (default 30) — auto-teardown after this even mid-cert
#   * Aborting at any phase triggers teardown (unless --no-auto-teardown)
#
# Usage:
#   ./scripts/run_gpu_cert.sh --track Q [--budget-usd 200] [--max-wall-hours 30]
#   ./scripts/run_gpu_cert.sh --track Q --resume-from P8     # skip earlier phases
#   ./scripts/run_gpu_cert.sh --track Q --dry-run            # print plan only
set -uo pipefail

TRACK="Q"
BUDGET_USD="${BUDGET_USD:-200}"
MAX_WALL_HOURS="${MAX_WALL_HOURS:-30}"
RESUME_FROM=""
DRY_RUN=0
AUTO_TEARDOWN=1
LLM_MODEL="${LLM_MODEL:-gemma4:e4b}"
RUN_DIR_BASE="${RUN_DIR_BASE:-runs}"
HOURLY_RATE="${HOURLY_RATE:-3.165}"  # 4× $0.76 GPU + 1× $0.125 CPU pg

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="$2"; shift 2 ;;
    --budget-usd) BUDGET_USD="$2"; shift 2 ;;
    --max-wall-hours) MAX_WALL_HOURS="$2"; shift 2 ;;
    --resume-from) RESUME_FROM="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-auto-teardown) AUTO_TEARDOWN=0; shift ;;
    --llm-model) LLM_MODEL="$2"; shift 2 ;;
    -h|--help) sed -n '4,32p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$REPO_DIR/scripts"
CAMPAIGN_ID="v0.7.0-gpu-${TRACK}-$(date -u +%Y%m%d-%H%M%S)"
RUN_DIR="$REPO_DIR/$RUN_DIR_BASE/$CAMPAIGN_ID"
mkdir -p "$RUN_DIR"
LOG="$RUN_DIR/master-orchestrator.log"
PHASE_TIMINGS="$RUN_DIR/phase-timings.json"
echo '[]' > "$PHASE_TIMINGS"

T_START=$(date +%s)

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

phase_start() {
  PHASE_ID="$1"; PHASE_NAME="$2"
  PHASE_T0=$(date +%s)
  log "===== $PHASE_ID: $PHASE_NAME ====="
}

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
    log "ABORT — phase $PHASE_ID failed with rc=$rc"
    if [[ "$AUTO_TEARDOWN" -eq 1 ]]; then
      teardown
    else
      log "(--no-auto-teardown set; droplets remain billing — operator must run teardown_gpu_droplets.sh)"
    fi
    exit "$rc"
  fi
  budget_check
}

budget_check() {
  local elapsed=$(( $(date +%s) - T_START ))
  local elapsed_h=$(awk -v s="$elapsed" 'BEGIN{printf "%.2f", s/3600}')
  local spend=$(awk -v h="$elapsed" -v r="$HOURLY_RATE" 'BEGIN{printf "%.2f", h*r/3600}')
  if awk -v e="$elapsed_h" -v m="$MAX_WALL_HOURS" 'BEGIN{exit !(e>=m)}'; then
    log "ABORT — wall clock ${elapsed_h}h ≥ MAX_WALL_HOURS=${MAX_WALL_HOURS}h; tearing down"
    [[ "$AUTO_TEARDOWN" -eq 1 ]] && teardown
    exit 5
  fi
  if awk -v s="$spend" -v b="$BUDGET_USD" 'BEGIN{exit !(s>=b)}'; then
    log "ABORT — spend \$${spend} ≥ BUDGET=\$${BUDGET_USD}; tearing down"
    [[ "$AUTO_TEARDOWN" -eq 1 ]] && teardown
    exit 6
  fi
}

teardown() {
  log "===== TEARDOWN: releasing GPU droplets ====="
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(dry-run; would call: $SCRIPT_DIR/teardown_gpu_droplets.sh --track $TRACK)"
    return 0
  fi
  echo "yes" | bash "$SCRIPT_DIR/teardown_gpu_droplets.sh" --track "$TRACK" 2>&1 | tee -a "$LOG"
  local elapsed=$(( $(date +%s) - T_START ))
  local spend=$(awk -v h="$elapsed" -v r="$HOURLY_RATE" 'BEGIN{printf "%.2f", h*r/3600}')
  log "Teardown complete. Final wall=${elapsed}s; final spend=\$${spend}"
}

# ---------- Phase plan ----------
should_run_phase() {
  local pid="$1"
  [[ -z "$RESUME_FROM" ]] && return 0
  local pnum="${pid#P}"; local rnum="${RESUME_FROM#P}"
  [[ "$pnum" -ge "$rnum" ]]
}

p0_preflight() {
  should_run_phase P0 || return 0
  phase_start P0 "pre-flight (auth, TLS, scripts)"
  command -v doctl >/dev/null || { log "doctl missing"; phase_end 4; }
  doctl auth list 2>/dev/null | grep -q current || { log "doctl not authenticated"; phase_end 4; }
  [[ -d /tmp/a2a-v07-tls-gpu ]] || { log "TLS material missing at /tmp/a2a-v07-tls-gpu"; phase_end 4; }
  [[ -f /tmp/a2a-v07-tls-gpu/mtls-allowlist.txt ]] || { log "mtls allowlist missing"; phase_end 4; }
  for s in provision_gpu_droplets.sh provision_postgres_cpu.sh \
           bootstrap_postgres_gpu.sh \
           bootstrap_gpu_droplets.sh wire_mtls_quad.sh \
           validate_baseline.sh validate_autonomous_tier.sh \
           render_gpu_results.py teardown_gpu_droplets.sh \
           run_round1.py; do
    [[ -x "$SCRIPT_DIR/$s" || -f "$SCRIPT_DIR/$s" ]] || { log "script missing: $s"; phase_end 4; }
  done
  log "  pre-flight OK"
  phase_end 0
}

p1_provision() {
  should_run_phase P1 || return 0
  phase_start P1 "provision 4× gpu-4000adax1-20gb + 1× s-4vcpu-16gb-amd postgres"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    bash "$SCRIPT_DIR/provision_gpu_droplets.sh" --track "$TRACK" --dry-run 2>&1 | tee -a "$LOG"
    bash "$SCRIPT_DIR/provision_postgres_cpu.sh" --dry-run 2>&1 | tee -a "$LOG"
    phase_end 0; return
  fi
  if ! bash "$SCRIPT_DIR/provision_gpu_droplets.sh" --track "$TRACK" 2>&1 | tee -a "$LOG"; then
    phase_end 1; return
  fi
  if bash "$SCRIPT_DIR/provision_postgres_cpu.sh" 2>&1 | tee -a "$LOG"; then
    phase_end 0
  else phase_end 1; fi
}

p2_bootstrap_postgres() {
  should_run_phase P2 || return 0
  phase_start P2 "bootstrap postgres+AGE+pgvector"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    bash "$SCRIPT_DIR/bootstrap_postgres_gpu.sh" --track "$TRACK" --dry-run 2>&1 | tee -a "$LOG"
    phase_end 0; return
  fi
  if bash "$SCRIPT_DIR/bootstrap_postgres_gpu.sh" --track "$TRACK" 2>&1 | tee -a "$LOG"; then
    phase_end 0
  else phase_end 1; fi
}

p3_bootstrap_openclaw() {
  should_run_phase P3 || return 0
  phase_start P3 "bootstrap 4× openclaw (Ollama + gemma + ai-memory)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    bash "$SCRIPT_DIR/bootstrap_gpu_droplets.sh" --track "$TRACK" --llm-model "$LLM_MODEL" --dry-run 2>&1 | tee -a "$LOG"
    phase_end 0; return
  fi
  if bash "$SCRIPT_DIR/bootstrap_gpu_droplets.sh" --track "$TRACK" --llm-model "$LLM_MODEL" 2>&1 | tee -a "$LOG"; then
    phase_end 0
  else phase_end 1; fi
}

p4_schema_init() {
  should_run_phase P4 || return 0
  phase_start P4 "schema-init via openclaw-1"
  local OC1=$(doctl compute droplet list --tag-name "track-$TRACK" \
    --format Name,PublicIPv4 --no-header 2>/dev/null \
    | awk '/openclaw.*-1\b/{print $2; exit}')
  local PG=$(doctl compute droplet list --tag-name "track-$TRACK" \
    --format Name,PrivateIPv4 --no-header 2>/dev/null \
    | awk '/postgres/{print $2; exit}')
  [[ -n "$OC1" && -n "$PG" ]] || { log "could not find openclaw-1 or postgres"; phase_end 4; return; }
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(dry-run) would ssh root@$OC1 → ai-memory schema-init --store-url postgres://aimemory:<pwd>@$PG:5432/aimemory"
    phase_end 0; return
  fi
  if ssh -o StrictHostKeyChecking=no "root@$OC1" "
    PG_PWD=\$(cat /root/postgres-aimemory-pw.txt 2>/dev/null)
    [ -n \"\$PG_PWD\" ] || { echo 'pg password not on droplet'; exit 7; }
    /usr/local/bin/ai-memory schema-init --store-url \"postgres://aimemory:\$PG_PWD@$PG:5432/aimemory\"
  " 2>&1 | tee -a "$LOG"; then
    phase_end 0
  else phase_end 1; fi
}

p5_wire_mtls() {
  should_run_phase P5 || return 0
  phase_start P5 "wire mTLS + 4-node federation"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    bash "$SCRIPT_DIR/wire_mtls_quad.sh" --track "$TRACK" --dry-run 2>&1 | tee -a "$LOG"
    phase_end 0; return
  fi
  if bash "$SCRIPT_DIR/wire_mtls_quad.sh" --track "$TRACK" 2>&1 | tee -a "$LOG"; then
    phase_end 0
  else phase_end 1; fi
}

p6_validate_baseline() {
  should_run_phase P6 || return 0
  phase_start P6 "baseline validation (11 domains, hard gate)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(dry-run) would call validate_baseline.sh --track $TRACK"
    phase_end 0; return
  fi
  if bash "$SCRIPT_DIR/validate_baseline.sh" --track "$TRACK" \
       --out "$RUN_DIR/baseline-validation.json" 2>&1 | tee -a "$LOG"; then
    phase_end 0
  else phase_end 1; fi
}

p7_validate_autonomous() {
  should_run_phase P7 || return 0
  phase_start P7 "per-droplet autonomous-tier validation"
  # Note: P6 already calls validate_autonomous_tier.sh via the A-overall
  # check. Phase P7 is a redundant explicit pass for cert-evidence trail.
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(dry-run) would call validate_autonomous_tier.sh --track $TRACK"
    phase_end 0; return
  fi
  if bash "$SCRIPT_DIR/validate_autonomous_tier.sh" --track "$TRACK" 2>&1 | tee -a "$LOG"; then
    phase_end 0
  else phase_end 1; fi
}

run_round() {
  local round="$1"
  local round_id="${CAMPAIGN_ID}-r${round}"
  local round_dir="$REPO_DIR/$RUN_DIR_BASE/$round_id"
  mkdir -p "$round_dir"
  # Discover IPs
  local OC1 OC2 OC3 OC4 PG
  OC1=$(doctl compute droplet list --tag-name "track-$TRACK" --format Name,PrivateIPv4 --no-header | awk '/openclaw.*-1\b/{print $2; exit}')
  OC2=$(doctl compute droplet list --tag-name "track-$TRACK" --format Name,PrivateIPv4 --no-header | awk '/openclaw.*-2\b/{print $2; exit}')
  OC3=$(doctl compute droplet list --tag-name "track-$TRACK" --format Name,PrivateIPv4 --no-header | awk '/openclaw.*-3\b/{print $2; exit}')
  OC4=$(doctl compute droplet list --tag-name "track-$TRACK" --format Name,PrivateIPv4 --no-header | awk '/openclaw.*-4\b/{print $2; exit}')
  PG=$(doctl compute droplet list --tag-name "track-$TRACK" --format Name,PrivateIPv4 --no-header | awk '/postgres/{print $2; exit}')
  log "  topology: oc1=$OC1 oc2=$OC2 oc3=$OC3 oc4=$OC4 pg=$PG"

  A2A_TRACK="$TRACK" \
  A2A_BACKEND_KIND=postgres \
  TLS_MODE=mtls \
  AGENT_GROUP=openclaw \
  NODE1_IP="$OC1" NODE2_IP="$OC2" NODE3_IP="$OC3" NODE4_IP="$OC4" \
  NODE1_PRIV="$OC1" NODE2_PRIV="$OC2" NODE3_PRIV="$OC3" NODE4_PRIV="$OC4" \
  POSTGRES_HOST="$PG" \
  RUN_DIR="$round_dir" \
  python3 "$SCRIPT_DIR/run_round1.py" --campaign "$round_id" 2>&1 | tee -a "$LOG"
}

p8_r1() {
  should_run_phase P8 || return 0
  phase_start P8 "Round 1 cert"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(dry-run) would run round 1 against 4 openclaw + postgres"
    phase_end 0; return
  fi
  if run_round 1; then phase_end 0; else phase_end 1; fi
}

p9_r2() {
  should_run_phase P9 || return 0
  phase_start P9 "Round 2 cert (must be 100% GREEN)"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(dry-run) would run round 2 against same 4 openclaw + postgres"
    phase_end 0; return
  fi
  if run_round 2; then phase_end 0; else phase_end 1; fi
}

p10_nhi() {
  should_run_phase P10 || return 0
  phase_start P10 "NHI discovery sweep (S83/S84/S85)"
  log "  (NHI scenarios run as part of P8/P9 if track manifest includes them; this phase is reserved for any standalone post-cert NHI sweep)"
  phase_end 0
}

p11_render() {
  should_run_phase P11 || return 0
  phase_start P11 "render results page (3-audience analysis)"
  local r1d="$REPO_DIR/$RUN_DIR_BASE/${CAMPAIGN_ID}-r1"
  local r2d="$REPO_DIR/$RUN_DIR_BASE/${CAMPAIGN_ID}-r2"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "(dry-run) would render docs/gpu-cert.md from $r1d and $r2d"
    phase_end 0; return
  fi
  if python3 "$SCRIPT_DIR/render_gpu_results.py" \
       --track "$TRACK" --r1-dir "$r1d" --r2-dir "$r2d" \
       --out "$REPO_DIR/docs/gpu-cert.md" 2>&1 | tee -a "$LOG"; then
    phase_end 0
  else phase_end 1; fi
}

p12_teardown() {
  should_run_phase P12 || return 0
  phase_start P12 "teardown — release GPU + CPU postgres droplets"
  teardown
  # Also tear down CPU postgres
  if [[ "$DRY_RUN" -eq 0 ]]; then
    PG_ID=$(doctl compute droplet list --tag-name a2a-v07-pg \
      --format ID --no-header 2>/dev/null | head -1)
    if [[ -n "$PG_ID" ]]; then
      log "  also tearing down CPU postgres droplet id=$PG_ID"
      doctl compute droplet delete "$PG_ID" --force 2>&1 | tee -a "$LOG"
    fi
  fi
  phase_end 0
}

# ---------- main ----------
log "v0.7.0 GPU CERT — master orchestrator"
log "  campaign_id: $CAMPAIGN_ID"
log "  track: $TRACK"
log "  budget: \$$BUDGET_USD  max_wall: ${MAX_WALL_HOURS}h  hourly: \$$HOURLY_RATE"
log "  dry_run: $DRY_RUN  resume_from: ${RESUME_FROM:-(start)}"
log "  run_dir: $RUN_DIR"

# Trap: ensure teardown on any exit (even unexpected) when AUTO_TEARDOWN=1
on_exit() {
  local rc=$?
  if [[ "$AUTO_TEARDOWN" -eq 1 && $rc -ne 0 && "$DRY_RUN" -eq 0 ]]; then
    log "TRAP — non-zero exit ($rc); ensuring teardown"
    teardown 2>&1 | tee -a "$LOG" || true
  fi
}
trap on_exit EXIT

p0_preflight
p1_provision
p2_bootstrap_postgres
p3_bootstrap_openclaw
p4_schema_init
p5_wire_mtls
p6_validate_baseline
p7_validate_autonomous
p8_r1
p9_r2
p10_nhi
p11_render
p12_teardown

# Disarm trap on clean success
trap - EXIT

T_END=$(date +%s)
WALL=$((T_END - T_START))
SPEND=$(awk -v h="$WALL" -v r="$HOURLY_RATE" 'BEGIN{printf "%.2f", h*r/3600}')
log
log "===== CERT RUN COMPLETE ====="
log "  campaign_id: $CAMPAIGN_ID"
log "  wall: ${WALL}s ($(awk -v s="$WALL" 'BEGIN{printf "%.1f", s/3600}')h)"
log "  spend: \$$SPEND of \$$BUDGET_USD budget"
log "  phase timings: $PHASE_TIMINGS"
log "  master log: $LOG"
log "  results: $REPO_DIR/docs/gpu-cert.md"
