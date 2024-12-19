from typing import Any, Callable, Dict, List, Set, Type, TypeVar

from .state import AgentState

T = TypeVar("T")


def agent(
    agent_id: str,
    capabilities: List[str] | Set[str] | None = None,
    metadata: Dict[str, Any] | None = None,
    state_model: Type[AgentState] | None = None,
) -> Callable[[Type[T]], Type[T]]:
    """
    Decorator to configure an agent class with runtime properties.

    Args:
        agent_id: Unique identifier for the agent
        capabilities: List of capabilities the agent supports
        metadata: Additional metadata for the agent
        state_model: Optional custom state model class

    Raises:
        ValueError: If agent_id is empty or invalid

    Returns:
        A decorator function that configures the agent class
    """
    if not agent_id:
        raise ValueError("agent_id must not be empty")

    def decorator(cls: Type[T]) -> Type[T]:
        # Set agent properties as class attributes
        cls.agent_id = agent_id  # type: ignore
        cls.capabilities = set(capabilities or [])  # type: ignore
        cls.metadata = metadata or {}  # type: ignore
        cls.state_model = state_model or AgentState  # type: ignore

        return cls

    return decorator
