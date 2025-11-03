# Healthcare Consultation Example - Gateway Mode

This example demonstrates the **Gateway Mode** architecture with two OpenAI-powered agents exchanging healthcare information with full security and compliance features.

## Architecture: Gateway Mode

```
Patient Agent → Gateway Service → Redis Streams → Doctor Agent
                (auth, authz,      (reliable
                 rate limit,        delivery)
                 DLP, audit)
```

## Key Difference from Chemistry Example

| Feature | Chemistry (P2P) | Healthcare (Gateway) |
|---------|-----------------|----------------------|
| **Messaging** | Direct Redis pub/sub | Routed through gateway |
| **Delivery** | At-most-once | At-least-once (Streams) |
| **Authentication** | None | Token-based auth |
| **Authorization** | None | RBAC enforcement |
| **Rate Limiting** | None | Token bucket limits |
| **DLP** | None | PHI/PII detection |
| **Audit Trail** | None | Complete immutable log |
| **Use Case** | Dev/test, low latency | Production, compliance |

## Gateway Features Demonstrated

### 1. Authentication
- Token-based agent authentication
- Validates agent identity before message processing

### 2. Authorization (RBAC)
- Role-based access control
- Enforces permissions for agent-to-agent communication

### 3. Rate Limiting
- Per-agent token bucket rate limits
- Prevents abuse and overload
- Configurable per-minute and per-hour limits

### 4. Data Loss Prevention (DLP)
- Scans messages for PHI/PII patterns
- Blocks messages containing sensitive data
- Configurable policies (log, redact, block)

### 5. Audit Logging
- Complete audit trail in Redis Streams
- Immutable log of all messages
- Includes auth decisions, rate limit events, DLP findings
- HIPAA/SOC2/GDPR compliance ready

### 6. Circuit Breakers
- Automatic failure isolation
- Prevents cascading failures
- Protects healthy agents from failing agents

### 7. Message Signing
- Cryptographic message verification
- Ensures message integrity
- Prevents tampering

### 8. Reliable Delivery
- Uses Redis Streams for at-least-once delivery
- Messages persisted until acknowledged
- Automatic retry on failure

## Prerequisites

1. **Redis**: Running locally on port 6379
   ```bash
   # macOS with Homebrew
   brew install redis
   brew services start redis
   
   # Or with Docker
   docker run -d -p 6379:6379 redis:latest
   ```

2. **OpenAI API Key**: Add to `.env` file in project root
   ```bash
   echo "OPENAI_API_KEY=your-key-here" >> ../../.env
   ```

3. **Python Dependencies**: Install with uv
   ```bash
   uv pip install openai python-dotenv
   ```

## Running the Example

```bash
cd examples/healthcare_consultation

# Run with the script
./run.sh

# Or manually
uv run python main.py

# Or from project root
uv run python -m examples.healthcare_consultation
```

## What Happens

1. **MAS Service** starts (agent registry)
2. **Gateway Service** starts with all security features enabled
3. **Doctor Agent** starts and registers (capability: `healthcare_doctor`)
4. **Patient Agent** starts and discovers doctor
5. Patient asks 3 health questions about wellness
6. Each message flows through gateway:
   - ✓ Authentication validated
   - ✓ Authorization checked (RBAC)
   - ✓ Rate limit enforced
   - ✓ DLP scan performed
   - ✓ Audit log entry created
   - ✓ Message routed to Redis Streams
   - ✓ Doctor receives via reliable delivery
7. Doctor provides medical advice
8. Gateway logs complete consultation trail

**Expected runtime:** ~60 seconds for 3 consultations

## Sample Output

