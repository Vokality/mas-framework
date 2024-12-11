import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, Generic, Optional, TypeVar

from pydantic import BaseModel, Field
from typing_extensions import override

T = TypeVar("T", bound=BaseModel)

State = TypeVar("State", bound="BaseStateModel")
Action = TypeVar("Action", bound="BaseAction")


class StateError(Exception):
    """Base exception for state-related errors"""

    pass


class ActionType(str, Enum):
    """Base class for action types"""

    RESET = "RESET"
    UPDATE = "UPDATE"


class BaseStateModel(BaseModel):
    """Base model for all state models"""

    version: int = Field(default=0, description="State version number")
    last_updated: float = Field(default_factory=lambda: asyncio.get_event_loop().time())

    class Config:
        arbitrary_types_allowed = True


class BaseAction(BaseModel, Generic[State]):
    """Base class for all actions"""

    type: ActionType
    payload: Optional[Dict[str, Any]] = None

    def reduce(self, state: State) -> State:
        """Default reducer implementation"""
        raise NotImplementedError


class StateStore(Generic[State, Action]):
    """Redux-like state store with async support"""

    def __init__(
        self,
        initial_state: State,
        reducer: Callable[[State, Action], State],
        middleware: Optional[list[Callable]] = None,
    ) -> None:
        self._state = initial_state
        self._reducer = reducer
        self._middleware = middleware or []
        self._subscribers: list[Callable[[State], None]] = []
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        """Get current state"""
        return self._state

    async def dispatch(self, action: Action) -> None:
        """Dispatch an action to update state"""
        async with self._lock:
            # Apply middleware
            for middleware in self._middleware:
                action = await middleware(self, action)

            # Update state
            new_state = self._reducer(self._state, action)
            new_state.version += 1
            new_state.last_updated = asyncio.get_event_loop().time()
            self._state = new_state

            # Notify subscribers
            for subscriber in self._subscribers:
                if asyncio.iscoroutinefunction(subscriber):
                    await subscriber(new_state)
                else:
                    subscriber(new_state)

    def subscribe(self, callback: Callable[[State], None]) -> Callable[[], None]:
        """Subscribe to state changes"""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            self._subscribers.remove(callback)

        return unsubscribe


class AgentStateModel(BaseStateModel):
    """Agent state model with validation"""

    data: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


class GlobalStateModel(BaseStateModel):
    """Global state model with validation"""

    shared_data: Dict[str, Any] = Field(default_factory=dict)
    agents: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class StateProvider(ABC, Generic[T]):
    """Interface for state providers"""

    @abstractmethod
    async def load(self) -> T:
        """Load state"""
        pass

    @abstractmethod
    async def save(self, state: T) -> None:
        """Save state"""
        pass


class InMemoryStateProvider(StateProvider[T]):
    """In-memory state provider."""

    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, Any]] = {}

    async def get(self, namespace: str) -> Dict[str, T] | None:
        """Load state from memory."""
        return self._state.get(namespace)

    async def set(self, namespace: str, state: Dict[str, Dict[str, Any]]) -> None:
        """Save state to memory."""
        self._state[namespace] = state


class PersistentStateProvider(StateProvider[T]):
    """Persistent state provider using persistence layer"""

    def __init__(
        self,
        persistence: InMemoryStateProvider,
        namespace: str,
        model_class: type[T],
    ) -> None:
        self.persistence = persistence
        self.namespace = namespace
        self.model_class = model_class

    @override
    async def load(self) -> T:
        data = await self.persistence.get(self.namespace)
        if data is None:
            return self.model_class()
        return self.model_class.model_validate(data)

    @override
    async def save(self, state: T) -> None:
        await self.persistence.set(self.namespace, state.model_dump())


class AgentAction(BaseAction[AgentStateModel]):
    """Agent-specific actions"""

    type: ActionType
    payload: Optional[Dict[str, Any]] = None

    @staticmethod
    def reducer(state: AgentStateModel, action: "AgentAction") -> AgentStateModel:
        if action.type == ActionType.UPDATE:
            return AgentStateModel(
                data={**state.data, **(action.payload or {})},
                meta=state.meta,
                version=state.version,
                last_updated=state.last_updated,
            )
        elif action.type == ActionType.RESET:
            return AgentStateModel()
        return state


class GlobalAction(BaseAction[GlobalStateModel]):
    """Global state actions"""

    type: ActionType
    payload: Optional[Dict[str, Any]] = None

    @staticmethod
    def reducer(state: GlobalStateModel, action: "GlobalAction") -> GlobalStateModel:
        if action.type == ActionType.UPDATE:
            return GlobalStateModel(
                shared_data={**state.shared_data, **(action.payload or {})},
                agents=state.agents,
                version=state.version,
                last_updated=state.last_updated,
            )
        elif action.type == ActionType.RESET:
            return GlobalStateModel()
        return state
