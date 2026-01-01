"""MAS Framework - Simplified Multi-Agent System."""

from .agent import Agent, AgentMessage
from .service import MASService
from .registry import AgentRegistry, AgentRecord
from .state import StateManager, StateType
from .protocol import EnvelopeMessage as Message, MessageType, MessageMeta
from .__version__ import __version__
from .runner import AgentRunner, RunnerSettings, load_runner_settings

__all__ = [
    "Agent",
    "AgentMessage",
    "MASService",
    "AgentRegistry",
    "AgentRecord",
    "StateManager",
    "StateType",
    "Message",
    "MessageType",
    "MessageMeta",
    "AgentRunner",
    "RunnerSettings",
    "load_runner_settings",
    "__version__",
]