```
2024-11-03 12:00:00 - INFO - ============================================================
Healthcare Consultation Demo - GATEWAY MODE
============================================================

Gateway Features Enabled:
  ✓ Authentication - Token-based agent auth
  ✓ Authorization - Role-based access control
  ✓ Rate Limiting - Prevents abuse
  ✓ DLP - Detects/blocks PHI/PII leakage
  ✓ Audit Trail - Complete consultation log
  ✓ Circuit Breakers - Failure isolation
  ✓ At-least-once delivery - Redis Streams
============================================================

2024-11-03 12:00:01 - INFO - Starting Gateway Service...
2024-11-03 12:00:01 - INFO - ✓ Gateway Service started

2024-11-03 12:00:02 - INFO - Doctor agent doctor_smith started (GATEWAY MODE)
2024-11-03 12:00:02 - INFO - HIPAA-compliant: All messages audited and DLP-scanned
2024-11-03 12:00:03 - INFO - Patient agent patient_jones started (GATEWAY MODE)
2024-11-03 12:00:03 - INFO - Security features: Auth, RBAC, Rate Limiting, DLP, Audit

============================================================
PATIENT'S QUESTION #1:
I'm interested in improving my overall wellness. What are the most 
important preventive health measures I should be taking?
============================================================

2024-11-03 12:00:05 - INFO - ✓ Message sent through gateway (auth, audit, DLP applied)
2024-11-03 12:00:05 - INFO - Received consultation request from patient_jones
2024-11-03 12:00:05 - INFO - ✓ Gateway validated: auth, authz, rate limit, DLP passed

============================================================
DOCTOR'S ADVICE:
Excellent question! Preventive care is the foundation of long-term 
health. The most important measures include regular health screenings 
appropriate for your age and risk factors, maintaining a balanced diet 
rich in fruits, vegetables, and whole grains, and engaging in at least 
150 minutes of moderate aerobic activity weekly.

Additionally, ensure you're up to date with vaccinations, get adequate 
sleep (7-9 hours nightly), manage stress through mindfulness or 
relaxation techniques, and avoid tobacco while limiting alcohol 
consumption. Regular check-ups with your primary care physician allow 
for early detection of potential health issues.

Remember, preventive care is personalized - your doctor can help create 
a plan tailored to your specific health profile and family history.
============================================================

[... continues for 2 more questions ...]

============================================================
Demo complete!
All consultations logged in audit trail for compliance
============================================================
```

## Gateway Configuration

The example uses these gateway settings (see `main.py`):

```python
gateway = GatewayService(
    redis_url=redis_url,
    rate_limit_per_minute=100,    # Max 100 messages/minute per agent
    rate_limit_per_hour=1000,     # Max 1000 messages/hour per agent
    enable_dlp=True,              # Enable DLP scanning
    enable_priority_queue=False,  # Use direct routing (MVP)
)
```

## Compliance Features

### HIPAA Compliance
- ✓ Complete audit trail of all consultations
- ✓ PHI detection and prevention (DLP)
- ✓ Access controls (authentication + authorization)
- ✓ Integrity controls (message signing)

### SOC2 Compliance
- ✓ Audit logging for all security events
- ✓ Access control enforcement
- ✓ Rate limiting prevents availability issues
- ✓ Circuit breakers for reliability

### GDPR Compliance
- ✓ Audit trail for data processing activities
- ✓ DLP prevents unauthorized data disclosure
- ✓ Access controls for data protection

## Customization

### Change Health Topic
Edit `patient_agent.py` line ~44:
```python
self.current_concern = "managing chronic conditions"
```

### Adjust Number of Questions
Edit `patient_agent.py` line ~43:
```python
self.max_questions = 5
```

### Configure DLP Sensitivity
Edit `main.py` gateway configuration:
```python
gateway = GatewayService(
    enable_dlp=True,
    # DLP configuration would go in gateway config file
)
```

### Modify Rate Limits
Edit `main.py`:
```python
gateway = GatewayService(
    rate_limit_per_minute=50,   # Stricter limit
    rate_limit_per_hour=500,
)
```

## Gateway vs Peer-to-Peer

### When to Use Gateway Mode

✅ **Use Gateway** when you need:
- HIPAA/SOC2/GDPR compliance
- Complete audit trail
- PHI/PII protection
- Rate limiting
- Authentication/authorization
- Message reliability (at-least-once)
- Production enterprise deployment

❌ **Use Peer-to-Peer** when you need:
- Lowest possible latency
- Highest throughput
- Simple dev/test environment
- No compliance requirements
- Minimal overhead

## Troubleshooting

**"Gateway Service failed to start"**
- Ensure Redis is running: `redis-cli ping`
- Check Redis supports Streams: Redis 5.0+
- Verify no port conflicts

**"Authentication failed"**
- Gateway assigns tokens on agent registration
- Check agent started successfully
- Review gateway logs for auth errors

**"Rate limit exceeded"**
- Agent sending too many messages
- Adjust rate limits in gateway config
- Check for message loops

**"DLP blocked message"**
- Message contained PHI/PII patterns
- Review DLP policies
- Redact sensitive data before sending
- Check gateway audit log for details

## Next Steps

After running this example:

1. Review [GATEWAY.md](../../GATEWAY.md) for complete gateway documentation
2. Compare with chemistry_tutoring (P2P mode) to see the differences
3. Explore gateway configuration options
4. Review audit logs in Redis Streams
5. Test DLP by including PHI in messages
6. Experiment with rate limiting by sending rapid messages
7. Add more agents to see RBAC in action

## Related Documentation

- [GATEWAY.md](../../GATEWAY.md) - Complete gateway architecture
- [ARCHITECTURE.md](../../ARCHITECTURE.md) - P2P architecture comparison
- [Chemistry Tutoring](../chemistry_tutoring/) - P2P mode example
