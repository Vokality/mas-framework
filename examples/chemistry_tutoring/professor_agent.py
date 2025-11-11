"""Professor agent that answers chemistry questions."""

import logging
from typing import Optional, override
from pydantic import BaseModel
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from mas import Agent, AgentMessage

logger = logging.getLogger(__name__)


class QuestionMessage(BaseModel):
    """Question message from student."""

    type: str = "question"
    question: str
    topic: str = "chemistry"


class ThanksMessage(BaseModel):
    """Thanks message from student."""

    type: str = "thanks"
    message: str


class ProfessorState(BaseModel):
    """State model for ProfessorAgent."""

    questions_answered: int = 0


class ProfessorAgent(Agent[ProfessorState]):
    """
    Professor agent that answers chemistry questions.

    Uses OpenAI to generate educational explanations for student questions.
    """

    def __init__(
        self,
        agent_id: str = "professor",
        redis_url: str = "redis://localhost:6379",
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
    ):
        """
        Initialize professor agent.

        Args:
            agent_id: Unique agent identifier
            redis_url: Redis connection URL
            openai_api_key: OpenAI API key (or set OPENAI_API_KEY env var)
            model: OpenAI model to use
        """
        super().__init__(
            agent_id=agent_id,
            capabilities=["chemistry_professor", "educator"],
            redis_url=redis_url,
            state_model=ProfessorState,
        )
        self.client = AsyncOpenAI(api_key=openai_api_key)
        self.model = model

    @override
    async def on_start(self) -> None:
        """Initialize the professor agent."""
        logger.info(f"Professor agent {self.id} started, ready to answer questions...")

    @Agent.on("question", model=QuestionMessage)
    async def handle_question(
        self, message: AgentMessage, payload: QuestionMessage
    ) -> None:
        """
        Handle questions from students.

        Args:
            message: Message from a student
            payload: Validated question payload
        """
        logger.info(f"Received question from {message.sender_id}")

        # Generate answer using OpenAI
        answer = await self._generate_answer(payload.question, payload.topic)

        # Send answer back to student
        await self.send(
            message.sender_id,
            "answer",
            {
                "type": "answer",
                "question": payload.question,
                "answer": answer,
            },
        )

        questions_answered = self.state.questions_answered + 1
        await self.update_state({"questions_answered": questions_answered})

    @Agent.on("thanks", model=ThanksMessage)
    async def handle_thanks(
        self, message: AgentMessage, payload: ThanksMessage
    ) -> None:
        """
        Handle thanks message from students.

        Args:
            message: Message from a student
            payload: Validated thanks payload
        """
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Student says: {payload.message}")
        logger.info(f"Total questions answered: {self.state.questions_answered}")
        logger.info(f"{'=' * 60}\n")

    async def _generate_answer(self, question: str, topic: str) -> str:
        """
        Generate an educational answer to a chemistry question.

        Args:
            question: The student's question
            topic: The chemistry topic being discussed

        Returns:
            The professor's answer
        """
        system_prompt = f"""You are an experienced and patient chemistry professor. 
A student is learning about {topic} and has asked you a question. 

Provide a clear, educational explanation that:
1. Directly answers their question
2. Explains the underlying concepts
3. Uses analogies when helpful
4. Encourages further learning

Keep your response concise but thorough (2-4 paragraphs)."""

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=400,
                temperature=0.7,
            )

            answer = response.choices[0].message.content
            return (
                answer
                if answer
                else "I apologize, I need to think more about that question."
            )

        except Exception as e:
            logger.error(f"Failed to generate answer: {e}")
            return f"I apologize, I'm having trouble formulating an answer right now. Error: {str(e)}"
