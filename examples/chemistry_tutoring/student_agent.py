"""Student agent that asks chemistry questions."""

import asyncio
import logging
from typing import Optional, override, cast

from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from mas import Agent, AgentMessage, AgentRecord

logger = logging.getLogger(__name__)


class AnswerMessage(BaseModel):
    """Answer message from professor."""

    type: str = "answer"
    answer: str
    question: str | None = None


class StudentState(BaseModel):
    """State model for StudentAgent."""

    professor_id: Optional[str] = None
    conversation_history: list[ChatCompletionMessageParam] = Field(default_factory=list)
    questions_asked: int = 0
    max_questions: int = 3
    current_topic: str = "chemical bonding"


class StudentAgent(Agent[StudentState]):
    """
    Student agent that asks chemistry homework questions.

    Uses OpenAI to generate questions based on a chemistry topic
    and processes responses from the professor.
    """

    def __init__(
        self,
        agent_id: str = "student",
        redis_url: str = "redis://localhost:6379",
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
    ):
        """
        Initialize student agent.

        Args:
            agent_id: Unique agent identifier
            redis_url: Redis connection URL
            openai_api_key: OpenAI API key (or set OPENAI_API_KEY env var)
            model: OpenAI model to use
        """
        super().__init__(
            agent_id=agent_id,
            capabilities=["chemistry_student", "question_asker"],
            redis_url=redis_url,
            state_model=StudentState,
        )
        self.client = AsyncOpenAI(api_key=openai_api_key)
        self.model = model

    @override
    async def on_start(self) -> None:
        """Initialize the student agent."""
        logger.info(f"Student agent {self.id} started, looking for professor...")

        # Discover professor agent
        await asyncio.sleep(0.5)  # Give professor time to register
        professors: list[AgentRecord] = await self.discover(
            capabilities=["chemistry_professor"]
        )

        if not professors:
            logger.error("No professor found! Cannot start tutoring session.")
            return

        await self.update_state({"professor_id": professors[0]["id"]})
        logger.info(f"Found professor: {self.state.professor_id}")

        # Start the tutoring session by asking first question
        await self._ask_question()

    @Agent.on("answer", model=AnswerMessage)
    async def handle_answer(
        self, message: AgentMessage, payload: AnswerMessage
    ) -> None:
        """
        Handle answer responses from the professor.

        Args:
            message: Message from the professor
            payload: Validated answer payload
        """
        logger.info(f"\n{'=' * 60}")
        logger.info("PROFESSOR'S ANSWER:")
        logger.info(f"{payload.answer}")
        logger.info(f"{'=' * 60}\n")

        # Store in conversation history
        history = list(self.state.conversation_history)
        history.append(
            cast(
                ChatCompletionMessageParam,
                {
                    "role": "assistant",
                    "content": f"Professor answered: {payload.answer}",
                },
            )
        )
        await self.update_state({"conversation_history": history})

        # Ask follow-up question or finish
        questions_asked = self.state.questions_asked + 1
        await self.update_state({"questions_asked": questions_asked})

        if questions_asked < self.state.max_questions:
            await asyncio.sleep(1)  # Brief pause between questions
            await self._ask_question()
        else:
            logger.info("Tutoring session complete! Thank you, professor.")
            await self._send_thanks()

    async def _ask_question(self) -> None:
        """Generate and ask a chemistry question using OpenAI."""
        if not self.state.professor_id:
            logger.error("No professor available")
            return

        # Build prompt for question generation
        system_prompt = f"""You are a high school student learning about {self.state.current_topic} 
in chemistry class. Generate a thoughtful question about this topic that shows you're 
trying to understand the concepts. Keep it concise (1-2 sentences)."""

        messages: list[ChatCompletionMessageParam] = [
            cast(
                ChatCompletionMessageParam,
                {"role": "system", "content": system_prompt},
            ),
            *self.state.conversation_history,
            cast(
                ChatCompletionMessageParam,
                {"role": "user", "content": "What should I ask next about this topic?"},
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
            logger.info(f"STUDENT'S QUESTION #{self.state.questions_asked + 1}:")
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

            # Send question to professor
            await self.send(
                self.state.professor_id,
                "question",
                {
                    "type": "question",
                    "question": question,
                    "topic": self.state.current_topic,
                },
            )

        except Exception as e:
            logger.error(f"Failed to generate question: {e}")

    async def _send_thanks(self) -> None:
        """Send thank you message to professor."""
        if self.state.professor_id:
            await self.send(
                self.state.professor_id,
                "thanks",
                {
                    "type": "thanks",
                    "message": "Thank you for helping me understand chemistry!",
                },
            )
