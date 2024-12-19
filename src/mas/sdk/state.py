import asyncio
from typing import Any, Awaitable, Callable, Dict, Generic, List, TypeVar, Union

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Base model for agent state"""

    data: Dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=0)
    last_updated: float = Field(default_factory=lambda: asyncio.get_event_loop().time())

    class Config:
        arbitrary_types_allowed = True

    def update(self, data: Dict[str, Any]) -> "AgentState":
        """Create a new state instance with updated data"""
        return self.model_copy(
            update={
                "data": {**self.data, **data},
                "version": self.version + 1,
                "last_updated": asyncio.get_event_loop().time(),
            }
        )

    def reset(self) -> "AgentState":
        """Reset state to initial values"""
        return self.__class__()


T = TypeVar("T", bound=AgentState)
StateCallback = Union[Callable[[T], None], Callable[[T], Awaitable[None]]]


class StateManager(Generic[T]):
    """Simple state manager for agents"""

    def __init__(self, state_model: type[T]) -> None:
        self._state: T = state_model()
        self._state_model: type[T] = state_model
        self._lock: asyncio.Lock = asyncio.Lock()
        self._subscribers: List[StateCallback[T]] = []

    @property
    def state(self) -> T:
        """Get current state"""
        return self._state

    async def update(self, data: Dict[str, Any]) -> None:
        """Update state with new data"""
        async with self._lock:
            self._state = self._state.update(data)  # type: ignore
            await self._notify_subscribers()

    async def reset(self) -> None:
        """Reset state to initial values"""
        async with self._lock:
            self._state = self._state.reset()  # type: ignore
            await self._notify_subscribers()

    def subscribe(self, callback: StateCallback[T]) -> None:
        """Subscribe to state changes"""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: StateCallback[T]) -> None:
        """Unsubscribe from state changes"""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _notify_subscribers(self) -> None:
        """Notify subscribers of state changes"""
        for subscriber in self._subscribers:
            if asyncio.iscoroutinefunction(subscriber):
                await subscriber(self._state)
            else:
                subscriber(self._state)
