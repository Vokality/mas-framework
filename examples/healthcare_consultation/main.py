"""Main entry point for healthcare consultation example (gateway mode)."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv

# Load .env from project root
project_root = Path(__file__).parent.parent.parent
dotenv_path = project_root / ".env"
load_dotenv(dotenv_path=dotenv_path)

from mas import MASService
from mas.gateway import GatewayService
from patient_agent import PatientAgent
from doctor_agent import DoctorAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
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
        logger.error("  2. Set environment variable: export OPENAI_API_KEY='your-key-here'")
        return
    
    logger.info("✓ Loaded OpenAI API key from .env file")
    
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    logger.info("="*60)
    logger.info("Healthcare Consultation Demo - GATEWAY MODE")
    logger.info("="*60)
    logger.info("")
    logger.info("Gateway Features Enabled:")
    logger.info("  ✓ Authentication - Token-based agent auth")
    logger.info("  ✓ Authorization - Role-based access control")
    logger.info("  ✓ Rate Limiting - Prevents abuse")
    logger.info("  ✓ DLP - Detects/blocks PHI/PII leakage")
    logger.info("  ✓ Audit Trail - Complete consultation log")
    logger.info("  ✓ Circuit Breakers - Failure isolation")
    logger.info("  ✓ At-least-once delivery - Redis Streams")
    logger.info("="*60)
    logger.info("")
    
    # Start MAS service
    service = MASService(redis_url=redis_url)
    await service.start()
    
    # Start Gateway service (required for gateway mode)
    logger.info("Starting Gateway Service...")
    gateway = GatewayService(
        redis_url=redis_url,
        rate_limit_per_minute=100,
        rate_limit_per_hour=1000,
        enable_dlp=True,  # Enable DLP for PHI/PII detection
        enable_priority_queue=False,
    )
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
    
    # Configure authorization before agents start
    logger.info("Configuring gateway authorization...")
    redis = gateway._redis
    if redis:
        # Add patient to doctor's allowed targets (ACL)
        await redis.sadd(f"agent:patient_jones:allowed_targets", "doctor_smith")
        # Add doctor to patient's allowed targets (for responses)
        await redis.sadd(f"agent:doctor_smith:allowed_targets", "patient_jones")
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
    logger.info("="*60)
    logger.info("Demo complete!")
    logger.info("All consultations logged in audit trail for compliance")
    logger.info("="*60)


if __name__ == "__main__":
    asyncio.run(main())
