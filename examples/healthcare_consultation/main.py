"""Main entry point for healthcare consultation example (gateway mode)."""

import asyncio
import logging
import os
import sys
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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


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

    # Create agents (both with use_gateway=True)
    doctor = DoctorAgent(
        agent_id="doctor_smith",
        redis_url=redis_url,
        openai_api_key=api_key,
    )

    patient = PatientAgent(
        agent_id="patient_jones",
        redis_url=redis_url,
        openai_api_key=api_key,
    )

    # Configure agents to use the gateway
    doctor.set_gateway(gateway)
    patient.set_gateway(gateway)
    logger.info("✓ Agents configured to use gateway")

    # Configure authorization before agents start (using high-level API)
    logger.info("Configuring gateway authorization...")
    auth = gateway.auth_manager()
    await auth.allow_bidirectional("patient_jones", "doctor_smith")
    logger.info("✓ Authorization configured: patient ↔ doctor communication allowed")
    logger.info("")

    try:
        # Start agents (doctor first so patient can discover)
        await doctor.start()
        await patient.start()

        # Let the consultation run (patient will ask 3 questions)
        # Each message goes through full gateway validation
        logger.info("Consultation in progress...")
        logger.info("(Each message: auth → authz → rate limit → DLP → audit → deliver)")
        logger.info("")

        await asyncio.sleep(60)  # ~60 seconds for 3 Q&As

    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        # Cleanup
        logger.info("")
        logger.info("Stopping agents and services...")
        await patient.stop()
        await doctor.stop()
        await gateway.stop()
        await service.stop()

    logger.info("")
    logger.info("=" * 60)
    logger.info("Demo complete!")
    logger.info("All consultations logged in audit trail for compliance")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
