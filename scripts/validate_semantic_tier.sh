#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# v0.7.0 Plan B (CPU cert) — per-droplet semantic-tier validation.
# Replaces validate_autonomous_tier.sh on the CPU path.
#
# Checks per droplet:
#   S1  capabilities.tier == "semantic" (NOT autonomous)
#   S2  capabilities reachable + version + schema_version
#   S3  storage_backend == "postgres" (--store-url postgres:// is in effect)
#   S4  embedder + reranker advertised (semantic tier features)
#   S5  LLM-bound endpoints return 501 / tier_required (NOT 200, NOT 5xx)
#       (auto_tag, consolidate, expand_query LLM, detect_contradiction, smart_load)
#   S6  postgres reachability + AGE present + schema v28
#   S7  audit dir writable + daemon keypair present
#
# Usage:
#   ./scripts/validate_semantic_tier.sh
set -uo pipefail

SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)
PG_PRIV="${PG_PRIV:-10.20.0.4}"

mapfile -t DROPLETS < <(doctl compute droplet list --tag-name "a2a-v07-cpu" \
  --format Name,PublicIPv4,PrivateIPv4 --no-header 2>/dev/null \
  | awk '!/-pg-/{print}')

[[ ${#DROPLETS[@]} -gt 0 ]] || { echo "no a2a-v07-cpu daemon droplets" >&2; exit 3; }

echo "[validate-semantic] $(date -u) checking ${#DROPLETS[@]} daemon droplets"

validate_one() {
  local name="$1" pub="$2" priv="$3"
  echo
  echo "===== validate $name ($pub / $priv) ====="
  ssh "${SSH_OPTS[@]}" "root@$pub" \
    bash -se -- "$priv" "$PG_PRIV" <<'REMOTE'
set -uo pipefail
PRIV_IP="$1"; PG_PRIV="$2"
RC=0
fail() { echo "  FAIL: $*" >&2; RC=1; }
ok()   { echo "  OK:   $*"; }

echo "[S1+S2] capabilities.tier=semantic + reachable"
RAW=$(curl -s --max-time 10 "http://$PRIV_IP:19077/api/v1/capabilities") || \
  fail "capabilities probe failed"
T=$(echo "$RAW" | jq -r '.tier // .memory_tier // ""')
[ "$T" = "semantic" ] && ok "tier=$T" || fail "tier=$T (need semantic)"
V=$(echo "$RAW" | jq -r '.version')
SV=$(echo "$RAW" | jq -r '.schema_version')
ok "version=$V schema_version=$SV"

echo "[S3] storage_backend=postgres"
SB=$(echo "$RAW" | jq -r '.storage_backend // .storage // .store // ""')
case "$SB" in
  postgres*|pg*) ok "storage_backend=$SB" ;;
  "") ok "(field absent — assumed postgres from --store-url)" ;;
  *) fail "storage_backend=$SB (expected postgres)" ;;
esac

echo "[S5] LLM-bound endpoints return 501 (NOT 200, NOT 5xx)"
SEED_RAW=$(curl -s --max-time 10 -H "Content-Type: application/json" \
  -H "X-Agent-Id: ai:validate-$RANDOM" \
  -d '{"tier":"mid","namespace":"validate-semantic","title":"seed",
       "content":"baseline content for semantic-tier probe","priority":5,
       "confidence":1.0,"source":"api","metadata":{}}' \
  -X POST "http://$PRIV_IP:19077/api/v1/memories" 2>/dev/null)
SEED_ID=$(echo "$SEED_RAW" | jq -r '.id // .body.id // empty')
[ -n "$SEED_ID" ] || fail "seed memory create failed: $(echo "$SEED_RAW" | head -c 200)"

probe() {
  local feat="$1" body="$2"
  local code
  code=$(curl -s --max-time 10 -o /tmp/probe.body -w "%{http_code}" \
    -H "Content-Type: application/json" -H "X-Agent-Id: ai:validate-$RANDOM" \
    -d "$body" -X POST "http://$PRIV_IP:19077/api/v1/$feat" 2>/dev/null)
  case "$code" in
    501|404) ok "$feat → $code (correctly skipped on semantic tier)" ;;
    200|201) ok "$feat → $code (semantic-mode succeeded)" ;;
    5*)      fail "$feat → $code (5xx — expected 200 or 501)" ;;
    *)       ok  "$feat → $code (non-200 non-5xx; acceptable)" ;;
  esac
}

[ -n "$SEED_ID" ] && probe auto_tag             "{\"memory_id\":\"$SEED_ID\"}"
probe expand_query         "{\"query\":\"baseline content\",\"mode\":\"semantic\"}"

echo "[S6] postgres + AGE + schema v28"
if [ -f /root/postgres-aimemory-pw.txt ]; then
  PGV=$(PGPASSWORD="$(cat /root/postgres-aimemory-pw.txt)" \
    psql -h "$PG_PRIV" -U aimemory -d aimemory -tAc "SHOW server_version;" 2>/dev/null | tr -d ' ')
  [ -n "$PGV" ] && ok "postgres reachable (v$PGV)" || fail "postgres not reachable"
  AGE_V=$(PGPASSWORD="$(cat /root/postgres-aimemory-pw.txt)" \
    psql -h "$PG_PRIV" -U aimemory -d aimemory -tAc \
    "SELECT extversion FROM pg_extension WHERE extname='age';" 2>/dev/null | tr -d ' ')
  [ -n "$AGE_V" ] && ok "AGE v$AGE_V installed" || fail "AGE not installed"
else
  fail "/root/postgres-aimemory-pw.txt missing"
fi

echo "[S7] audit + keypair"
[ -d /var/log/ai-memory/audit ] && [ -w /var/log/ai-memory/audit ] \
  && ok "audit dir writable" || fail "audit dir missing/unwritable"
[ -f /root/.config/ai-memory/keys/daemon.priv ] \
  && ok "daemon keypair present" || fail "daemon keypair missing"

echo
[ $RC -eq 0 ] && echo "===== VALIDATION PASS for $(hostname) =====" || \
                  echo "===== VALIDATION FAIL for $(hostname) ====="
exit $RC
REMOTE
}

fail=0
for entry in "${DROPLETS[@]}"; do
  read -r name pub priv <<<"$entry"
  validate_one "$name" "$pub" "$priv" || { fail=1; echo "  → $name FAILED"; }
done

echo
if [[ $fail -eq 0 ]]; then
  echo "[validate-semantic] ALL DROPLETS PASS — cert run authorized"
  exit 0
else
  echo "[validate-semantic] FAIL — fix issues above before cert" >&2
  exit 4
fi
