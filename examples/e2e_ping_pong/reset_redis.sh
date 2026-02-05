#!/usr/bin/env bash

set -euo pipefail

REDIS_URL="${REDIS_URL:-redis://localhost:6379}"

delete_pattern() {
  local pattern="$1"
  while IFS= read -r key; do
    if [[ -n "$key" ]]; then
      redis-cli -u "$REDIS_URL" DEL "$key" >/dev/null
    fi
  done < <(redis-cli -u "$REDIS_URL" --scan --pattern "$pattern")
}

delete_pattern "agent.stream:ping*"
delete_pattern "agent.stream:pong*"
delete_pattern "mas.pending_request:*"

redis-cli -u "$REDIS_URL" DEL \
  "agent.state:ping" \
  "agent.state:pong" \
  "agent:ping" \
  "agent:pong" \
  "agent:ping:allowed_targets" \
  "agent:ping:blocked_targets" \
  "agent:pong:allowed_targets" \
  "agent:pong:blocked_targets" \
  "audit:by_sender:ping" \
  "audit:by_sender:pong" \
  "audit:by_target:ping" \
  "audit:by_target:pong" \
  >/dev/null

echo "[redis] cleared e2e_ping_pong keys"
