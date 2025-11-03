"""MAS Framework - Simplified Multi-Agent System."""

from .agent import Agent, AgentMessage
from .service import MASService
from .registry import AgentRegistry
from .state import StateManager
from .protocol import Message, MessageType
from .__version__ import __version__

__all__ = [
    "Agent",
    "AgentMessage",
    "MASService",
    "AgentRegistry",
    "StateManager",
    "Message",
    "MessageType",
    "__version__",
]
