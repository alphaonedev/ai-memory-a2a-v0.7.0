#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Bootstrap PostgreSQL + Apache AGE + pgvector for the v0.7.0 cert track.
# Auto-detects host RAM and tunes proportionally — works on:
#   * s-4vcpu-16gb-amd  (track Q-pg, $0.125/hr CPU)
#   * gpu-4000adax1-20gb (32 GiB, alternate if GPU available)
#
# What it installs:
#   PostgreSQL 16
#   pgvector 0.7.4   (apt: postgresql-16-pgvector)
#   Apache AGE 1.5.0 (built from source against PG 16)
#
# Tuning (proportional to host RAM):
#   shared_buffers              25% of total RAM
#   effective_cache_size        75% of total RAM
#   work_mem                    32 MiB at 16 GiB; 64 MiB at 32 GiB+
#   maintenance_work_mem        1 GiB at 16 GiB; 2 GiB at 32 GiB+
#   max_connections             100 at 16 GiB; 200 at 32 GiB+
#   random_page_cost            1.1   (NVMe)
#   effective_io_concurrency    200
#
# What it creates:
#   Role:     aimemory  (SUPERUSER, password from /tmp/v07-a2a-pg-password.txt)
#   Database: aimemory  (extensions: age, vector)
#   Listen:   localhost,10.20.0.14 (VPC-private only)
#
# Schema bootstrap (memories, memory_links, agent_quotas, audit_log,
# kg_query_view, etc) is delegated to `ai-memory schema-init` invoked
# from openclaw-1 AFTER both bootstraps complete. This script gets
# the database engine ready; the openclaw bootstrap stack puts the
# schema in.
#
# Usage:
#   ./scripts/bootstrap_postgres_gpu.sh [--track Q] [--dry-run]
set -euo pipefail

TRACK="Q"
DRY_RUN=0
PG_PASSWORD_PATH="${PG_PASSWORD_PATH:-/tmp/v07-a2a-pg-password.txt}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=5)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --pw-path) PG_PASSWORD_PATH="$2"; shift 2 ;;
    -h|--help) sed -n '4,38p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# Locate the postgres droplet — try multiple known tag/name patterns
PG_LINE=""
for filter in "a2a-v07-cpu:a2a-v07-cpu-pg" "a2a-v07-pg:a2a-v07-pg-cpu" "track-$TRACK:a2a-v07-gpu-postgres"; do
  tag="${filter%%:*}"; pat="${filter##*:}"
  PG_LINE=$(doctl compute droplet list --tag-name "$tag" \
    --format Name,PublicIPv4,PrivateIPv4 --no-header 2>/dev/null \
    | awk -v p="$pat" '$1 ~ p {print; exit}')
  [[ -n "$PG_LINE" ]] && break
done
[[ -n "$PG_LINE" ]] || { echo "no postgres droplet found — provision first" >&2; exit 3; }
read -r PG_NAME PG_PUB PG_PRIV <<<"$PG_LINE"

# Generate a fresh password if none exists
if [[ ! -f "$PG_PASSWORD_PATH" ]]; then
  echo "[bootstrap-pg] generating fresh password at $PG_PASSWORD_PATH"
  openssl rand -hex 24 > "$PG_PASSWORD_PATH"
  chmod 0600 "$PG_PASSWORD_PATH"
fi
PG_PWD=$(<"$PG_PASSWORD_PATH")

echo "[bootstrap-pg] track=$TRACK postgres=$PG_NAME pub=$PG_PUB priv=$PG_PRIV"
echo "[bootstrap-pg] password file: $PG_PASSWORD_PATH ($(wc -c < "$PG_PASSWORD_PATH") bytes)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[dry-run] would ssh into root@$PG_PUB and run the bootstrap script"
  exit 0
fi

ssh "${SSH_OPTS[@]}" "root@$PG_PUB" \
  bash -se -- "$PG_PRIV" "$PG_PWD" <<'REMOTE'
set -euo pipefail

PRIV_IP="$1"; PG_PWD="$2"
PG_VER=16
AGE_VER=1.5.0

echo "===== bootstrap PostgreSQL $PG_VER + AGE $AGE_VER + pgvector ====="
echo "host: $(hostname)  priv_ip: $PRIV_IP  date: $(date -u)"

echo "--- step 1: enable PostgreSQL apt repo ---"
apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg lsb-release \
                       build-essential pkg-config flex bison \
                       postgresql-common
install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
sh -c 'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
apt-get update -qq

echo "--- step 2: install PG16 + dev headers + pgvector ---"
apt-get install -y -qq postgresql-${PG_VER} postgresql-server-dev-${PG_VER} \
                       postgresql-${PG_VER}-pgvector
systemctl enable --now postgresql

echo "--- step 3: build Apache AGE $AGE_VER from source ---"
if ! psql --no-psqlrc -tAc "SELECT 1 FROM pg_extension WHERE extname='age';" \
     postgres 2>/dev/null | grep -q 1; then
  cd /tmp
  rm -rf age
  git clone --depth 1 --branch "release/PG${PG_VER}/${AGE_VER}" https://github.com/apache/age.git
  cd age
  make PG_CONFIG=/usr/lib/postgresql/${PG_VER}/bin/pg_config -j"$(nproc)"
  make install PG_CONFIG=/usr/lib/postgresql/${PG_VER}/bin/pg_config
  echo "  AGE $AGE_VER built + installed"
else
  echo "  AGE already installed; skipping rebuild"
fi

