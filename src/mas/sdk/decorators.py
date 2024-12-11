from functools import wraps
from typing import Any, Callable, Dict, List, Set, Type, TypeVar
from .state import AgentStateModel


def agent(
    agent_id: str,
    capabilities: List[str] | Set[str] | None = None,
    metadata: Dict[str, Any] | None = None,
    state_model: Type[AgentStateModel] | None = None,
) -> Callable:
    """
    Decorator to configure an agent class with runtime properties.

    Args:
        agent_id: Unique identifier for the agent
        capabilities: List of capabilities the agent supports
        metadata: Additional metadata for the agent

    Raises:
        ValueError: If agent_id is empty or invalid
    """

    def decorator(cls: Type) -> Type:
        # Set agent properties as class attributes
        cls.agent_id = agent_id
        cls.capabilities = set(capabilities or [])
        cls.metadata = metadata or {}
        cls.state_model = state_model

        # Store original init
        original_init = cls.__init__

        @wraps(original_init)
        def wrapped_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)

        cls.__init__ = wrapped_init
        return cls

    return decorator
