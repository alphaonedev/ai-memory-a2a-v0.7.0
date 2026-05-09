#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Bootstrap the PostgreSQL + Apache AGE + pgvector node for the v0.7.0
# GPU cert track. Runs against the gpu-4000adax1-20gb postgres droplet
# at 10.20.0.14 (provisioned by provision_gpu_droplets.sh --track Q).
#
# What it installs:
#   PostgreSQL 16
#   pgvector 0.7.4   (apt: postgresql-16-pgvector)
#   Apache AGE 1.5.0 (built from source against PG 16)
#
# What it tunes (for 32 GiB host):
#   shared_buffers              8 GiB
#   effective_cache_size        24 GiB
#   work_mem                    64 MiB
#   maintenance_work_mem        2 GiB
#   max_connections             200
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

# Locate the postgres droplet (one tagged track-$TRACK with name a2a-v07-gpu-postgres-*)
PG_LINE=$(doctl compute droplet list --tag-name "track-$TRACK" \
  --format Name,PublicIPv4,PrivateIPv4 --no-header 2>/dev/null \
  | awk '/a2a-v07-gpu-postgres/{print; exit}')
[[ -n "$PG_LINE" ]] || { echo "no postgres droplet found in track-$TRACK — provision first" >&2; exit 3; }
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

echo "--- step 4: tune postgresql.conf for 32 GiB host ---"
PG_CONF="/etc/postgresql/${PG_VER}/main/postgresql.conf"
PG_HBA="/etc/postgresql/${PG_VER}/main/pg_hba.conf"
sed -i.bak \
  -e "s|^#*shared_buffers.*|shared_buffers = 8GB|" \
  -e "s|^#*effective_cache_size.*|effective_cache_size = 24GB|" \
  -e "s|^#*work_mem.*|work_mem = 64MB|" \
  -e "s|^#*maintenance_work_mem.*|maintenance_work_mem = 2GB|" \
  -e "s|^#*max_connections.*|max_connections = 200|" \
  -e "s|^#*random_page_cost.*|random_page_cost = 1.1|" \
  -e "s|^#*effective_io_concurrency.*|effective_io_concurrency = 200|" \
  -e "s|^#*shared_preload_libraries.*|shared_preload_libraries = 'age'|" \
  -e "s|^#*listen_addresses.*|listen_addresses = 'localhost,${PRIV_IP}'|" \
  "$PG_CONF"

echo "--- step 5: pg_hba.conf — allow VPC CIDR with md5 + SCRAM ---"
# Append our CIDR rule if not already present
grep -q '10\.20\.0\.0/24' "$PG_HBA" || \
  echo "host    aimemory    aimemory    10.20.0.0/24    scram-sha-256" >> "$PG_HBA"

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