echo "--- step 4: auto-detect host RAM and tune postgresql.conf proportionally ---"
PG_CONF="/etc/postgresql/${PG_VER}/main/postgresql.conf"
PG_HBA="/etc/postgresql/${PG_VER}/main/pg_hba.conf"

# /proc/meminfo MemTotal is in kB; convert to MB for tuning math.
TOTAL_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
SHARED_BUF_MB=$(( TOTAL_MB / 4 ))         # 25%
EFFECTIVE_CACHE_MB=$(( TOTAL_MB * 3 / 4 )) # 75%
if [ "$TOTAL_MB" -ge 30000 ]; then
  WORK_MEM=64MB; MAINT_MEM=2GB; MAX_CONN=200
elif [ "$TOTAL_MB" -ge 14000 ]; then
  WORK_MEM=32MB; MAINT_MEM=1GB; MAX_CONN=100
else
  WORK_MEM=16MB; MAINT_MEM=512MB; MAX_CONN=50
fi
echo "  detected ${TOTAL_MB}MB RAM → shared_buffers=${SHARED_BUF_MB}MB effective_cache=${EFFECTIVE_CACHE_MB}MB work_mem=$WORK_MEM maint_mem=$MAINT_MEM max_conn=$MAX_CONN"

sed -i.bak \
  -e "s|^#*shared_buffers.*|shared_buffers = ${SHARED_BUF_MB}MB|" \
  -e "s|^#*effective_cache_size.*|effective_cache_size = ${EFFECTIVE_CACHE_MB}MB|" \
  -e "s|^#*work_mem.*|work_mem = $WORK_MEM|" \
  -e "s|^#*maintenance_work_mem.*|maintenance_work_mem = $MAINT_MEM|" \
  -e "s|^#*max_connections.*|max_connections = $MAX_CONN|" \
  -e "s|^#*random_page_cost.*|random_page_cost = 1.1|" \
  -e "s|^#*effective_io_concurrency.*|effective_io_concurrency = 200|" \
  -e "s|^#*shared_preload_libraries.*|shared_preload_libraries = 'age'|" \
  -e "s|^#*listen_addresses.*|listen_addresses = 'localhost,${PRIV_IP}'|" \
  "$PG_CONF"

echo "--- step 5: pg_hba.conf — allow VPC CIDR with md5 + SCRAM (ALL DBs) ---"
# Append the CIDR rule for ALL databases (not just aimemory) so disposable
# databases like aimemory_perf_r3 created by S76 cargo bench can also auth.
grep -q '10\.20\.0\.0/24' "$PG_HBA" || \
  echo "host    all         aimemory    10.20.0.0/24    scram-sha-256" >> "$PG_HBA"

systemctl restart postgresql
sleep 3
systemctl is-active postgresql

echo "--- step 6: create role + db + extensions ---"
PSQL="sudo -u postgres psql --no-psqlrc"

# role
$PSQL -tAc "SELECT 1 FROM pg_roles WHERE rolname='aimemory';" 2>/dev/null \
  | grep -q 1 || \
$PSQL -c "CREATE ROLE aimemory WITH LOGIN SUPERUSER PASSWORD '${PG_PWD}';"

# database
$PSQL -tAc "SELECT 1 FROM pg_database WHERE datname='aimemory';" 2>/dev/null \
  | grep -q 1 || \
$PSQL -c "CREATE DATABASE aimemory OWNER aimemory;"

# extensions in the DB
$PSQL -d aimemory -c "CREATE EXTENSION IF NOT EXISTS age;"
$PSQL -d aimemory -c "CREATE EXTENSION IF NOT EXISTS vector;"
$PSQL -d aimemory -c "LOAD 'age';"
$PSQL -d aimemory -c "SELECT * FROM ag_catalog.create_graph('memory_graph');" 2>&1 \
  | grep -qE "already exists|create_graph" || true

echo "--- step 7: persist password locally on droplet (mode 600) ---"
echo -n "$PG_PWD" > /root/postgres-aimemory-pw.txt
chmod 0600 /root/postgres-aimemory-pw.txt

echo "--- step 8: smoke test ---"
PGPASSWORD="$PG_PWD" psql -h "$PRIV_IP" -U aimemory -d aimemory -tAc "
  SELECT 'pg='||version()||'|age='||(SELECT extversion FROM pg_extension WHERE extname='age')||'|vector='||(SELECT extversion FROM pg_extension WHERE extname='vector');
"

echo "--- step 9: verify shared_buffers tuning landed ---"
PGPASSWORD="$PG_PWD" psql -h "$PRIV_IP" -U aimemory -d aimemory -tAc "
  SELECT name||'='||setting||unit FROM pg_settings
  WHERE name IN ('shared_buffers','effective_cache_size','max_connections',
                 'work_mem','maintenance_work_mem','listen_addresses',
                 'shared_preload_libraries')
  ORDER BY name;
"

echo "===== postgres bootstrap done for $(hostname) ====="
REMOTE

echo
echo "[bootstrap-pg] done. Postgres ready at postgres://aimemory@$PG_PRIV:5432/aimemory"
echo "[bootstrap-pg] password persisted on droplet at /root/postgres-aimemory-pw.txt"
echo "[bootstrap-pg] Next: bootstrap_gpu_droplets.sh --track Q (deploys ai-memory daemon to openclaw nodes)"
echo "[bootstrap-pg] Then:  ai-memory schema-init --store-url postgres://aimemory:<pwd>@$PG_PRIV:5432/aimemory"
echo "                      (run from openclaw-1 once binary is deployed)"
