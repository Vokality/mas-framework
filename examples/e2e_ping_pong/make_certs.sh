#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="$ROOT_DIR/certs"

mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

rm -f ./*.pem ./*.key ./*.csr ./*.cnf ./*.srl

echo "[certs] generating CA"
openssl genrsa -out ca.key 2048
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -subj "/CN=MAS Example CA" -out ca.pem

echo "[certs] generating server cert"
cat > server.cnf <<'EOF'
[req]
distinguished_name = dn
req_extensions = req_ext
prompt = no

[dn]
CN = localhost

[req_ext]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
EOF

openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -config server.cnf
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out server.pem -days 3650 -sha256 -extensions req_ext -extfile server.cnf

gen_agent_cert() {
  local agent_id="$1"

  echo "[certs] generating agent cert: $agent_id"

  cat > "${agent_id}.cnf" <<EOF
[req]
distinguished_name = dn
req_extensions = req_ext
prompt = no

[dn]
CN = ${agent_id}

[req_ext]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
URI.1 = spiffe://mas/agent/${agent_id}
EOF

  openssl genrsa -out "${agent_id}.key" 2048
  openssl req -new -key "${agent_id}.key" -out "${agent_id}.csr" -config "${agent_id}.cnf"
  openssl x509 -req -in "${agent_id}.csr" -CA ca.pem -CAkey ca.key -CAserial ca.srl -out "${agent_id}.pem" -days 3650 -sha256 -extensions req_ext -extfile "${agent_id}.cnf"
}

gen_agent_cert ping
gen_agent_cert pong

echo "[certs] done: wrote certs/ca.pem certs/server.pem certs/ping.pem certs/pong.pem"
