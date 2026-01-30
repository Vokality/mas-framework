# End-to-end Example: Ping/Pong over gRPC+mTLS

This example runs a MAS server and two agents in-process via the runner.

Prereqs:
- Redis running on `localhost:6379`
- `openssl`

## Run

```bash
cd examples/e2e_ping_pong

# Generate CA, server cert, and agent certs (mTLS)
bash make_certs.sh

# Run the runner (auto-loads ./mas.yaml)
uv run python -m mas
```

You should see:
- `PongAgent` receives `ping`
- `PingAgent` prints the `pong` reply and then shuts the system down

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
