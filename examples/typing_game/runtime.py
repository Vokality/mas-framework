"""Shared runtime utilities for starting and stopping the typing game system."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mas import MASService
from mas.gateway import (
    FeaturesSettings,
    GatewayService,
    GatewaySettings,
    RateLimitSettings,
    RedisSettings,
)

logger = logging.getLogger(__name__)

try:
    from .achievement_agent import AchievementAgent
    from .parent_agent import ParentAgent
    from .reward_agent import RewardAgent
    from .typing_game_agent import TypingGameAgent
except ImportError:
    from achievement_agent import AchievementAgent
    from parent_agent import ParentAgent
    from reward_agent import RewardAgent
    from typing_game_agent import TypingGameAgent


@dataclass(slots=True)
class GameSystemHandles:
    """Runtime handles for the typing game system."""

    mas_service: MASService
    gateway: GatewayService
    game_agent: TypingGameAgent
    reward_agent: RewardAgent
    achievement_agent: AchievementAgent
    parent_agent: ParentAgent


async def check_game_system_running(child_id: str, redis_url: str) -> bool:
    """Return True if the typing game system appears to be active."""
    import redis.asyncio as redis

    r = redis.from_url(redis_url, decode_responses=True)
    try:
        agent_data = await r.hgetall(f"agent:{child_id}")
        return bool(agent_data and agent_data.get("status") == "ACTIVE")
    finally:
        await r.aclose()


async def start_game_system(
    child_id: str, parent_id: str, redis_url: str
) -> GameSystemHandles:
    """Start MAS service, gateway, and all game agents."""
    logger.info("Starting MAS Service…")
    mas_service = MASService(redis_url=redis_url)
    await mas_service.start()
    logger.info("MAS Service started")

    logger.info("Starting Gateway Service…")
    gateway_settings = GatewaySettings(
        redis=RedisSettings(url=redis_url),
        rate_limit=RateLimitSettings(per_minute=200, per_hour=5000),
        features=FeaturesSettings(
            dlp=False,
            priority_queue=False,
            rbac=False,
            message_signing=False,
            circuit_breaker=True,
        ),
    )
    gateway = GatewayService(settings=gateway_settings)
    await gateway.start()
    logger.info("Gateway Service started")

    logger.info("Configuring authorization…")
    auth = gateway.auth_manager()
    await auth.allow_network(
        [child_id, f"{child_id}_rewards", f"{child_id}_achievements", parent_id]
    )
    logger.info("Authorization configured")

    logger.info("Creating agents…")
    game_agent = TypingGameAgent(child_id, redis_url=redis_url, use_gateway=True)
    reward_agent = RewardAgent(child_id, redis_url=redis_url, use_gateway=True)
    achievement_agent = AchievementAgent(
        child_id, redis_url=redis_url, use_gateway=True
    )
    parent_agent = ParentAgent(
        parent_id, child_id=child_id, redis_url=redis_url, use_gateway=True
    )

    game_agent.set_gateway(gateway)
    reward_agent.set_gateway(gateway)
    achievement_agent.set_gateway(gateway)
    parent_agent.set_gateway(gateway)

    logger.info("Starting agents…")
    await game_agent.start()
    logger.info("Game agent started: %s", game_agent.id)

    await reward_agent.start()
    logger.info("Reward agent started: %s", reward_agent.id)

    await achievement_agent.start()
    logger.info("Achievement agent started: %s", achievement_agent.id)

    await parent_agent.start()
    logger.info("Parent agent started: %s", parent_agent.id)

    return GameSystemHandles(
        mas_service=mas_service,
        gateway=gateway,
        game_agent=game_agent,
        reward_agent=reward_agent,
        achievement_agent=achievement_agent,
        parent_agent=parent_agent,
    )


async def stop_game_system(handles: GameSystemHandles) -> None:
    """Stop agents, gateway, and MAS service."""
    logger.info("Stopping typing game system…")
    await handles.game_agent.stop()
    await handles.reward_agent.stop()
    await handles.achievement_agent.stop()
    await handles.parent_agent.stop()
    await handles.gateway.stop()
    await handles.mas_service.stop()
    logger.info("Typing game system stopped")


async def configure_client_authorization(
    client_id: str,
    child_id: str,
    redis_url: str,
    gateway: GatewayService | None = None,
) -> GatewayService | None:
    """Allow a client agent to message the child agent; returns temporary gateway if created."""
    temporary_gateway: GatewayService | None = None
    auth_gateway = gateway
    if auth_gateway is None:
        gateway_settings = GatewaySettings(redis=RedisSettings(url=redis_url))
        temporary_gateway = GatewayService(settings=gateway_settings)
        await temporary_gateway.start()
        auth_gateway = temporary_gateway

    auth = auth_gateway.auth_manager()
    await auth.allow_bidirectional(client_id, child_id)
    logger.info("Authorization configured for %s ↔ %s", client_id, child_id)
    return temporary_gateway
