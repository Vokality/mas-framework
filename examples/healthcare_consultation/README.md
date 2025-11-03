# Healthcare Consultation Example - Gateway Mode

This example demonstrates the **Gateway Mode** architecture with three OpenAI-powered agents in a realistic healthcare consultation workflow with full security and compliance features.

## Architecture: Gateway Mode (3-Agent System)

```
Patient Agent → Gateway → GP Doctor Agent → Gateway → Specialist Agent
                 ↓                            ↓
           (auth, authz,                (auth, authz,
            rate limit,                  rate limit,
            DLP, audit)                  DLP, audit)
                                             ↓
                                      Specialist → Gateway → GP → Gateway → Patient
```

**Message Flow:**
1. Patient asks health question → GP Doctor
2. GP provides initial diagnosis → Consults Specialist
3. Specialist provides expert advice → GP
4. GP synthesizes final advice → Patient

## Agent Roles and Workflow

### PatientAgent
- **Role**: Asks health-related questions
- **Communication**: Sends questions to GP Doctor
- **Capabilities**: `healthcare_patient`, `question_asker`

### DoctorAgent (General Practitioner)
- **Role**: Provides initial diagnosis and coordinates with specialist
- **Communication**: 
  - Receives questions from Patient
  - Consults with Specialist for expert guidance
  - Synthesizes final advice for Patient
- **Capabilities**: `healthcare_doctor`, `medical_advisor`
- **Workflow**:
  1. Receives patient question
  2. Generates initial diagnosis/assessment
  3. Forwards question + diagnosis to specialist
  4. Receives specialist's expert advice
  5. Synthesizes comprehensive final advice
  6. Sends final advice to patient

### SpecialistAgent
- **Role**: Provides expert medical advice in specific domain
- **Communication**: 
  - Receives consultation requests from GP Doctor
  - Sends expert advice back to GP Doctor
- **Capabilities**: `healthcare_specialist`, `specialist_<specialization>`
- **Specialization**: Configurable (cardiology, neurology, endocrinology, etc.)

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
| **Agent Count** | 2 agents | 3 agents (multi-tier) |
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
3. **Specialist Agent** starts and registers (capability: `healthcare_specialist`)
4. **GP Doctor Agent** starts and discovers specialist (capability: `healthcare_doctor`)
5. **Patient Agent** starts and discovers GP doctor
6. Patient asks 3 health questions about wellness
7. For each question, the following workflow occurs:
   - Patient → GP Doctor (via gateway)
     - ✓ Authentication validated
     - ✓ Authorization checked (RBAC)
     - ✓ Rate limit enforced
     - ✓ DLP scan performed
     - ✓ Audit log entry created
   - GP generates initial diagnosis
   - GP → Specialist (via gateway with same security checks)
   - Specialist provides expert medical advice
   - Specialist → GP (via gateway)
   - GP synthesizes final advice combining both perspectives
   - GP → Patient (via gateway) with comprehensive guidance
8. Gateway logs complete consultation trail with all agent interactions

**Expected runtime:** ~90 seconds for 3 consultations with specialist involvement

## Sample Output

