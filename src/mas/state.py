"""Redis-backed state management."""

import json
import logging
from collections.abc import MutableMapping
from typing import Any, Mapping, TypeVar, Generic, cast

from pydantic import BaseModel

from .redis_types import AsyncRedisProtocol

logger = logging.getLogger(__name__)


StateType = TypeVar("StateType", bound=BaseModel | MutableMapping[str, Any])


class StateManager(Generic[StateType]):
    """Manages agent state with Redis persistence."""

    def __init__(
        self,
        agent_id: str,
        redis: AsyncRedisProtocol,
        state_model: type[StateType] | None = None,
    ) -> None:
        """
        Initialize state manager.

        Args:
            agent_id: Agent identifier
            redis: Redis client instance
            state_model: Optional Pydantic model for typed state.
                When None, state is managed as dict[str, Any].
        """
        self.agent_id = agent_id
        self.redis = redis
        self._state_model: type[StateType] | None = state_model
        self._state: StateType | None = None

    @property
    def state(self) -> StateType:
        """Get current state."""
        if self._state is None:
            raise RuntimeError(
                "State requested before initialization. Call load() before accessing state."
            )
        return self._state

    async def load(self) -> None:
        """Load state from Redis."""
        key = f"agent.state:{self.agent_id}"
        data = await self.redis.hgetall(key)  # type: ignore

        if data:
            if self._state_model is None:
                # Dict state (values stored as strings)
                self._state = cast(StateType, data)
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
            if self._state_model is None:
                self._state = cast(StateType, {})
            else:
                self._state = self._state_model()

    async def update(self, updates: Mapping[str, Any]) -> None:
        """
        Update state and persist to Redis.

        Args:
            updates: Dictionary of state updates
        """
        current_state = self.state

        if isinstance(current_state, BaseModel):
            # Pydantic model
            for key, value in updates.items():
                setattr(current_state, key, value)
            state_dict = current_state.model_dump()
        else:
            # Dict
            dict_state = cast(MutableMapping[str, Any], current_state)
            dict_state.update(updates)
            state_dict = dict(dict_state)

        # Convert all values to strings for Redis
        redis_data: dict[str, str] = {}
        for key, value in state_dict.items():
            if isinstance(value, (dict, list)):
                redis_data[key] = json.dumps(value)
            else:
                redis_data[key] = str(value)

        # Persist to Redis
        key = f"agent.state:{self.agent_id}"
        await self.redis.hset(key, mapping=redis_data)

    async def reset(self) -> None:
        """Reset state to defaults."""
        if self._state_model is None:
            self._state = cast(StateType, {})
        else:
            self._state = self._state_model()

        # Clear from Redis
        key = f"agent.state:{self.agent_id}"
        await self.redis.delete(key)
