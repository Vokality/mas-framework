"""Patient agent that asks healthcare questions (gateway mode)."""

import asyncio
import logging
from typing import Optional, override, cast

from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from mas import Agent, AgentMessage, AgentRecord

logger = logging.getLogger(__name__)


class ConsultationResponse(BaseModel):
    """Consultation response from doctor."""

    type: str = "consultation_response"
    advice: str
    question: str | None = None


class PatientState(BaseModel):
    """State model for PatientAgent."""

    doctor_id: Optional[str] = None
    conversation_history: list[ChatCompletionMessageParam] = Field(default_factory=list)
    questions_asked: int = 0
    max_questions: int = 3
    current_concern: str = "general wellness and preventive care"


class PatientAgent(Agent[PatientState]):
    """
    Patient agent that asks healthcare-related questions.

    Uses gateway mode for:
    - HIPAA compliance with audit trail
    - DLP to detect and block PHI leakage
    - Rate limiting to prevent abuse
    - Authentication and authorization
    """

    def __init__(
        self,
        agent_id: str = "patient",
        redis_url: str = "redis://localhost:6379",
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
    ):
        """
        Initialize patient agent with gateway mode enabled.

        Args:
            agent_id: Unique agent identifier
            redis_url: Redis connection URL
            openai_api_key: OpenAI API key
            model: OpenAI model to use
        """
        super().__init__(
            agent_id=agent_id,
            capabilities=["healthcare_patient", "question_asker"],
            redis_url=redis_url,
            use_gateway=True,  # Enable gateway mode for security
            state_model=PatientState,
        )
        self.client = AsyncOpenAI(api_key=openai_api_key)
        self.model = model

    @override
    async def on_start(self) -> None:
        """Initialize the patient agent."""
        logger.info(f"Patient agent {self.id} started (GATEWAY MODE)")
        logger.info("Security features: Auth, RBAC, Rate Limiting, DLP, Audit")

        # Discover doctor agent
        await asyncio.sleep(0.5)
        doctors: list[AgentRecord] = await self.discover(
            capabilities=["healthcare_doctor"]
        )

        if not doctors:
            logger.error("No doctor found! Cannot start consultation.")
            return

        await self.update_state({"doctor_id": doctors[0]["id"]})
        logger.info(f"Found doctor: {self.state.doctor_id}")

        # Start consultation by asking first question
        await self._ask_question()

    @Agent.on("consultation_response", model=ConsultationResponse)
    async def handle_consultation_response(
        self, message: AgentMessage, payload: ConsultationResponse
    ) -> None:
        """
        Handle consultation responses from the doctor.

        Args:
            message: Message from the doctor (passed through gateway)
            payload: Validated consultation response payload
        """
        logger.info(f"\n{'=' * 60}")
        logger.info("DOCTOR'S ADVICE:")
        logger.info(f"{payload.advice}")
        logger.info(f"{'=' * 60}\n")

        # Store in conversation history
        history = list(self.state.conversation_history)
        history.append(
            cast(
                ChatCompletionMessageParam,
                {"role": "assistant", "content": f"Doctor advised: {payload.advice}"},
            )
        )
        await self.update_state({"conversation_history": history})

        # Ask follow-up question or finish
        questions_asked = self.state.questions_asked + 1
        await self.update_state({"questions_asked": questions_asked})

        if questions_asked < self.state.max_questions:
            await asyncio.sleep(1)
            await self._ask_question()
        else:
            logger.info("Consultation complete! Thank you, doctor.")
            await self._send_thanks()

    async def _ask_question(self) -> None:
        """Generate and ask a healthcare question using OpenAI."""
        if not self.state.doctor_id:
            logger.error("No doctor available")
            return

        # Build prompt for question generation
        system_prompt = f"""You are a patient seeking medical advice about {self.state.current_concern}. 
Generate a thoughtful, realistic question that a patient might ask their doctor. 
Keep it concise (1-2 sentences) and avoid including specific personal information like 
names, dates, or medical record numbers."""

        messages: list[ChatCompletionMessageParam] = [
            cast(
                ChatCompletionMessageParam,
                {"role": "system", "content": system_prompt},
            ),
            *self.state.conversation_history,
            cast(
                ChatCompletionMessageParam,
                {"role": "user", "content": "What should I ask the doctor next?"},
            ),
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=150,
                temperature=0.8,
            )

            question = response.choices[0].message.content

            if not question:
                logger.error("Generated empty question")
                return

            logger.info(f"\n{'=' * 60}")
            logger.info(f"PATIENT'S QUESTION #{self.state.questions_asked + 1}:")
            logger.info(f"{question}")
            logger.info(f"{'=' * 60}\n")

            # Store in conversation history
            history = list(self.state.conversation_history)
            history.append(
                cast(
                    ChatCompletionMessageParam,
                    {"role": "user", "content": question},
                )
            )
            await self.update_state({"conversation_history": history})

            # Send question to doctor through gateway
            # Gateway will:
            # 1. Authenticate the message
            # 2. Check authorization (RBAC)
            # 3. Apply rate limiting
            # 4. Scan for PHI/PII (DLP)
            # 5. Log to audit trail
            # 6. Route to doctor via Redis Streams
            await self.send(
                self.state.doctor_id,
                "consultation_request",
                {
                    "type": "consultation_request",
                    "question": question,
                    "concern": self.state.current_concern,
                },
            )

            logger.info("✓ Message sent through gateway (auth, audit, DLP applied)")

        except Exception as e:
            logger.error(f"Failed to generate question: {e}")

    async def _send_thanks(self) -> None:
        """Send thank you message to doctor."""
        if self.state.doctor_id:
            await self.send(
                self.state.doctor_id,
                "consultation_end",
                {
                    "type": "consultation_end",
                    "message": "Thank you for the consultation!",
                },
            )
