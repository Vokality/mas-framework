"""Main entry point for chemistry tutoring example."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env from project root (two directories up from this file)
project_root = Path(__file__).parent.parent.parent
dotenv_path = project_root / ".env"
load_dotenv(dotenv_path=dotenv_path)

from mas import MASService  # noqa: E402
from student_agent import StudentAgent  # noqa: E402
from professor_agent import ProfessorAgent  # noqa: E402

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the chemistry tutoring demo."""
    # Check for OpenAI API key (loaded from .env file or environment)
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

    logger.info("Starting Chemistry Tutoring Demo")
    logger.info("=" * 60)

    # Start MAS service
    service = MASService(redis_url=redis_url)
    await service.start()

    # Create agents
    professor = ProfessorAgent(
        agent_id="professor_chen",
        redis_url=redis_url,
        openai_api_key=api_key,
    )

    student = StudentAgent(
        agent_id="student_alex",
        redis_url=redis_url,
        openai_api_key=api_key,
    )

    try:
        # Start agents (professor first so student can discover them)
        await professor.start()
        await student.start()

        # Let the conversation run (student will ask 3 questions)
        # Wait for conversation to complete (roughly 30 seconds for 3 Q&As)
        await asyncio.sleep(60)

    except KeyboardInterrupt:
        logger.info("\nShutting down...")
    finally:
        # Cleanup
        await student.stop()
        await professor.stop()
        await service.stop()

    logger.info("Demo complete!")


if __name__ == "__main__":
    asyncio.run(main())
