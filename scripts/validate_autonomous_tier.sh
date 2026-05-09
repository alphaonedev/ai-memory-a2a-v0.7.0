#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# HARD-GATE validation that every openclaw GPU droplet is genuinely
# in autonomous-tier-full mode with all tools loaded BEFORE the cert
# run starts. Bails non-zero if any droplet fails any check — the
# operator can debug without burning more $/hr on a broken bring-up.
#
# Checks per droplet:
#   C1  capabilities.tier == "autonomous"
#   C2  capabilities advertises auto_tag, consolidate, expand_query,
#       detect_contradiction, smart_load
#   C3  Ollama healthy (http 200 on /api/tags)
#   C4  configured LLM is loaded (ollama list grep)
#   C5  smoke each LLM-bound endpoint:
#       - POST /api/v1/auto_tag returns 200 (not 501) on a sample memory
#       - POST /api/v1/consolidate returns 200
#       - POST /api/v1/expand_query (mode=llm) returns 200
#       - POST /api/v1/detect_contradiction returns 200
#       - POST /api/v1/smart_load returns 200
#   C6  postgres+AGE reachability from each droplet (psql + pg_extension)
#   C7  audit log writable
#   C8  daemon keypair present
#   C9  GPU memory headroom (>= 4GB free) after model load
#
# Usage:
#   ./scripts/validate_autonomous_tier.sh --track Q

set -euo pipefail

TRACK=""
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)
PG_PRIV="${PG_PRIV:-10.20.0.4}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track) TRACK="$2"; shift 2 ;;
    -h|--help) sed -n '4,28p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$TRACK" ]] || { echo "must pass --track Q" >&2; exit 2; }

mapfile -t DROPLETS < <(doctl compute droplet list --tag-name "track-$TRACK" \
  --format Name,PublicIPv4,PrivateIPv4 --no-header)

