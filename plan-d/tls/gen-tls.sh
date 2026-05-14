#!/usr/bin/env bash
# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
#
# Generate self-signed CA + 4-node mTLS material for the v0.7.0 grand-slam
# test cell (Mac Mini native, alice/bob/charlie/dave on 192.168.50.100).
#
# Each node gets one cert that serves BOTH as server cert (HTTPS termination)
# and as client cert (outbound federation /sync/push). The same SHA-256
# fingerprint is added to the mTLS allowlist on every node.
#
# Outputs in cwd:
#   ca.key, ca.pem
#   node-<name>.key, node-<name>.pem  (server+client; SAN: 192.168.50.100, ::1, 127.0.0.1, <name>.local)
#   allowlist.txt   (SHA-256 fingerprints, one per node, hex-with-colons)
#
# Idempotent: skips a node if its .pem already exists.
set -euo pipefail

NODES="${NODES:-alice bob charlie dave}"
DAYS="${DAYS:-3650}"           # test-cell lifetime
SUBJECT_BASE="/C=US/O=AlphaOne v0.7.0 test cell"
SAN_IP="${SAN_IP:-192.168.50.100}"

umask 077

# --- CA -------------------------------------------------------------------
if [[ ! -f ca.pem ]]; then
  echo "[gen-tls] creating CA (P-256 ECDSA for broad client compat)"
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out ca.key
  openssl req -x509 -new -nodes -key ca.key -sha256 -days "$DAYS" \
    -subj "$SUBJECT_BASE/CN=v07-test-cell-ca" \
    -out ca.pem
else
  echo "[gen-tls] CA already present, reusing"
fi
chmod 0600 ca.key
chmod 0644 ca.pem

# --- per-node certs ------------------------------------------------------
> allowlist.txt.new
for name in $NODES; do
  if [[ -f "node-${name}.pem" ]]; then
    echo "[gen-tls] node-${name} cert exists, reusing"
  else
    echo "[gen-tls] minting node-${name} (P-256 ECDSA)"
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "node-${name}.key"
    cat >"node-${name}.cnf" <<CNF
[req]
distinguished_name = dn
req_extensions = v3_req
prompt = no
[dn]
CN = ${name}
O = AlphaOne v0.7.0 test cell
[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = @alt_names
[alt_names]
DNS.1 = ${name}
DNS.2 = ${name}.local
DNS.3 = localhost
IP.1 = ${SAN_IP}
IP.2 = 127.0.0.1
IP.3 = ::1
CNF
    openssl req -new -key "node-${name}.key" -out "node-${name}.csr" \
      -config "node-${name}.cnf"
    openssl x509 -req -in "node-${name}.csr" \
      -CA ca.pem -CAkey ca.key -CAcreateserial \
      -out "node-${name}.pem" -days "$DAYS" -sha256 \
      -extfile "node-${name}.cnf" -extensions v3_req
    rm -f "node-${name}.csr" "node-${name}.cnf"
  fi
  chmod 0600 "node-${name}.key"
  chmod 0644 "node-${name}.pem"
  # Compute SHA-256 fingerprint for the mtls-allowlist (hex with colons,
  # matching the ai-memory mtls-allowlist parser).
  fp=$(openssl x509 -in "node-${name}.pem" -noout -fingerprint -sha256 \
       | sed 's/^.*=//')
  echo "# ${name}" >>allowlist.txt.new
  echo "${fp}" >>allowlist.txt.new
done
mv allowlist.txt.new allowlist.txt
chmod 0644 allowlist.txt

echo
echo "[gen-tls] material written:"
ls -la ca.pem ca.key node-*.pem node-*.key allowlist.txt 2>/dev/null | awk '{print "  ",$1,$NF}'
echo
echo "[gen-tls] verifying each node cert against CA..."
for name in $NODES; do
  if openssl verify -CAfile ca.pem "node-${name}.pem" >/dev/null 2>&1; then
    echo "  node-${name}: OK"
  else
    echo "  node-${name}: FAIL"
    exit 1
  fi
done
