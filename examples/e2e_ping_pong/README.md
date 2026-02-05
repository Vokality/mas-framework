# End-to-end Example: Request/Reply over gRPC+mTLS

This example runs a MAS server and two agents in-process via the runner.

Prereqs:
- Redis running on `localhost:6379`
- `openssl`
- Optional for telemetry: Docker + Docker Compose

## OpenTelemetry Setup

This example has OpenTelemetry enabled in `mas.yaml` and exports to a local OTel Collector at `http://localhost:4319`.

Start Jaeger + OTel Collector + Prometheus:

```bash
docker compose up -d
```

Then open:

```bash
open http://localhost:16686
open http://localhost:9090
```

- Jaeger: search service `mas-e2e-ping-pong` for traces.
- Prometheus: query metrics like `mas_messages_ingress_total`, `mas_policy_latency_ms_count`, `mas_delivery_ack_total`.

If telemetry stack is not running, OpenTelemetry exporters will log `Connection refused` errors.
Set `gateway.telemetry.enabled: false` in `mas.yaml` to run the example without telemetry.

## Run

```bash
cd examples/e2e_ping_pong

# Generate CA, server cert, and agent certs (mTLS)
bash make_certs.sh

# Clear example Redis keys from previous runs
bash reset_redis.sh

# Run the runner (auto-loads ./mas.yaml)
uv run python -m mas
```

When done:

```bash
docker compose down
```

You should see:
- `PongAgent` starts as a responder for `patient_message`
- `PingAgent` sends a short sequence of patient turns and prints each `doctor_message` reply
- After the final turn, `PingAgent` shuts the system down
- Server and agent logs include `trace_id` and `span_id` for log/trace correlation

## Inspect Audit Logs

Messages and policy decisions are logged by the server into Redis Streams:

```bash
redis-cli XLEN audit:messages
redis-cli XRANGE audit:messages - + COUNT 10
```

Or tail them via the MAS CLI:

```bash
uv run mas audit tail --last 10
# or: uv run python -m mas audit tail --last 10
```