```
2024-11-03 12:00:00 - INFO - ============================================================
Healthcare Consultation Demo - GATEWAY MODE
3-Agent System: Patient → GP Doctor → Specialist
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

2024-11-03 12:00:02 - INFO - Specialist agent specialist_dr_chen started (GATEWAY MODE)
2024-11-03 12:00:02 - INFO - Specialization: cardiology
2024-11-03 12:00:02 - INFO - HIPAA-compliant: All messages audited and DLP-scanned

2024-11-03 12:00:02 - INFO - Doctor agent doctor_smith started (GATEWAY MODE)
2024-11-03 12:00:02 - INFO - Found specialist: specialist_dr_chen
2024-11-03 12:00:02 - INFO - HIPAA-compliant: All messages audited and DLP-scanned

2024-11-03 12:00:03 - INFO - Patient agent patient_jones started (GATEWAY MODE)
2024-11-03 12:00:03 - INFO - Security features: Auth, RBAC, Rate Limiting, DLP, Audit

============================================================
PATIENT'S QUESTION #1:
I'm interested in improving my overall wellness. What are the most 
important preventive health measures I should be taking for heart health?
============================================================

2024-11-03 12:00:05 - INFO - ✓ Message sent through gateway (auth, audit, DLP applied)

============================================================
CONSULTATION REQUEST from patient_jones
Question: I'm interested in improving my overall wellness...
Concern: general wellness and preventive care
============================================================
2024-11-03 12:00:05 - INFO - ✓ Gateway validated: auth, authz, rate limit, DLP passed

============================================================
GP'S INITIAL DIAGNOSIS:
That's a great question about preventive care. From a general 
practitioner's perspective, the foundation of wellness includes 
regular exercise, a balanced diet, adequate sleep, and stress 
management. For your specific concern about heart health, I'd 
recommend consulting with our cardiology specialist for expert 
guidance on cardiovascular preventive measures.
============================================================

2024-11-03 12:00:06 - INFO - Consulting specialist specialist_dr_chen...
2024-11-03 12:00:06 - INFO - ✓ Specialist consultation request sent

============================================================
SPECIALIST CONSULTATION REQUEST from doctor_smith
Patient: patient_jones
Concern: general wellness and preventive care
Patient Question: I'm interested in improving my overall wellness...
GP Diagnosis: That's a great question about preventive care...
============================================================
2024-11-03 12:00:06 - INFO - ✓ Gateway validated: auth, authz, rate limit, DLP passed

============================================================
SPECIALIST'S EXPERT ADVICE:
Your GP's assessment is excellent. From a cardiology perspective, 
I'd emphasize that cardiovascular health is crucial for overall 
wellness. Key preventive measures include: 1) Regular aerobic 
exercise (150 minutes weekly), 2) Mediterranean-style diet rich 
in omega-3 fatty acids, 3) Blood pressure monitoring, 4) Lipid 
panel screening, and 5) Stress management. I recommend the patient 
schedule regular cardiovascular risk assessments, especially if 
there's family history of heart disease.
============================================================

============================================================
SPECIALIST RESPONSE RECEIVED (cardiology)
============================================================

============================================================
FINAL ADVICE TO PATIENT:
Thank you for asking about preventive wellness measures, particularly 
for heart health. After consulting with our cardiology specialist, I 
can provide you with comprehensive guidance.

The foundation of preventive care includes maintaining a healthy 
lifestyle with regular exercise (aim for 150 minutes of aerobic 
activity weekly), a balanced Mediterranean-style diet rich in fruits, 
vegetables, whole grains, and omega-3 fatty acids, adequate sleep 
(7-9 hours), and effective stress management.

Specifically for cardiovascular health, which the specialist 
emphasizes is crucial for overall wellness, it's important to 
monitor your blood pressure regularly and get periodic lipid panel 
screenings. These help catch potential issues early.

I recommend scheduling regular cardiovascular risk assessments, 
especially important if you have a family history of heart disease. 
Together, we can create a personalized preventive care plan tailored 
to your specific health profile and risk factors.
============================================================

[... continues for 2 more questions ...]

============================================================
Demo complete!
All consultations logged in audit trail for compliance
Flow: Patient → GP → Specialist → GP → Patient
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

### Change Specialist Type
Edit `main.py` to change the specialist's area of expertise:
```python
specialist = SpecialistAgent(
    agent_id="specialist_dr_lee",
    redis_url=redis_url,
    openai_api_key=api_key,
    specialization="endocrinology",  # or "neurology", "oncology", etc.
)
```

### Change Health Topic
Edit `patient_agent.py` line ~51:
```python
self.current_concern = "managing chronic conditions"
```

### Adjust Number of Questions
Edit `patient_agent.py` line ~50:
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
