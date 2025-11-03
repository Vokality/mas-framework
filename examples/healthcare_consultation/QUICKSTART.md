# Quick Start Guide - Healthcare Consultation (Gateway Mode)

## 5-Minute Setup

### 1. Install Dependencies

```bash
# Install with uv
uv pip install openai python-dotenv
```

### 2. Start Redis (5.0+ required for Streams)

**macOS with Homebrew:**
```bash
brew install redis
brew services start redis
```

**Docker:**
```bash
docker run -d -p 6379:6379 redis:latest
```

**Check version:**
```bash
redis-cli INFO | grep redis_version
# Should be 5.0 or higher
```

### 3. Set OpenAI API Key

**Option 1: Add to .env file (Recommended)**
```bash
cd /path/to/mas-framework
echo "OPENAI_API_KEY=sk-..." >> .env
```

**Option 2: Export as environment variable**
```bash
export OPENAI_API_KEY='sk-...'
```

### 4. Run the Demo

```bash
# Easy way - use the run script
./run.sh

# Manual way with uv
uv run python main.py

# Or from project root
uv run python -m examples.healthcare_consultation
```

## What to Expect

The demo will:

1. Start MAS Service (agent registry)
2. Start Gateway Service with all security features
3. Start Doctor agent (registers with `healthcare_doctor` capability)
4. Start Patient agent (discovers doctor via gateway)
5. Patient asks 3 health questions about wellness
6. **Gateway processes each message through:**
   - Authentication (validates agent token)
   - Authorization (checks RBAC permissions)
   - Rate limiting (enforces limits)
   - DLP scanning (detects PHI/PII)
   - Audit logging (immutable trail)
   - Routing via Redis Streams
7. Doctor receives messages and provides advice
8. Complete audit trail logged for compliance

**Expected runtime:** ~60 seconds for 3 consultations

## Gateway vs Peer-to-Peer

| Aspect | P2P (Chemistry) | Gateway (Healthcare) |
|--------|-----------------|----------------------|
| **Setup** | Simple | Requires gateway |
| **Latency** | Low (~5ms) | Higher (~20ms) |
| **Delivery** | At-most-once | At-least-once |
| **Security** | None | Full stack |
| **Audit** | None | Complete |
| **Compliance** | No | HIPAA/SOC2/GDPR |
| **Use Case** | Dev/test | Production |

## Key Differences in Code

**Chemistry (Peer-to-Peer):**
```python
agent = StudentAgent(
    agent_id="student_alex",
    redis_url=redis_url,
    # use_gateway defaults to False
)
```

**Healthcare (Gateway):**
```python
agent = PatientAgent(
    agent_id="patient_jones",
    redis_url=redis_url,
    use_gateway=True,  # Enable gateway mode
)

# Also requires Gateway Service running
gateway = GatewayService(redis_url=redis_url)
await gateway.start()
```

## Sample Output

```
================================================
Healthcare Consultation Demo - GATEWAY MODE
================================================

✓ uv is installed
✓ Redis is running
✓ Found .env file in project root
✓ Dependencies ready

Starting Gateway Mode Demo...
Gateway Features:
  • Authentication & Authorization
  • Rate Limiting
  • Data Loss Prevention (DLP)
  • Complete Audit Trail
  • Circuit Breakers
  • At-least-once delivery
================================================

2024-11-03 12:00:01 - INFO - Starting Gateway Service...
2024-11-03 12:00:01 - INFO - ✓ Gateway Service started

2024-11-03 12:00:02 - INFO - Doctor agent doctor_smith started (GATEWAY MODE)
2024-11-03 12:00:03 - INFO - Patient agent patient_jones started (GATEWAY MODE)

============================================================
PATIENT'S QUESTION #1:
What are the key preventive health measures I should focus on?
============================================================

2024-11-03 12:00:05 - INFO - ✓ Message sent through gateway (auth, audit, DLP applied)
2024-11-03 12:00:05 - INFO - ✓ Gateway validated: auth, authz, rate limit, DLP passed

[Doctor's detailed medical advice follows...]

[Continues for 2 more consultations...]

============================================================
Demo complete!
All consultations logged in audit trail for compliance
============================================================
```

## Troubleshooting

**"Redis version too old"**
- Gateway requires Redis 5.0+ for Streams
- Upgrade: `brew upgrade redis` or use newer Docker image
- Check: `redis-cli INFO | grep redis_version`

**"Gateway Service failed to start"**
- Verify Redis is running: `redis-cli ping`
- Check Redis supports Streams (5.0+)
- Review error logs for specific issue

**"Authentication failed"**
- Gateway auto-assigns tokens on registration
- Ensure agent started successfully
- Check gateway logs for auth errors

**"Rate limit exceeded"**
- Default: 100/min, 1000/hour per agent
- Adjust in main.py gateway config
- Wait for rate limit window to reset

**"DLP blocked message"**
- Message contained PHI/PII patterns
- Normal behavior for sensitive data
- Check audit log for DLP findings

## Next Steps

1. Compare with chemistry_tutoring example (P2P mode)
2. Read [GATEWAY.md](../../GATEWAY.md) for architecture details
3. Test DLP by including sensitive data in questions
4. Experiment with rate limits
5. Review audit logs in Redis Streams
6. Configure gateway policies

## Architecture Diagram

```
┌────────────────┐         ┌─────────────────┐
│ Patient Agent  │         │  Doctor Agent   │
│ (patient_jones)│         │ (doctor_smith)  │
└───────┬────────┘         └────────┬────────┘
        │                           │
        │  1. Message + Token       │
        ├──────────────┐            │
        │              ▼            │
        │    ┌─────────────────┐   │
        │    │ Gateway Service │   │
        │    ├─────────────────┤   │
        │    │ • Authenticate  │   │
        │    │ • Authorize     │   │
        │    │ • Rate Limit    │   │
        │    │ • DLP Scan      │   │
        │    │ • Audit Log     │   │
        │    └────────┬────────┘   │
        │             │            │
        │     2. Via Redis        │
        │        Streams          │
        │             │            │
        │             └────────────┤
        │                          │
        │  3. Receives message     │
        │     (at-least-once)      │
        └──────────────────────────┘
```

## Gateway Features in Detail

### Authentication
- Automatic token generation on agent registration
- Token validation on every message
- Failed auth logged to audit trail

### Authorization
- Role-based access control (RBAC)
- Configurable permission rules
- Denials logged and blocked

### Rate Limiting
- Token bucket algorithm
- Per-agent limits (minute + hour)
- Graceful backpressure

### DLP (Data Loss Prevention)
- Pattern matching for PHI/PII
- Configurable policies: log, redact, block
- SSN, credit cards, medical IDs detected

### Audit Trail
- Every message logged to Redis Streams
- Immutable record for compliance
- Includes auth, authz, rate limit, DLP events
- Queryable for reporting

### Circuit Breakers
- Automatic failure detection
- Prevents cascading failures
- Configurable thresholds

### Reliable Delivery
- Redis Streams (not pub/sub)
- At-least-once guarantee
- Consumer groups for scaling