[[ ${#DROPLETS[@]} -gt 0 ]] || { echo "no droplets tagged track-$TRACK" >&2; exit 3; }

echo "[validate] track=$TRACK droplets=${#DROPLETS[@]}"

validate_one() {
  local name="$1" pub_ip="$2" priv_ip="$3"
  echo
  echo "===== validate $name ($pub_ip / $priv_ip) ====="
  ssh "${SSH_OPTS[@]}" "root@$pub_ip" \
    bash -se -- "$priv_ip" "$PG_PRIV" <<'REMOTE'
set -uo pipefail
PRIV_IP="$1"; PG_PRIV="$2"
RC=0
fail() { echo "  FAIL: $*" >&2; RC=1; }
ok()   { echo "  OK:   $*"; }

echo "[C1] capabilities.tier=autonomous"
RAW=$(curl -s --max-time 10 "http://$PRIV_IP:19077/api/v1/capabilities") || \
  fail "capabilities probe failed"
T=$(echo "$RAW" | jq -r '.tier // .memory_tier // ""')
[ "$T" = "autonomous" ] && ok "tier=$T" || fail "tier=$T (need autonomous)"

echo "[C2] capabilities advertises LLM-bound features"
for feat in auto_tag consolidate expand_query detect_contradiction smart_load; do
  if echo "$RAW" | jq -e --arg f "$feat" '
    (.features // .advertised // .endpoints // []) as $list
    | (any($list[]?; . == $f) // false)
    or ((.[$f] // null) != null)
  ' >/dev/null; then
    ok "advertised: $feat"
  else
    fail "NOT advertised: $feat"
  fi
done

echo "[C3] Ollama healthy"
if curl -sf --max-time 5 http://127.0.0.1:11434/api/tags >/dev/null; then
  ok "ollama /api/tags returns 200"
else
  fail "ollama unreachable"
fi

echo "[C4] configured LLM is loaded"
LLM=$(awk -F'"' '/^llm_model/{print $2; exit}' /etc/ai-memory/config.toml \
        2>/dev/null || echo "")
if [ -n "$LLM" ] && ollama list 2>/dev/null | awk '{print $1}' | grep -Fxq "$LLM"; then
  ok "$LLM is in ollama list"
else
  fail "configured LLM '$LLM' not in ollama list"
fi

echo "[C5] LLM-bound endpoints smoke (200 expected, NOT 501)"
SEED=$(curl -s --max-time 10 -H "Content-Type: application/json" \
  -H "X-Agent-Id: ai:validate-$RANDOM" \
  -d '{"tier":"mid","namespace":"validate-tier","title":"seed",
       "content":"Apollo launch readiness review on 2026-05-09 covering quorum and audit.",
       "priority":5,"confidence":1.0,"source":"api","metadata":{}}' \
  -X POST "http://$PRIV_IP:19077/api/v1/memories")
SEED_ID=$(echo "$SEED" | jq -r '.id // .body.id // empty')
[ -n "$SEED_ID" ] || fail "seed memory create failed: $SEED"

probe() {
  local feat="$1" body="$2"
  local code
  code=$(curl -s --max-time 30 -o /tmp/probe.body -w "%{http_code}" \
    -H "Content-Type: application/json" -H "X-Agent-Id: ai:validate-$RANDOM" \
    -d "$body" -X POST "http://$PRIV_IP:19077/api/v1/$feat" 2>/dev/null)
  if [ "$code" = "200" ] || [ "$code" = "201" ] || [ "$code" = "202" ]; then
    ok "$feat → $code"
  else
    fail "$feat → $code (body: $(head -c 200 /tmp/probe.body))"
  fi
}

probe auto_tag             "{\"memory_id\":\"$SEED_ID\"}"
probe consolidate          "{\"namespace\":\"validate-tier\"}"
probe expand_query         "{\"query\":\"apollo readiness\",\"mode\":\"llm\"}"
probe detect_contradiction "{\"memory_id\":\"$SEED_ID\"}"
probe smart_load           "{\"namespace\":\"validate-tier\",\"budget_tokens\":2000}"

echo "[C6] postgres+AGE reachable + AGE installed"
if [ -f /root/postgres-aimemory-pw.txt ]; then
  PGPASSWORD="$(cat /root/postgres-aimemory-pw.txt)" \
    psql -h "$PG_PRIV" -U aimemory -d aimemory -tAc \
      "SELECT extname FROM pg_extension WHERE extname='age';" 2>/dev/null \
    | grep -q age && ok "AGE extension installed" || fail "AGE not installed"
else
  fail "/root/postgres-aimemory-pw.txt missing — bootstrap incomplete"
fi

echo "[C7] audit log writable"
[ -d /var/log/ai-memory/audit ] && [ -w /var/log/ai-memory/audit ] \
  && ok "audit dir writable" || fail "audit dir missing/unwritable"

echo "[C8] daemon keypair present"
[ -f /root/.config/ai-memory/keys/daemon.priv ] \
  && ok "daemon keypair present" || fail "daemon keypair missing"

echo "[C9] GPU memory headroom"
FREE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
if [ -n "$FREE" ] && [ "$FREE" -ge 4000 ]; then
  ok "GPU free=${FREE} MiB (>= 4GB)"
else
  fail "GPU free=${FREE:-?} MiB (< 4GB headroom)"
fi

echo
[ $RC -eq 0 ] && echo "===== VALIDATION PASS for $(hostname) =====" || \
                  echo "===== VALIDATION FAIL for $(hostname) ====="
exit $RC
REMOTE
}

fail=0
for entry in "${DROPLETS[@]}"; do
  read -r name pub_ip priv_ip <<<"$entry"
  validate_one "$name" "$pub_ip" "$priv_ip" || { fail=1; echo "  → $name FAILED"; }
done

echo
if [[ $fail -eq 0 ]]; then
  echo "[validate] ALL DROPLETS PASS — cert run is authorized to start"
  exit 0
else
  echo "[validate] FAIL — at least one droplet did not pass autonomous-tier validation" >&2
  echo "          DO NOT start cert run; debug the failures above first" >&2
  exit 4
fi
