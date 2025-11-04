"""Main entry point for healthcare consultation example (gateway mode)."""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env from project root
project_root = Path(__file__).parent.parent.parent
dotenv_path = project_root / ".env"
load_dotenv(dotenv_path=dotenv_path)

from mas import MASService  # noqa: E402
from mas.gateway import GatewayService  # noqa: E402
from mas.gateway.config import GatewaySettings, FeaturesSettings, RateLimitSettings  # noqa: E402
from patient_agent import PatientAgent  # noqa: E402
from doctor_agent import DoctorAgent  # noqa: E402
from specialist_agent import SpecialistAgent  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


def format_timestamp(timestamp: float) -> str:
    """Format Unix timestamp to readable datetime."""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


async def display_audit_logs(gateway: GatewayService) -> None:
    """Display audit log details at the end of the run."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("AUDIT LOG REPORT")
    logger.info("=" * 60)
    logger.info("")

    # Get audit module - access via private attribute if method has issues
    try:
        if not gateway._running:  # type: ignore[attr-defined]
            logger.warning("Gateway not running, cannot access audit logs")
            return
        audit = gateway._audit  # type: ignore[attr-defined]
        if audit is None:
            logger.warning("Audit module not initialized")
            return
    except Exception as e:
        logger.error(f"Failed to get audit module: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return

    # Get statistics
    stats = await audit.get_stats()
    logger.info("Audit Statistics:")
    logger.info(f"  Total Messages: {stats.get('total_messages', 0)}")
    logger.info(f"  Security Events: {stats.get('security_events', 0)}")
    logger.info("")

    # Query all audit entries
    entries = await audit.query_all(count=1000)

    if not entries:
        logger.info("No audit entries found.")
        logger.info("")
        return

    logger.info(f"Found {len(entries)} audit entries")
    logger.info("")

    # Group by decision
    decisions: dict[str, int] = {}
    violations: list[str] = []
    agent_activity: dict[str, int] = {}

    for entry in entries:
        # Count decisions
        decision = entry.get("decision", "UNKNOWN")
        decisions[decision] = decisions.get(decision, 0) + 1

        # Collect violations
        entry_violations = entry.get("violations", [])
        if isinstance(entry_violations, list):
            violations.extend(entry_violations)
        elif isinstance(entry_violations, str):
            # Try to parse JSON string
            try:
                parsed = json.loads(entry_violations)
                if isinstance(parsed, list):
                    violations.extend(parsed)
            except (json.JSONDecodeError, TypeError):
                pass

        # Track agent activity
        sender = entry.get("sender_id", "unknown")
        target = entry.get("target_id", "unknown")
        agent_activity[sender] = agent_activity.get(sender, 0) + 1
        agent_activity[target] = agent_activity.get(target, 0) + 1

    # Display summary
    logger.info("Summary by Decision:")
    for decision, count in sorted(decisions.items()):
        logger.info(f"  {decision}: {count}")
    logger.info("")

    # Display violations
    if violations:
        violation_counts: dict[str, int] = {}
        for violation in violations:
            violation_counts[violation] = violation_counts.get(violation, 0) + 1

        logger.info("Policy Violations Detected:")
        for violation, count in sorted(violation_counts.items()):
            logger.info(f"  {violation}: {count}")
        logger.info("")
    else:
        logger.info("No policy violations detected.")
        logger.info("")

    # Display agent activity
    logger.info("Agent Activity (messages sent/received):")
    for agent, count in sorted(agent_activity.items()):
        logger.info(f"  {agent}: {count}")
    logger.info("")

    # Display detailed entries
    logger.info("Detailed Audit Entries:")
    logger.info("-" * 60)

    for i, entry in enumerate(entries[:20], 1):  # Show first 20 entries
        message_id = entry.get("message_id", "unknown")
        sender = entry.get("sender_id", "unknown")
        target = entry.get("target_id", "unknown")
        decision = entry.get("decision", "UNKNOWN")
        timestamp = entry.get("timestamp", 0)
        latency = entry.get("latency_ms", 0)
        entry_violations = entry.get("violations", [])

        # Format violations
        if isinstance(entry_violations, str):
            try:
                entry_violations = json.loads(entry_violations)
            except (json.JSONDecodeError, TypeError):
                entry_violations = []

        violation_str = ", ".join(entry_violations) if entry_violations else "None"

        logger.info(f"Entry {i}:")
        logger.info(f"  Message ID: {message_id}")
        logger.info(f"  From: {sender} → To: {target}")
        logger.info(f"  Decision: {decision}")
        # Format timestamp - handle both float and string
        try:
            ts = float(timestamp) if timestamp else 0
            ts_str = format_timestamp(ts) if ts > 0 else "N/A"
        except (ValueError, TypeError):
            ts_str = str(timestamp) if timestamp else "N/A"
        logger.info(f"  Timestamp: {ts_str}")
        # Format latency - handle both float and string
        try:
            lat = float(latency) if latency else 0
            logger.info(f"  Latency: {lat:.2f}ms")
        except (ValueError, TypeError):
            logger.info(f"  Latency: {latency}ms")
        logger.info(f"  Violations: {violation_str}")
        logger.info("")

    if len(entries) > 20:
        logger.info(
            f"... and {len(entries) - 20} more entries (use audit.query_all() to see all)"
        )
        logger.info("")

    # Display security events
    security_events = await audit.query_security_events(count=50)
    if security_events:
        logger.info("Security Events:")
        logger.info("-" * 60)
        for event in security_events[:10]:  # Show first 10 events
            event_type = event.get("event_type", "UNKNOWN")
            timestamp = event.get("timestamp", 0)
            details = event.get("details", {})

            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (json.JSONDecodeError, TypeError):
                    details = {}

            # Format timestamp - handle both float and string
            try:
                ts = float(timestamp) if timestamp else 0
                ts_str = format_timestamp(ts) if ts > 0 else "N/A"
            except (ValueError, TypeError):
                ts_str = str(timestamp) if timestamp else "N/A"
            logger.info(f"  [{ts_str}] {event_type}")
            if isinstance(details, dict):
                for key, value in details.items():
                    logger.info(f"    {key}: {value}")
        logger.info("")

    logger.info("=" * 60)
    logger.info("")


async def main() -> None:
    """Run the healthcare consultation demo with gateway mode."""
    # Check for OpenAI API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY not found!")
        logger.error("Please either:")
        logger.error("  1. Add OPENAI_API_KEY to .env file in project root")
        logger.error(
            "  2. Set environment variable: export OPENAI_API_KEY='your-key-here'"
        )
        return

    logger.info("✓ Loaded OpenAI API key from .env file")

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    logger.info("=" * 60)
    logger.info("Healthcare Consultation Demo - GATEWAY MODE")
    logger.info("3-Agent System: Patient → GP Doctor → Specialist")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Gateway Features Enabled:")
    logger.info("  ✓ Authentication - Token-based agent auth")
    logger.info("  ✓ Authorization - Role-based access control")
    logger.info("  ✓ Rate Limiting - Prevents abuse")
    logger.info("  ✓ DLP - Detects/blocks PHI/PII leakage")
    logger.info("  ✓ Audit Trail - Complete consultation log")
    logger.info("  ✓ Circuit Breakers - Failure isolation")
    logger.info("  ✓ At-least-once delivery - Redis Streams")
    logger.info("=" * 60)
    logger.info("")

    # Start MAS service
    service = MASService(redis_url=redis_url)
    await service.start()

    # Start Gateway service (required for gateway mode)
    logger.info("Starting Gateway Service...")

    # Configure gateway with example-friendly settings
    gateway_settings = GatewaySettings(
        rate_limit=RateLimitSettings(per_minute=100, per_hour=1000),
        features=FeaturesSettings(
            dlp=True,  # Enable DLP for PHI/PII detection
            priority_queue=False,
            rbac=False,  # Use simple ACL for this example
            message_signing=False,  # Simplified for demo
            circuit_breaker=True,
        ),
    )
    gateway = GatewayService(settings=gateway_settings)
    await gateway.start()
    logger.info("✓ Gateway Service started")
    logger.info("")

    # Create agents (all with use_gateway=True)
    doctor = DoctorAgent(
        agent_id="doctor_smith",
        redis_url=redis_url,
        openai_api_key=api_key,
    )

    specialist = SpecialistAgent(
        agent_id="specialist_dr_chen",
        redis_url=redis_url,
        openai_api_key=api_key,
        specialization="cardiology",  # Can be any specialization
    )

    patient = PatientAgent(
        agent_id="patient_jones",
        redis_url=redis_url,
        openai_api_key=api_key,
    )

    # Configure agents to use the gateway
    doctor.set_gateway(gateway)
    specialist.set_gateway(gateway)
    patient.set_gateway(gateway)
    logger.info("✓ Agents configured to use gateway")

    # Configure authorization before agents start (using high-level API)
    logger.info("Configuring gateway authorization...")
    auth = gateway.auth_manager()
    await auth.allow_bidirectional("patient_jones", "doctor_smith")
    await auth.allow_bidirectional("doctor_smith", "specialist_dr_chen")
    logger.info("✓ Authorization configured:")
    logger.info("  - patient ↔ doctor communication allowed")
    logger.info("  - doctor ↔ specialist communication allowed")
    logger.info("")

    try:
        # Start agents (specialist and doctor first so patient can discover)
        await specialist.start()
        await doctor.start()
        await patient.start()

        # Let the consultation run (patient will ask 3 questions)
        # Each message goes through full gateway validation
        # Flow: Patient → GP → Specialist → GP → Patient
        logger.info("Consultation in progress...")
        logger.info("(Message flow: Patient → GP → Specialist → GP → Patient)")
        logger.info("(Each message: auth → authz → rate limit → DLP → audit → deliver)")
        logger.info("")

        await asyncio.sleep(90)  # ~90 seconds for 3 Q&As with specialist consultations

    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        # Display audit logs before stopping gateway
        try:
            await display_audit_logs(gateway)
        except Exception as e:
            logger.warning(f"Failed to display audit logs: {e}")

        # Cleanup
        logger.info("")
        logger.info("Stopping agents and services...")
        await patient.stop()
        await doctor.stop()
        await specialist.stop()
        await gateway.stop()
        await service.stop()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Demo complete!")
    logger.info("All consultations logged in audit trail for compliance")
    logger.info("Flow: Patient → GP → Specialist → GP → Patient")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
