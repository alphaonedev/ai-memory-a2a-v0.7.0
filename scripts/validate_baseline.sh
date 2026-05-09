#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# v0.7.0 GPU cert — COMPREHENSIVE BASELINE VALIDATION.
#
# Runs BEFORE the cert sweep starts. Hard-fails if ANY baseline check
# fails — operator must fix before testing can begin so that infra-level
# variance doesn't disrupt test results.
#
# Validation domains (each can fail independently):
#
#   B  Cloud / DO baseline      — auth, VPC, firewall, SSH, region
#   D  Droplet hardware         — GPU/driver/CUDA, RAM, disk, CPU, kernel
#   T  Time / clock             — NTP, skew across droplets <1s
#   N  Network                  — peer reachability matrix, MTU, DNS
#   S  Software versions        — ai-memory, ollama, postgres, rust match
#   C  Configuration            — config.toml + systemd unit fingerprint
#                                 across all droplets is identical
#   X  TLS / mTLS                — cert expiry, allowlist, CA chain
#   P  Postgres + AGE           — extension, schema v28, clean state
#   F  Federation               — full reachability matrix (4×4 + hermes)
#   A  Autonomous tier per-node — calls validate_autonomous_tier.sh
#   U  Audit chain init clean   — no orphan rows from prior runs
#
# Usage:
#   ./scripts/validate_baseline.sh --track Q --out runs/<campaign-id>/baseline-validation.json
#
# Exit codes:
#   0  all baselines pass — cert run authorized
#   1  validation report fail — see report
#   2  bad invocation
#   3  prerequisite missing (no droplets, no doctl, etc.)
set -uo pipefail

TRACK=""
OUT_PATH=""
HERMES_PRIV="${HERMES_PRIV:-10.20.0.3}"
PG_PRIV="${PG_PRIV:-10.20.0.4}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)
TIMEOUT_BIN="${TIMEOUT_BIN:-timeout}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="$2"; shift 2 ;;
    --out) OUT_PATH="$2"; shift 2 ;;
    --hermes-priv) HERMES_PRIV="$2"; shift 2 ;;
    --pg-priv) PG_PRIV="$2"; shift 2 ;;
    -h|--help) sed -n '4,40p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$TRACK" ]] || { echo "must pass --track Q" >&2; exit 2; }
OUT_PATH="${OUT_PATH:-/tmp/v07-baseline-validation-$(date +%Y%m%d-%H%M%S).json}"
mkdir -p "$(dirname "$OUT_PATH")"

REPORT="$OUT_PATH"
TMP_REPORT="${REPORT}.tmp"
echo '{"checks": []}' > "$TMP_REPORT"

# Append a check result. emit_check <id> <pass|fail> <summary> <evidence-json>
emit_check() {
  local id="$1" status="$2" summary="$3" evidence="${4:-{}}"
  jq --arg id "$id" --arg s "$status" --arg sum "$summary" --argjson ev "$evidence" \
    '.checks += [{id:$id, status:$s, summary:$sum, evidence:$ev}]' \
    "$TMP_REPORT" > "${TMP_REPORT}.next" && mv "${TMP_REPORT}.next" "$TMP_REPORT"
  if [ "$status" = "pass" ]; then echo "  [$id] OK   $summary"
  else echo "  [$id] FAIL $summary" >&2
  fi
}

FAIL=0
fail_track() { FAIL=1; }

# --------------------------------------------------------------- B (cloud)
echo "=== B: cloud / DO baseline ==="
B1_OK=0
if doctl auth list 2>/dev/null | grep -q current; then
  CTX=$(doctl auth list 2>/dev/null | awk '/current/{print $1}')
  emit_check B1 pass "doctl authenticated (context=$CTX)" "{\"context\":\"$CTX\"}"
  B1_OK=1
else
  emit_check B1 fail "doctl not authenticated"
  fail_track
fi

if [ $B1_OK -eq 1 ]; then
  mapfile -t DROPLETS < <(doctl compute droplet list --tag-name "track-$TRACK" \
    --format Name,PublicIPv4,PrivateIPv4 --no-header 2>/dev/null)
else
  DROPLETS=()
fi

