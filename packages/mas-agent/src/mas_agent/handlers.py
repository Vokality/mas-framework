"""Typed message-handler registration and dispatch for agents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import FunctionType
from typing import ClassVar

from pydantic import BaseModel

from ._core import AgentCore, AgentMessage

HandlerMarker = tuple[str, type[BaseModel] | None]
# The parameter list is intentionally open (`...`): handlers are heterogeneous —
# `self` is whatever Agent subclass declares them and `payload` is each handler's
# own BaseModel type. Pinning a concrete signature would reject valid typed
# handlers via Callable contravariance on `self`/`payload`. The return type
# (Awaitable[None]) is the only invariant, and it is pinned.
HandlerFunction = Callable[..., Awaitable[None]]

# Decorated handler functions -> their (message_type, model) markers. Populated
# by ``@Agent.on`` at class-definition time and collected per subclass in
# ``__init_subclass__``.
_DECORATED_HANDLERS: dict[HandlerFunction, list[HandlerMarker]] = {}


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    """Typed handler registration entry."""

    fn: HandlerFunction
    model: type[BaseModel] | None


class HandlerRegistryMixin(AgentCore):
    """``@on`` registration plus typed dispatch for :class:`Agent`.

    Delivery contract — **at-least-once**. The server delivers over a durable
    Redis stream and redelivers anything it has not seen ACKed: on handler
    error (NACK), on a dropped/reconnected transport, or when shutdown cancels a
    handler before its ACK is flushed. A handler can therefore run **more than
    once for the same logical message**, and concurrently across instances that
    share the agent id.

    Handlers must be **idempotent**: dedupe on ``msg.message_id`` (stable across
    redeliveries), make side effects safe to repeat (upserts, conditional
    writes, idempotency keys on external calls), and never assume exactly-once.
    A handler that raises is NACKed and redelivered, so raising is the right
    signal for a transient failure but a poison message will retry until DLQ'd.
    """

    _handlers: ClassVar[dict[str, HandlerSpec]] = {}

    @classmethod
    def on(
        cls, message_type: str, *, model: type[BaseModel] | None = None
    ) -> Callable[[HandlerFunction], HandlerFunction]:
        """Decorator to register a handler for a message_type."""

        def decorator(fn: HandlerFunction) -> HandlerFunction:
            """Register a function as a handler for this agent class."""
            if not callable(fn):
                raise TypeError("handler must be callable")

            if not isinstance(fn, FunctionType):
                registry = dict(cls._handlers)
                registry[message_type] = HandlerSpec(fn=fn, model=model)
                cls._handlers = registry
                return fn

            qualname_parts = fn.__qualname__.split(".")
            if len(qualname_parts) >= 2:
                _DECORATED_HANDLERS.setdefault(fn, []).append((message_type, model))
            else:
                registry = dict(cls._handlers)
                registry[message_type] = HandlerSpec(fn=fn, model=model)
                cls._handlers = registry
            return fn

        return decorator

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Collect handler registrations from subclass methods."""
        super().__init_subclass__(**kwargs)
        cls._handlers = {}

        # Reflection over class members is intentional here: handlers are
        # registered by the @on decorator, which marks functions in
        # _DECORATED_HANDLERS; this collects the marked ones (including inherited
        # methods, hence dir() rather than __dict__) into the per-class registry.
        for name in dir(cls):
            try:
                attr = getattr(cls, name)
                if not isinstance(attr, FunctionType):
                    continue
                for message_type, model in _DECORATED_HANDLERS.get(attr, []):
                    cls._handlers[message_type] = HandlerSpec(fn=attr, model=model)
            except AttributeError:
                pass

    async def _dispatch_typed(self, msg: AgentMessage) -> bool:
        """Dispatch to a typed handler if registered."""
        registry = type(self)._handlers
        spec = registry.get(msg.message_type)
        if not spec:
            return False

        if spec.model is None:
            await spec.fn(self, msg, None)
            return True

        payload_model = spec.model.model_validate(msg.data)
        await spec.fn(self, msg, payload_model)
        return True
