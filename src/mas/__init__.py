"""MAS Framework - secure multi-agent runtime (gRPC + mTLS)."""

from .agent import Agent, AgentMessage, TlsClientConfig
from .runner import AgentRunner, RunnerSettings
from .server import MASServer, MASServerSettings, TlsConfig
from .state import StateType
from .protocol import EnvelopeMessage as Message, MessageType, MessageMeta
from .__version__ import __version__

__all__ = [
    "Agent",
    "AgentMessage",
    "StateType",
    "Message",
    "MessageType",
    "MessageMeta",
    "TlsClientConfig",
    "MASServer",
    "MASServerSettings",
    "TlsConfig",
    "AgentRunner",
    "RunnerSettings",
    "__version__",
]