if [ ${#DROPLETS[@]} -gt 0 ]; then
  emit_check B2 pass "${#DROPLETS[@]} droplets tagged track-$TRACK" \
    "$(printf '%s\n' "${DROPLETS[@]}" | jq -R . | jq -s .)"
else
  emit_check B2 fail "no droplets tagged track-$TRACK — provision first"
  fail_track
fi

# Verify all droplets are in same VPC + region
if [ ${#DROPLETS[@]} -ge 2 ]; then
  REGIONS=$(doctl compute droplet list --tag-name "track-$TRACK" \
    --format Region --no-header 2>/dev/null | sort -u)
  if [ "$(echo "$REGIONS" | wc -l)" -eq 1 ]; then
    emit_check B3 pass "all droplets in region $REGIONS"
  else
    emit_check B3 fail "droplets span multiple regions: $REGIONS"
    fail_track
  fi
else
  emit_check B3 pass "single-droplet track; region check skipped"
fi

# --------------------------------------------------------------- D, T, S, C, X (per-droplet)
DROPLET_REPORTS=()
DROPLET_FP_CONFIG=()
DROPLET_FP_UNIT=()
DROPLET_VER_AIMEM=()
DROPLET_VER_OLLAMA=()
DROPLET_VER_DRIVER=()

for entry in "${DROPLETS[@]}"; do
  read -r name pub_ip priv_ip <<<"$entry"
  echo
  echo "=== droplet: $name ($pub_ip / $priv_ip) ==="

  REMOTE_OUT=$(ssh "${SSH_OPTS[@]}" "root@$pub_ip" \
    bash -se -- "$priv_ip" "$PG_PRIV" "$HERMES_PRIV" <<'REMOTE' 2>/dev/null
set -uo pipefail
PRIV_IP="$1"; PG_PRIV="$2"; HM_PRIV="$3"

# Capture single JSON object, every field a check result.
NVIDIA_DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
NVIDIA_CUDA=$(nvidia-smi --query-gpu=cuda_version --format=csv,noheader 2>/dev/null | head -1)
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
GPU_VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
GPU_FREE_MB=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1)

CPU_CORES=$(nproc 2>/dev/null)
RAM_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null)
DISK_AVAIL_GB=$(df -BG --output=avail / 2>/dev/null | tail -1 | tr -dc '0-9')
KERNEL=$(uname -r)
UPTIME_S=$(awk '{print int($1)}' /proc/uptime 2>/dev/null)

# Time / NTP
DATE_UTC=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
NTP_OK="false"
if timedatectl 2>/dev/null | grep -q "synchronized: yes"; then NTP_OK="true"; fi

# Software versions
AIMEM_VER=$(/usr/local/bin/ai-memory --version 2>/dev/null | awk '{print $NF}')
AIMEM_SCHEMA=$(curl -s --max-time 5 "http://$PRIV_IP:19077/api/v1/capabilities" \
  | jq -r '.schema_version // ""' 2>/dev/null)
OLLAMA_VER=$(ollama --version 2>/dev/null | awk '{print $NF}')
RUSTC_VER=$(rustc --version 2>/dev/null | awk '{print $2}')
PG_CLIENT_VER=$(psql --version 2>/dev/null | awk '{print $3}')
JQ_VER=$(jq --version 2>/dev/null)

# Configuration fingerprints
FP_CFG=$(sha256sum /etc/ai-memory/config.toml 2>/dev/null | awk '{print $1}')
FP_UNIT=$(sha256sum /etc/systemd/system/ai-memory.service 2>/dev/null | awk '{print $1}')

# Daemon state
SVC_ACTIVE=$(systemctl is-active ai-memory 2>/dev/null || echo "missing")
DAEMON_KEY_OK=$([ -f /root/.config/ai-memory/keys/daemon.priv ] && echo "true" || echo "false")
AUDIT_DIR_OK=$([ -d /var/log/ai-memory/audit ] && [ -w /var/log/ai-memory/audit ] && echo "true" || echo "false")
AUDIT_ROWS=$(find /var/log/ai-memory/audit -type f -name '*.log' 2>/dev/null | xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}')
AUDIT_ROWS=${AUDIT_ROWS:-0}

# Postgres reachability
PG_OK="false"; PG_AGE="false"; PG_VER=""; PG_SCHEMA_VER=""
if [ -f /root/postgres-aimemory-pw.txt ]; then
  PG_VER=$(PGPASSWORD="$(cat /root/postgres-aimemory-pw.txt)" \
    psql -h "$PG_PRIV" -U aimemory -d aimemory -tAc "SHOW server_version;" 2>/dev/null | tr -d ' ')
  if [ -n "$PG_VER" ]; then PG_OK="true"; fi
  if [ "$PG_OK" = "true" ]; then
    PG_AGE_RAW=$(PGPASSWORD="$(cat /root/postgres-aimemory-pw.txt)" \
      psql -h "$PG_PRIV" -U aimemory -d aimemory -tAc \
      "SELECT extversion FROM pg_extension WHERE extname='age';" 2>/dev/null | tr -d ' ')
    [ -n "$PG_AGE_RAW" ] && PG_AGE="true"
    PG_SCHEMA_VER=$(PGPASSWORD="$(cat /root/postgres-aimemory-pw.txt)" \
      psql -h "$PG_PRIV" -U aimemory -d aimemory -tAc \
      "SELECT MAX(version) FROM schema_migrations;" 2>/dev/null | tr -d ' ')
  fi
fi

# Federation reachability matrix (PRIV_IP→hermes, PRIV_IP→postgres)
HM_REACH="false"
if curl -sf --max-time 5 "http://$HM_PRIV:19077/api/v1/capabilities" >/dev/null 2>&1; then
  HM_REACH="true"
fi

# TLS material
TLS_DIR_PRESENT="false"; TLS_FILES=""
if [ -d /etc/ai-memory-a2a/tls ]; then
  TLS_DIR_PRESENT="true"
  TLS_FILES=$(ls /etc/ai-memory-a2a/tls 2>/dev/null | tr '\n' ',' | sed 's/,$//')
fi

# Emit single JSON
jq -n \
  --arg gpu "$GPU_NAME" --arg drv "$NVIDIA_DRV" --arg cuda "$NVIDIA_CUDA" \
  --arg vram_mb "$GPU_VRAM_MB" --arg gpu_free_mb "$GPU_FREE_MB" \
  --arg cpu "$CPU_CORES" --arg ram_kb "$RAM_KB" --arg disk_gb "$DISK_AVAIL_GB" \
  --arg kernel "$KERNEL" --arg uptime "$UPTIME_S" \
  --arg date "$DATE_UTC" --arg ntp "$NTP_OK" \
  --arg aimem_ver "$AIMEM_VER" --arg aimem_schema "$AIMEM_SCHEMA" \
  --arg ollama "$OLLAMA_VER" --arg rustc "$RUSTC_VER" \
  --arg pgcli "$PG_CLIENT_VER" --arg jq_ver "$JQ_VER" \
  --arg fp_cfg "$FP_CFG" --arg fp_unit "$FP_UNIT" \
  --arg svc "$SVC_ACTIVE" --arg keys "$DAEMON_KEY_OK" \
  --arg audit_dir "$AUDIT_DIR_OK" --arg audit_rows "$AUDIT_ROWS" \
  --arg pg_ok "$PG_OK" --arg pg_age "$PG_AGE" --arg pg_ver "$PG_VER" \
  --arg pg_schema "$PG_SCHEMA_VER" --arg hm_reach "$HM_REACH" \
  --arg tls_dir "$TLS_DIR_PRESENT" --arg tls_files "$TLS_FILES" \
  '{gpu_name:$gpu,driver:$drv,cuda:$cuda,vram_mb:($vram_mb|tonumber? // 0),
    gpu_free_mb:($gpu_free_mb|tonumber? // 0),
    cpu_cores:($cpu|tonumber? // 0),ram_kb:($ram_kb|tonumber? // 0),
    disk_avail_gb:($disk_gb|tonumber? // 0),kernel:$kernel,
    uptime_seconds:($uptime|tonumber? // 0),
    date_utc:$date,ntp_synchronized:($ntp=="true"),
    aimem_version:$aimem_ver,aimem_schema:($aimem_schema|tonumber? // 0),
    ollama_version:$ollama,rustc_version:$rustc,
    pg_client_version:$pgcli,jq_version:$jq_ver,
    fp_config_toml:$fp_cfg,fp_systemd_unit:$fp_unit,
    daemon_active:($svc=="active"),daemon_keypair:($keys=="true"),
    audit_dir_writable:($audit_dir=="true"),audit_existing_rows:($audit_rows|tonumber? // 0),
    postgres_reachable:($pg_ok=="true"),age_extension:($pg_age=="true"),
    postgres_version:$pg_ver,postgres_schema_version:($pg_schema|tonumber? // 0),
    hermes_reachable:($hm_reach=="true"),
    tls_dir_present:($tls_dir=="true"),tls_files:$tls_files}'
REMOTE
)

  if [ -z "$REMOTE_OUT" ] || ! echo "$REMOTE_OUT" | jq . >/dev/null 2>&1; then
    emit_check "D-$name" fail "remote probe returned no JSON"
    fail_track
    continue
  fi

  DROPLET_REPORTS+=("$REMOTE_OUT")

  # Per-droplet checks — each surfaces individually for visibility
  GPU=$(echo "$REMOTE_OUT" | jq -r '.gpu_name')
  DRV=$(echo "$REMOTE_OUT" | jq -r '.driver')
  CUDA=$(echo "$REMOTE_OUT" | jq -r '.cuda')
  VRAM=$(echo "$REMOTE_OUT" | jq -r '.vram_mb')
  GPU_FREE=$(echo "$REMOTE_OUT" | jq -r '.gpu_free_mb')
  CPU=$(echo "$REMOTE_OUT" | jq -r '.cpu_cores')
  RAM_KB=$(echo "$REMOTE_OUT" | jq -r '.ram_kb')
  RAM_GB=$((RAM_KB / 1024 / 1024))
  DISK=$(echo "$REMOTE_OUT" | jq -r '.disk_avail_gb')
  NTP=$(echo "$REMOTE_OUT" | jq -r '.ntp_synchronized')
  AIMEM_VER=$(echo "$REMOTE_OUT" | jq -r '.aimem_version')
  AIMEM_SCHEMA=$(echo "$REMOTE_OUT" | jq -r '.aimem_schema')
  OLLAMA_VER=$(echo "$REMOTE_OUT" | jq -r '.ollama_version')
  FP_CFG=$(echo "$REMOTE_OUT" | jq -r '.fp_config_toml')
  FP_UNIT=$(echo "$REMOTE_OUT" | jq -r '.fp_systemd_unit')
  DAEMON_OK=$(echo "$REMOTE_OUT" | jq -r '.daemon_active')
  AUDIT_OK=$(echo "$REMOTE_OUT" | jq -r '.audit_dir_writable')
  AUDIT_ROWS=$(echo "$REMOTE_OUT" | jq -r '.audit_existing_rows')
  PG_OK=$(echo "$REMOTE_OUT" | jq -r '.postgres_reachable')
  PG_AGE=$(echo "$REMOTE_OUT" | jq -r '.age_extension')
  PG_SCHEMA=$(echo "$REMOTE_OUT" | jq -r '.postgres_schema_version')
  HM_OK=$(echo "$REMOTE_OUT" | jq -r '.hermes_reachable')
  TLS_OK=$(echo "$REMOTE_OUT" | jq -r '.tls_dir_present')

  DROPLET_FP_CONFIG+=("$FP_CFG")
  DROPLET_FP_UNIT+=("$FP_UNIT")
  DROPLET_VER_AIMEM+=("$AIMEM_VER")
  DROPLET_VER_OLLAMA+=("$OLLAMA_VER")
  DROPLET_VER_DRIVER+=("$DRV")

  # D — droplet hardware
  [ "$VRAM" -ge 18000 ] \
    && emit_check "D1-$name" pass "GPU $GPU vram=${VRAM}MiB driver=$DRV cuda=$CUDA" \
       "$(jq -nc --arg gpu "$GPU" --arg drv "$DRV" --arg cuda "$CUDA" --arg vram "$VRAM" '{gpu:$gpu,driver:$drv,cuda:$cuda,vram_mb:($vram|tonumber)}')" \
    || { emit_check "D1-$name" fail "VRAM too low (${VRAM}MiB < 18000)"; fail_track; }
  [ "$CPU" -ge 8 ] \
    && emit_check "D2-$name" pass "CPU cores=$CPU" \
    || { emit_check "D2-$name" fail "CPU cores=$CPU (< 8)"; fail_track; }
  [ "$RAM_GB" -ge 30 ] \
    && emit_check "D3-$name" pass "RAM ${RAM_GB}GB (>=30 floor for 32GB SKU)" \
    || { emit_check "D3-$name" fail "RAM ${RAM_GB}GB (< 30GB)"; fail_track; }
  [ "$DISK" -ge 100 ] \
    && emit_check "D4-$name" pass "Disk avail ${DISK}GB" \
    || { emit_check "D4-$name" fail "Disk avail ${DISK}GB (< 100GB)"; fail_track; }
  [ "$GPU_FREE" -ge 4000 ] \
    && emit_check "D5-$name" pass "GPU free ${GPU_FREE}MiB" \
    || { emit_check "D5-$name" fail "GPU free ${GPU_FREE}MiB (< 4GB headroom)"; fail_track; }

  # T — time / NTP
  [ "$NTP" = "true" ] \
    && emit_check "T1-$name" pass "NTP synchronized" \
    || { emit_check "T1-$name" fail "NTP NOT synchronized"; fail_track; }

  # S — software baseline
  [ -n "$AIMEM_VER" ] \
    && emit_check "S1-$name" pass "ai-memory $AIMEM_VER schema=$AIMEM_SCHEMA" \
    || { emit_check "S1-$name" fail "ai-memory binary missing/broken"; fail_track; }
  [ -n "$OLLAMA_VER" ] \
    && emit_check "S2-$name" pass "ollama $OLLAMA_VER" \
    || { emit_check "S2-$name" fail "ollama not installed"; fail_track; }

  # C — config + systemd unit fingerprint (per-droplet just records; cross-droplet diff below)
  [ -n "$FP_CFG" ] \
    && emit_check "C1-$name" pass "config.toml present (sha256=${FP_CFG:0:12}…)" \
    || { emit_check "C1-$name" fail "config.toml missing"; fail_track; }
  [ -n "$FP_UNIT" ] \
    && emit_check "C2-$name" pass "systemd unit present (sha256=${FP_UNIT:0:12}…)" \
    || { emit_check "C2-$name" fail "systemd unit missing"; fail_track; }
  [ "$DAEMON_OK" = "true" ] \
    && emit_check "C3-$name" pass "ai-memory daemon active" \
    || { emit_check "C3-$name" fail "ai-memory daemon NOT active"; fail_track; }

  # X — TLS material
  [ "$TLS_OK" = "true" ] \
    && emit_check "X1-$name" pass "TLS dir present" \
    || { emit_check "X1-$name" fail "TLS material missing — run cert distribution first"; fail_track; }

  # P — postgres+AGE
  [ "$PG_OK" = "true" ] \
    && emit_check "P1-$name" pass "postgres reachable" \
    || { emit_check "P1-$name" fail "postgres NOT reachable from this droplet"; fail_track; }
  [ "$PG_AGE" = "true" ] \
    && emit_check "P2-$name" pass "AGE extension present" \
    || { emit_check "P2-$name" fail "AGE extension NOT present"; fail_track; }
  [ "$PG_SCHEMA" -ge 28 ] \
    && emit_check "P3-$name" pass "postgres schema_version=$PG_SCHEMA (>= 28)" \
    || { emit_check "P3-$name" fail "postgres schema_version=$PG_SCHEMA (need >= 28)"; fail_track; }

  # F — federation
  [ "$HM_OK" = "true" ] \
    && emit_check "F1-$name" pass "hermes peer reachable" \
    || emit_check "F1-$name" pass "hermes peer not reachable (ok if openclaw-only mode)" \
       "$(jq -nc '{ok_when_openclaw_only:true}')"

  # U — audit clean
  [ "$AUDIT_OK" = "true" ] \
    && emit_check "U1-$name" pass "audit dir writable" \
    || { emit_check "U1-$name" fail "audit dir not writable"; fail_track; }
  [ "$AUDIT_ROWS" -le 5 ] \
    && emit_check "U2-$name" pass "audit chain clean (rows=$AUDIT_ROWS)" \
    || emit_check "U2-$name" pass "audit has prior rows ($AUDIT_ROWS) — captured for delta" \
       "$(jq -nc --arg n "$AUDIT_ROWS" '{prior_rows:($n|tonumber)}')"
done

# --------------------------------------------------------------- Cross-droplet consistency
echo
echo "=== cross-droplet consistency ==="
fp_uniq() { printf '%s\n' "$@" | sort -u | wc -l | tr -d ' '; }

if [ ${#DROPLET_FP_CONFIG[@]} -ge 2 ]; then
  N=$(fp_uniq "${DROPLET_FP_CONFIG[@]}")
  if [ "$N" = "1" ]; then
    emit_check C-fp-config pass "config.toml fingerprint identical across all ${#DROPLET_FP_CONFIG[@]} droplets" \
      "$(jq -nc --arg fp "${DROPLET_FP_CONFIG[0]}" '{sha256:$fp}')"
  else
    emit_check C-fp-config fail "config.toml fingerprint MISMATCHED across droplets ($N distinct)" \
      "$(printf '%s\n' "${DROPLET_FP_CONFIG[@]}" | jq -R . | jq -s '{fingerprints:.}')"
    fail_track
  fi
  N=$(fp_uniq "${DROPLET_FP_UNIT[@]}")
  if [ "$N" = "1" ]; then
    emit_check C-fp-unit pass "systemd unit fingerprint identical"
  else
    emit_check C-fp-unit fail "systemd unit fingerprint MISMATCHED ($N distinct)" \
      "$(printf '%s\n' "${DROPLET_FP_UNIT[@]}" | jq -R . | jq -s '{fingerprints:.}')"
    fail_track
  fi
  N=$(fp_uniq "${DROPLET_VER_AIMEM[@]}")
  if [ "$N" = "1" ]; then
    emit_check S-ver-aimem pass "ai-memory version identical (${DROPLET_VER_AIMEM[0]})"
  else
    emit_check S-ver-aimem fail "ai-memory version MISMATCHED ($N distinct)" \
      "$(printf '%s\n' "${DROPLET_VER_AIMEM[@]}" | jq -R . | jq -s '{versions:.}')"
    fail_track
  fi
  N=$(fp_uniq "${DROPLET_VER_OLLAMA[@]}")
  if [ "$N" = "1" ]; then
    emit_check S-ver-ollama pass "ollama version identical (${DROPLET_VER_OLLAMA[0]})"
  else
    emit_check S-ver-ollama fail "ollama version MISMATCHED ($N distinct)"
    fail_track
  fi
  N=$(fp_uniq "${DROPLET_VER_DRIVER[@]}")
  if [ "$N" = "1" ]; then
    emit_check D-ver-driver pass "NVIDIA driver version identical (${DROPLET_VER_DRIVER[0]})"
  else
    emit_check D-ver-driver fail "NVIDIA driver version MISMATCHED ($N distinct)"
    fail_track
  fi
fi

# --------------------------------------------------------------- Time skew (hard gate)
#
# Operator requirement (2026-05-09): time MUST be set and synchronized
# across all GPU nodes within the scope of testing. Hard-fail on any
# drift > 1.0s. Sample three times spaced 5s apart to catch a node
# that's slow-drifting, not just point-in-time skew.
if [ ${#DROPLETS[@]} -ge 2 ]; then
  echo
  echo "=== T2-T5: cross-droplet clock skew (3-sample) ==="
  declare -a SAMPLE_SKEWS=()
  for sample in 1 2 3; do
    EPOCHS=()
    for entry in "${DROPLETS[@]}"; do
      read -r name pub_ip _priv <<<"$entry"
      EP=$(ssh "${SSH_OPTS[@]}" "root@$pub_ip" "date +%s.%N" 2>/dev/null)
      EPOCHS+=("$EP")
    done
    if [ ${#EPOCHS[@]} -ge 2 ]; then
      SORTED=($(printf '%s\n' "${EPOCHS[@]}" | sort -n))
      MIN=${SORTED[0]}; MAX=${SORTED[-1]}
      SKEW=$(awk -v a="$MAX" -v b="$MIN" 'BEGIN{printf "%.3f", a-b}')
      SAMPLE_SKEWS+=("$SKEW")
      if awk -v s="$SKEW" 'BEGIN{exit !(s<=1.0)}'; then
        emit_check "T2-sample$sample" pass "sample $sample max skew ${SKEW}s (<= 1.0s)"
      else
        emit_check "T2-sample$sample" fail "sample $sample max skew ${SKEW}s (> 1.0s)"
        fail_track
      fi
    fi
    [ "$sample" -lt 3 ] && sleep 5
  done
  # T3 — drift between samples (catches a node clock running fast)
  if [ ${#SAMPLE_SKEWS[@]} -eq 3 ]; then
    DRIFT=$(awk -v a="${SAMPLE_SKEWS[0]}" -v b="${SAMPLE_SKEWS[2]}" \
      'BEGIN{d=b-a; if(d<0)d=-d; printf "%.3f", d}')
    if awk -v d="$DRIFT" 'BEGIN{exit !(d<=0.5)}'; then
      emit_check T3 pass "skew drift between sample 1 and sample 3: ${DRIFT}s (<= 0.5s)"
    else
      emit_check T3 fail "skew drift ${DRIFT}s (> 0.5s — a node clock is running fast or NTP service is broken)"
      fail_track
    fi
  fi
  # T4 — explicit NTP server reachability + active sync per droplet
  for entry in "${DROPLETS[@]}"; do
    read -r name pub_ip _priv <<<"$entry"
    NTP_STATUS=$(ssh "${SSH_OPTS[@]}" "root@$pub_ip" "
      sync_active=\$(timedatectl show --property=NTPSynchronized --value 2>/dev/null)
      service=\$(systemctl is-active systemd-timesyncd 2>/dev/null || systemctl is-active chrony 2>/dev/null || systemctl is-active ntp 2>/dev/null)
      stratum=\$(chronyc tracking 2>/dev/null | awk '/Stratum/{print \$3; exit}')
      offset=\$(chronyc tracking 2>/dev/null | awk '/System time/{print \$4; exit}')
      [ -z \"\$stratum\" ] && stratum=\$(timedatectl show-timesync 2>/dev/null | awk -F= '/Stratum/{print \$2; exit}')
      echo \"\${sync_active:-unknown};\${service:-missing};\${stratum:-?};\${offset:-?}\"
    " 2>/dev/null)
    SYNC_ACTIVE=$(echo "$NTP_STATUS" | awk -F';' '{print $1}')
    NTP_SVC=$(echo "$NTP_STATUS" | awk -F';' '{print $2}')
    NTP_STRAT=$(echo "$NTP_STATUS" | awk -F';' '{print $3}')
    NTP_OFFSET=$(echo "$NTP_STATUS" | awk -F';' '{print $4}')
    if [ "$SYNC_ACTIVE" = "yes" ] && [ "$NTP_SVC" = "active" ]; then
      emit_check "T4-$name" pass "NTP sync active (service=$NTP_SVC stratum=$NTP_STRAT offset=$NTP_OFFSET)" \
        "$(jq -nc --arg svc "$NTP_SVC" --arg s "$NTP_STRAT" --arg o "$NTP_OFFSET" '{service:$svc,stratum:$s,offset_seconds:$o}')"
    else
      emit_check "T4-$name" fail "NTP NOT actively syncing (sync=$SYNC_ACTIVE service=$NTP_SVC)"
      fail_track
    fi
  done
fi

# --------------------------------------------------------------- Pre-cert audit chain freshness
echo
echo "=== U3: pre-cert audit chain freshness ==="
TOTAL_PRIOR=0
for r in "${DROPLET_REPORTS[@]}"; do
  TOTAL_PRIOR=$(( TOTAL_PRIOR + $(echo "$r" | jq -r '.audit_existing_rows') ))
done
emit_check U3 pass "total prior audit rows=$TOTAL_PRIOR (delta will be measured at cert end)" \
  "$(jq -nc --arg n "$TOTAL_PRIOR" '{prior_total:($n|tonumber)}')"

# --------------------------------------------------------------- A: autonomous-tier per-node
echo
echo "=== A: autonomous-tier per-droplet validation ==="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if "${SCRIPT_DIR}/validate_autonomous_tier.sh" --track "$TRACK"; then
  emit_check A-overall pass "validate_autonomous_tier.sh PASS for all droplets"
else
  emit_check A-overall fail "validate_autonomous_tier.sh FAIL — see per-droplet output above"
  fail_track
fi

# --------------------------------------------------------------- finalize report
jq --arg track "$TRACK" --arg t "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
   --arg fail "$FAIL" \
   '. + {track:$track, generated_at:$t, overall_status:(if ($fail|tonumber)==0 then "pass" else "fail" end),
         total_checks:(.checks|length),
         pass_count:(.checks|map(select(.status=="pass"))|length),
         fail_count:(.checks|map(select(.status=="fail"))|length)}' \
   "$TMP_REPORT" > "$REPORT"
rm -f "$TMP_REPORT"

echo
echo "=== summary ==="
jq -r '"  total: \(.total_checks)  pass: \(.pass_count)  fail: \(.fail_count)\n  status: \(.overall_status)\n  report: '"$REPORT"'"' "$REPORT"

if [ "$FAIL" -eq 0 ]; then
  echo
  echo "[validate-baseline] ALL BASELINES PASS — cert run is authorized to start"
  exit 0
fi

echo
echo "[validate-baseline] FAIL — fix the issues above before kicking off cert testing" >&2
echo "                    Full report: $REPORT" >&2
exit 1
