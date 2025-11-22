"""Main entry point for typing game."""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

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
logger.info("=" * 60)
logger.info("Typing Game System Starting")
logger.info("=" * 60)

from mas import MASService
from mas.gateway import (
    GatewayService,
    GatewaySettings,
    RateLimitSettings,
    FeaturesSettings,
    RedisSettings,
)

from typing_game_agent import TypingGameAgent
from reward_agent import RewardAgent
from parent_agent import ParentAgent
from achievement_agent import AchievementAgent


async def main():
    """Run the typing game system."""
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    child_id = os.getenv("CHILD_ID", "child_alex")
    parent_id = os.getenv("PARENT_ID", "parent")

    print("=" * 60)
    print("Typing Game for 4-Year-Olds")
    print("Reward-Driven Learning System")
    print("=" * 60)

    # Start MAS Service
    print("\nStarting MAS Service...")
    mas_service = MASService(redis_url=redis_url)
    await mas_service.start()
    print("✓ MAS Service started")

    # Start Gateway Service (for security)
    print("\nStarting Gateway Service...")
    gateway_settings = GatewaySettings(
        redis=RedisSettings(url=redis_url),
        rate_limit=RateLimitSettings(per_minute=200, per_hour=5000),
        features=FeaturesSettings(
            dlp=False,  # Not needed for typing game
            priority_queue=False,
            rbac=False,  # Use simple ACL
            message_signing=False,  # Simplified for demo
            circuit_breaker=True,
        ),
    )
    gateway = GatewayService(settings=gateway_settings)
    await gateway.start()
    print("✓ Gateway Service started")

    # Configure authorization (allow agents to communicate)
    print("\nConfiguring authorization...")
    auth = gateway.auth_manager()
    # Allow all agents to communicate with each other
    # Note: UI clients will set up their own authorization when they connect
    await auth.allow_network(
        [child_id, f"{child_id}_rewards", f"{child_id}_achievements", parent_id]
    )
    print("✓ Authorization configured")
    print("  (UI clients will configure their own authorization when connecting)")

    # Create agents
    print(f"\nCreating agents for {child_id}...")

    game_agent = TypingGameAgent(child_id, redis_url=redis_url, use_gateway=True)
    reward_agent = RewardAgent(child_id, redis_url=redis_url, use_gateway=True)
    achievement_agent = AchievementAgent(
        child_id, redis_url=redis_url, use_gateway=True
    )
    parent_agent = ParentAgent(
        parent_id, child_id=child_id, redis_url=redis_url, use_gateway=True
    )

    # Set gateway for agents (must be done after creation, before start)
    game_agent.set_gateway(gateway)
    reward_agent.set_gateway(gateway)
    achievement_agent.set_gateway(gateway)
    parent_agent.set_gateway(gateway)

    # Start all agents
    print("\nStarting agents...")
    await game_agent.start()
    print(f"✓ Game agent started: {game_agent.id}")

    await reward_agent.start()
    print(f"✓ Reward agent started: {reward_agent.id}")

    await achievement_agent.start()
    print(f"✓ Achievement agent started: {achievement_agent.id}")

    await parent_agent.start()
    print(f"✓ Parent agent started: {parent_agent.id}")

    print("\n" + "=" * 60)
    print("Game System Ready!")
    print("=" * 60)
    print(f"\nChild ID: {child_id}")
    print(f"Parent ID: {parent_id}")
    print("\nAgents are running and ready to play!")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    try:
        # Keep running
        await asyncio.sleep(3600)  # Run for 1 hour
    except KeyboardInterrupt:
        print("\n\nStopping agents...")

    # Cleanup
    await game_agent.stop()
    await reward_agent.stop()
    await achievement_agent.stop()
    await parent_agent.stop()
    await gateway.stop()
    await mas_service.stop()

    print("✓ All agents stopped")


if __name__ == "__main__":
    asyncio.run(main())
