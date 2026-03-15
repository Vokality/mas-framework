#!/bin/sh

set -eu

runtime_dir="${1:-/runtime}"
cert_dir="${runtime_dir}/certs"
redis_url="${MAS_RUNTIME_REDIS_URL:-redis://redis:6379}"

mkdir -p "${cert_dir}"

if [ ! -f "${cert_dir}/ca.pem" ] || [ ! -f "${cert_dir}/ca.key" ]; then
  openssl genrsa -out "${cert_dir}/ca.key" 2048
  openssl req -x509 -new -nodes \
    -key "${cert_dir}/ca.key" \
    -sha256 \
    -days 3650 \
    -subj "/CN=MAS Local CA" \
    -out "${cert_dir}/ca.pem"
fi

cat > "${cert_dir}/server.cnf" <<'EOF'
[ req ]
default_bits       = 2048
prompt             = no
default_md         = sha256
distinguished_name = dn
req_extensions     = req_ext

[ dn ]
CN = mas-runtime

[ req_ext ]
subjectAltName = @alt_names

[ alt_names ]
DNS.1 = mas-runtime
DNS.2 = localhost
IP.1 = 127.0.0.1
EOF

openssl genrsa -out "${cert_dir}/server.key" 2048
openssl req -new \
  -key "${cert_dir}/server.key" \
  -out "${cert_dir}/server.csr" \
  -config "${cert_dir}/server.cnf"
openssl x509 -req \
  -in "${cert_dir}/server.csr" \
  -CA "${cert_dir}/ca.pem" \
  -CAkey "${cert_dir}/ca.key" \
  -CAcreateserial \
  -out "${cert_dir}/server.pem" \
  -days 3650 \
  -sha256 \
  -extensions req_ext \
  -extfile "${cert_dir}/server.cnf"

cat > "${runtime_dir}/mas.yaml" <<EOF
server_listen_addr: 0.0.0.0:50051
tls_ca_path: certs/ca.pem
tls_server_cert_path: certs/server.pem
tls_server_key_path: certs/server.key
gateway:
  redis:
    url: ${redis_url}
    decode_responses: true
  telemetry:
    enabled: false
agents: []
permissions: []
EOF
