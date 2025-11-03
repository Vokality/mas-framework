"""Student agent that asks chemistry questions."""

import asyncio
import logging
from typing import Optional, override
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from mas import Agent, AgentMessage

logger = logging.getLogger(__name__)


class StudentAgent(Agent):
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
        )
        self.client = AsyncOpenAI(api_key=openai_api_key)
        self.model = model
        self.professor_id: Optional[str] = None
        self.conversation_history: list[ChatCompletionMessageParam] = []
        self.questions_asked = 0
        self.max_questions = 3
        self.current_topic = "chemical bonding"

    @override
    async def on_start(self) -> None:
        """Initialize the student agent."""
        logger.info(f"Student agent {self.id} started, looking for professor...")

        # Discover professor agent
        await asyncio.sleep(0.5)  # Give professor time to register
        professors = await self.discover(capabilities=["chemistry_professor"])

        if not professors:
            logger.error("No professor found! Cannot start tutoring session.")
            return

        self.professor_id = professors[0]["id"]
        logger.info(f"Found professor: {self.professor_id}")

        # Start the tutoring session by asking first question
        await self._ask_question()

    @override
    async def on_message(self, message: AgentMessage) -> None:
        """
        Handle responses from the professor.

        Args:
            message: Message from the professor
        """
        if message.payload.get("type") == "answer":
            answer = message.payload.get("answer")

            logger.info(f"\n{'=' * 60}")
            logger.info("PROFESSOR'S ANSWER:")
            logger.info(f"{answer}")
            logger.info(f"{'=' * 60}\n")

            # Store in conversation history
            self.conversation_history.append(
                {"role": "assistant", "content": f"Professor answered: {answer}"}
            )

            # Ask follow-up question or finish
            self.questions_asked += 1

            if self.questions_asked < self.max_questions:
                await asyncio.sleep(1)  # Brief pause between questions
                await self._ask_question()
            else:
                logger.info("Tutoring session complete! Thank you, professor.")
                await self._send_thanks()

    async def _ask_question(self) -> None:
        """Generate and ask a chemistry question using OpenAI."""
        if not self.professor_id:
            logger.error("No professor available")
            return

        # Build prompt for question generation
        system_prompt = f"""You are a high school student learning about {self.current_topic} 
in chemistry class. Generate a thoughtful question about this topic that shows you're 
trying to understand the concepts. Keep it concise (1-2 sentences)."""

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            *self.conversation_history,
            {"role": "user", "content": "What should I ask next about this topic?"},
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
            logger.info(f"STUDENT'S QUESTION #{self.questions_asked + 1}:")
            logger.info(f"{question}")
            logger.info(f"{'=' * 60}\n")

            # Store in conversation history
            self.conversation_history.append({"role": "user", "content": question})

            # Send question to professor
            await self.send(
                self.professor_id,
                {
                    "type": "question",
                    "question": question,
                    "topic": self.current_topic,
                },
            )

        except Exception as e:
            logger.error(f"Failed to generate question: {e}")

    async def _send_thanks(self) -> None:
        """Send thank you message to professor."""
        if self.professor_id:
            await self.send(
                self.professor_id,
                {
                    "type": "thanks",
                    "message": "Thank you for helping me understand chemistry!",
                },
            )
