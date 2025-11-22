"""Launch the typing game Textual TUI (starts everything needed)."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from runtime import (
    GameSystemHandles,
    check_game_system_running,
    start_game_system,
    stop_game_system,
)
from ui import TypingGameUI

# Configure logging to file
log_file = Path(__file__).parent / "typing_game.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),  # Also print to console
    ],
)

logger = logging.getLogger(__name__)


async def simple_typing_client():
    """Launch the Textual TUI for the typing game."""
    child_id = os.getenv("CHILD_ID", "child_alex")
    parent_id = os.getenv("PARENT_ID", "parent")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

    print("=" * 60)
    print("Typing Game - Textual Interface")
    print("=" * 60)
    print(f"\nChild ID: {child_id}")
    print("A rich terminal UI will launch. Press 'q' to exit.\n")

    # Check if game system is running
    print("Checking if game system is running...")
    game_running = await check_game_system_running(child_id, redis_url)
    logger.info(f"Game system running check: {game_running}")

    game_system: GameSystemHandles | None = None
    started_system = False
    if not game_running:
        print("Game system not running. Starting it now...")
        logger.info("Starting game system...")
        try:
            game_system = await start_game_system(child_id, parent_id, redis_url)
            print("✓ Game system started")
            logger.info("Game system started successfully")
            started_system = True
        except Exception as e:
            print(f"✗ Failed to start game system: {e}")
            logger.error(f"Failed to start game system: {e}", exc_info=True)
            return
    else:
        print("✓ Game system already running")
        logger.info("Game system already running, skipping startup")

    app: TypingGameUI | None = None
    try:
        print("Launching Textual UI…\n")
        app = TypingGameUI(
            child_id=child_id,
            parent_id=parent_id,
            redis_url=redis_url,
            system_handles=game_system,
            started_system=started_system,
        )
        try:
            await app.run_async()
        except KeyboardInterrupt:
            print("\nKeyboard interrupt received. Closing UI…")
        finally:
            await app.shutdown_resources()
        print("\n✓ Textual UI closed")
    finally:
        if started_system and game_system:
            print("\nStopping game system...")
            await stop_game_system(game_system)
            print("✓ Game system stopped")
        print("\nThanks for playing!")


if __name__ == "__main__":
    asyncio.run(simple_typing_client())
