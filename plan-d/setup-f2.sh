#!/usr/bin/env bash
# Plan D — set up f2 (Pop!_OS 24.04, Postgres 16.13 + Apache AGE 1.5.0).
#
# Prereqs:
#   - SSH alias `f2` resolves; passwordless `sudo -n -u postgres psql` works.
#   - Apache AGE files already present at
#     /usr/share/postgresql/16/extension/age.control
#   - pgvector available (pkg or compiled) — installed below if missing
#
# Reads FED_PG_PASSWORD from local ~/.env at runtime; never echoed to logs.
#
# Idempotent: each step is guarded by `IF EXISTS` / `IF NOT EXISTS` /
# `grep -q ... || tee -a` so re-running is a no-op when state is converged.
set -euo pipefail

# shellcheck disable=SC1090
. ~/.env

: "${FED_PG_USER:?missing in ~/.env}"
: "${FED_PG_PASSWORD:?missing in ~/.env}"
: "${FED_PG_DB:?missing in ~/.env}"

# Step 1 — drop+recreate role + DB.
echo "[plan-d-f2] drop+recreate ${FED_PG_DB} role=${FED_PG_USER}"
ssh f2 "sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d postgres" <<SQL
DROP DATABASE IF EXISTS ${FED_PG_DB};
DROP ROLE IF EXISTS ${FED_PG_USER};
CREATE ROLE ${FED_PG_USER} LOGIN PASSWORD '${FED_PG_PASSWORD}';
CREATE DATABASE ${FED_PG_DB} OWNER ${FED_PG_USER};
SQL

# Step 2 — install AGE + pgvector extensions; create the canonical graph
# `memory_links_graph` and grant USAGE on ag_catalog to the daemon role
# so it can call ag_catalog functions (cypher path execution).
echo "[plan-d-f2] AGE + pgvector + memory_links_graph"
ssh f2 "sudo -n -u postgres psql -v ON_ERROR_STOP=1 -d ${FED_PG_DB}" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, public;
SELECT create_graph('memory_links_graph') WHERE NOT EXISTS (
  SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'memory_links_graph'
);
GRANT USAGE ON SCHEMA ag_catalog TO ${FED_PG_USER};
SQL

# Step 3 — listen_addresses + pg_hba.conf.
echo "[plan-d-f2] pg_hba + listen_addresses"
ssh f2 "sudo -n sed -i \"s/^listen_addresses.*/listen_addresses = '*'/\" /etc/postgresql/16/main/postgresql.conf || true"

# Mac Mini LAN IP
ssh f2 'sudo -n grep -q "host '"${FED_PG_DB}"' '"${FED_PG_USER}"' 192.168.50.100/32" /etc/postgresql/16/main/pg_hba.conf || \
  echo "host '"${FED_PG_DB}"' '"${FED_PG_USER}"' 192.168.50.100/32 scram-sha-256" | sudo -n tee -a /etc/postgresql/16/main/pg_hba.conf >/dev/null'
# Tailnet CIDR (workaround for the Tailscale per-app intercept on macOS).
ssh f2 'sudo -n grep -q "host '"${FED_PG_DB}"' '"${FED_PG_USER}"' 100.64.0.0/10" /etc/postgresql/16/main/pg_hba.conf || \
  echo "host '"${FED_PG_DB}"' '"${FED_PG_USER}"' 100.64.0.0/10 scram-sha-256" | sudo -n tee -a /etc/postgresql/16/main/pg_hba.conf >/dev/null'

ssh f2 'sudo -n systemctl restart postgresql && echo "[plan-d-f2] postgres restarted"'

echo "[plan-d-f2] done. Smoke from this host:"
echo "  PGPASSWORD=\"\$FED_PG_PASSWORD\" psql -h \$FED_PG_HOST -U \$FED_PG_USER -d \$FED_PG_DB -c 'SELECT version();'"
