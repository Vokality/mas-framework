from __future__ import annotations

import asyncio
import signal
from typing import override

from pydantic import BaseModel, Field

from mas import Agent, AgentMessage


class Ping(BaseModel):
    value: int = Field(ge=0)


class PongState(BaseModel):
    handled: int = 0


class PongAgent(Agent[PongState]):
    def __init__(self, agent_id: str, **kwargs: object) -> None:
        super().__init__(agent_id, state_model=PongState, **kwargs)

    @override
    async def on_start(self) -> None:
        print(f"[pong] started (instance={self.instance_id})")

    @Agent.on("ping", model=Ping)
    async def handle_ping(self, message: AgentMessage, payload: Ping) -> None:
        self.state.handled += 1
        await self.update_state({"handled": self.state.handled})
        await message.reply(
            "pong",
            {"value": payload.value + 1, "handled": self.state.handled},
        )


class PingAgent(Agent[dict[str, object]]):
    @override
    async def on_start(self) -> None:
        print(f"[ping] started (instance={self.instance_id})")

        # PongAgent is started first by agents.yaml, but give transport a moment.
        await asyncio.sleep(0.2)

        reply = await self.request("pong", "ping", {"value": 41}, timeout=5)
        print(f"[ping] got reply: type={reply.message_type} data={reply.data}")

        # Ask the runner to shutdown cleanly.
        signal.raise_signal(signal.SIGINT)
