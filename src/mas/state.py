"""Redis-backed state management."""

import json
import logging
from typing import Any
from redis.asyncio import Redis
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class StateManager:
    """Manages agent state with Redis persistence."""

    def __init__(
        self, agent_id: str, redis: Redis, state_model: type[BaseModel] | None = None
    ):
        """
        Initialize state manager.

        Args:
            agent_id: Agent identifier
            redis: Redis client instance
            state_model: Optional Pydantic model for typed state
        """
        self.agent_id = agent_id
        self.redis = redis
        self._state_model = state_model or dict
        self._state: Any = None

    @property
    def state(self) -> Any:
        """Get current state."""
        return self._state

    async def load(self) -> None:
        """Load state from Redis."""
        key = f"agent.state:{self.agent_id}"
        data = await self.redis.hgetall(key)  # type: ignore

        if data:
            if self._state_model is dict:
                self._state = data
            else:
                # Pydantic model - convert string values to proper types
                try:
                    self._state = self._state_model(**data)
                except Exception as e:
                    logger.warning(
                        "Failed to load state from Redis, using defaults",
                        extra={"agent_id": self.agent_id, "error": str(e)},
                    )
                    self._state = self._state_model()
        else:
            # Initialize with defaults
            if self._state_model is dict:
                self._state = {}
            else:
                self._state = self._state_model()

    async def update(self, updates: dict) -> None:
        """
        Update state and persist to Redis.

        Args:
            updates: Dictionary of state updates
        """
        if isinstance(self._state, BaseModel):
            # Pydantic model
            for key, value in updates.items():
                setattr(self._state, key, value)
            state_dict = self._state.model_dump()
        else:
            # Dict
            self._state.update(updates)
            state_dict = self._state

        # Convert all values to strings for Redis
        redis_data = {}
        for key, value in state_dict.items():
            if isinstance(value, (dict, list)):
                redis_data[key] = json.dumps(value)
            else:
                redis_data[key] = str(value)

        # Persist to Redis
        key = f"agent.state:{self.agent_id}"
        await self.redis.hset(key, mapping=redis_data)  # type: ignore

    async def reset(self) -> None:
        """Reset state to defaults."""
        if self._state_model is dict:
            self._state = {}
        else:
            self._state = self._state_model()

        # Clear from Redis
        key = f"agent.state:{self.agent_id}"
        await self.redis.delete(key)  # type: ignore
