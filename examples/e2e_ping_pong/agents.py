from __future__ import annotations

import asyncio
import signal
from typing import Any, override

from pydantic import BaseModel, Field

from mas import Agent, AgentMessage


class PatientMessage(BaseModel):
    text: str = Field(min_length=1)
    turn: int = Field(ge=1)


class DoctorState(BaseModel):
    handled: int = 0
    next_reply_index: int = 0


class PongAgent(Agent[DoctorState]):
    def __init__(self, agent_id: str, **kwargs: Any) -> None:
        super().__init__(agent_id, state_model=DoctorState, **kwargs)

    @override
    async def on_start(self) -> None:
        print(f"[pong] started (instance={self.instance_id})")

    @Agent.on("patient_message", model=PatientMessage)
    async def handle_patient_message(
        self,
        message: AgentMessage,
        payload: PatientMessage,
    ) -> None:
        doctor_lines = [
            "Hi, I am Dr. Patel. What brings you in today?",
            "How long has the cough been going on, and is it worse at night?",
            "Any fever, chest pain, or shortness of breath?",
            "That sounds like a viral upper respiratory infection. I will listen to your lungs and check your vitals.",
            "I recommend rest, fluids, and an over-the-counter cough suppressant. If symptoms worsen or last more than two weeks, follow up.",
        ]

        self.state.handled += 1
        reply_index = self.state.next_reply_index
        if reply_index < len(doctor_lines):
            reply_text = doctor_lines[reply_index]
        else:
            reply_text = "Thanks for the update. Let me know if anything changes."

        self.state.next_reply_index = min(reply_index + 1, len(doctor_lines))
        await self.update_state(
            {
                "handled": self.state.handled,
                "next_reply_index": self.state.next_reply_index,
            }
        )

        await message.reply(
            "doctor_message",
            {
                "text": reply_text,
                "turn": payload.turn + 1,
                "handled": self.state.handled,
            },
        )


class PingAgent(Agent[dict[str, object]]):
    @override
    async def on_start(self) -> None:
        print(f"[ping] started (instance={self.instance_id})")

        patient_lines = [
            "Hi Doctor. I have had a cough for about a week and it is keeping me up at night. james@gmail.com",
            "It started after a cold. The cough is dry and my throat feels scratchy.",
            "No fever or chest pain. I get a little winded when I climb stairs, but nothing severe.",
            "I have been drinking tea and using lozenges. I have not taken any medicines yet.",
            "Got it. I will monitor it and come back if it does not improve. Thanks.",
        ]

        # PongAgent is started first by mas.yaml, but give transport a moment.
        await asyncio.sleep(0.2)

        for turn_index, line in enumerate(patient_lines, start=1):
            reply = await self.request(
                "pong",
                "patient_message",
                {"text": line, "turn": turn_index},
                timeout=5,
            )
            print(f"[ping] got reply: type={reply.message_type} data={reply.data}")

        # Ask the runner to shutdown cleanly.
        signal.raise_signal(signal.SIGINT)
