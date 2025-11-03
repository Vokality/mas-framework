"""Patient agent that asks healthcare questions (gateway mode)."""

import asyncio
import logging
from typing import Optional, override
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from mas import Agent, AgentMessage

logger = logging.getLogger(__name__)


class PatientAgent(Agent):
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
        )
        self.client = AsyncOpenAI(api_key=openai_api_key)
        self.model = model
        self.doctor_id: Optional[str] = None
        self.conversation_history: list[ChatCompletionMessageParam] = []
        self.questions_asked = 0
        self.max_questions = 3
        self.current_concern = "general wellness and preventive care"

    @override
    async def on_start(self) -> None:
        """Initialize the patient agent."""
        logger.info(f"Patient agent {self.id} started (GATEWAY MODE)")
        logger.info("Security features: Auth, RBAC, Rate Limiting, DLP, Audit")

        # Discover doctor agent
        await asyncio.sleep(0.5)
        doctors = await self.discover(capabilities=["healthcare_doctor"])

        if not doctors:
            logger.error("No doctor found! Cannot start consultation.")
            return

        self.doctor_id = doctors[0]["id"]
        logger.info(f"Found doctor: {self.doctor_id}")

        # Start consultation by asking first question
        await self._ask_question()

    @override
    async def on_message(self, message: AgentMessage) -> None:
        """
        Handle responses from the doctor.

        Args:
            message: Message from the doctor (passed through gateway)
        """
        if message.payload.get("type") == "consultation_response":
            advice = message.payload.get("advice")

            logger.info(f"\n{'=' * 60}")
            logger.info("DOCTOR'S ADVICE:")
            logger.info(f"{advice}")
            logger.info(f"{'=' * 60}\n")

            # Store in conversation history
            self.conversation_history.append(
                {"role": "assistant", "content": f"Doctor advised: {advice}"}
            )

            # Ask follow-up question or finish
            self.questions_asked += 1

            if self.questions_asked < self.max_questions:
                await asyncio.sleep(1)
                await self._ask_question()
            else:
                logger.info("Consultation complete! Thank you, doctor.")
                await self._send_thanks()

    async def _ask_question(self) -> None:
        """Generate and ask a healthcare question using OpenAI."""
        if not self.doctor_id:
            logger.error("No doctor available")
            return

        # Build prompt for question generation
        system_prompt = f"""You are a patient seeking medical advice about {self.current_concern}. 
Generate a thoughtful, realistic question that a patient might ask their doctor. 
Keep it concise (1-2 sentences) and avoid including specific personal information like 
names, dates, or medical record numbers."""

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            *self.conversation_history,
            {"role": "user", "content": "What should I ask the doctor next?"},
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
            logger.info(f"PATIENT'S QUESTION #{self.questions_asked + 1}:")
            logger.info(f"{question}")
            logger.info(f"{'=' * 60}\n")

            # Store in conversation history
            self.conversation_history.append({"role": "user", "content": question})

            # Send question to doctor through gateway
            # Gateway will:
            # 1. Authenticate the message
            # 2. Check authorization (RBAC)
            # 3. Apply rate limiting
            # 4. Scan for PHI/PII (DLP)
            # 5. Log to audit trail
            # 6. Route to doctor via Redis Streams
            await self.send(
                self.doctor_id,
                {
                    "type": "consultation_request",
                    "question": question,
                    "concern": self.current_concern,
                },
            )

            logger.info("✓ Message sent through gateway (auth, audit, DLP applied)")

        except Exception as e:
            logger.error(f"Failed to generate question: {e}")

    async def _send_thanks(self) -> None:
        """Send thank you message to doctor."""
        if self.doctor_id:
            await self.send(
                self.doctor_id,
                {
                    "type": "consultation_end",
                    "message": "Thank you for the consultation!",
                },
            )
