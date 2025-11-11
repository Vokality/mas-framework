"""Specialist doctor agent that provides expert medical advice (gateway mode)."""

import logging
from typing import Optional, override, cast

from pydantic import BaseModel
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from mas import Agent, AgentMessage

logger = logging.getLogger(__name__)


class SpecialistConsultationRequest(BaseModel):
    """Specialist consultation request payload."""

    patient_question: str
    concern: str = "general health"
    gp_diagnosis: str = ""
    patient_id: str


class SpecialistConsultationResponse(BaseModel):
    """Specialist consultation response payload."""

    specialist_advice: str
    specialization: str = "specialist"


class SpecialistState(BaseModel):
    """State model for SpecialistAgent."""

    consultations_completed: int = 0


class SpecialistAgent(Agent[SpecialistState]):
    """
    Specialist doctor agent that provides expert medical advice in specific areas.

    Works with the general practitioner (DoctorAgent) to provide specialized
    consultations. The GP refers patients to the specialist, and the specialist
    provides expert guidance back to the GP.

    Uses gateway mode for:
    - Complete audit trail for medical consultations
    - DLP to prevent accidental PHI disclosure
    - Authentication to verify legitimate agents
    - Rate limiting to prevent overload
    """

    def __init__(
        self,
        agent_id: str = "specialist",
        redis_url: str = "redis://localhost:6379",
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        specialization: str = "general medicine",
    ):
        """
        Initialize specialist agent with gateway mode enabled.

        Args:
            agent_id: Unique agent identifier
            redis_url: Redis connection URL
            openai_api_key: OpenAI API key
            model: OpenAI model to use
            specialization: Medical specialization area
        """
        super().__init__(
            agent_id=agent_id,
            capabilities=[
                "healthcare_specialist",
                f"specialist_{specialization.lower().replace(' ', '_')}",
            ],
            redis_url=redis_url,
            use_gateway=True,  # Enable gateway mode for compliance
            state_model=SpecialistState,
        )
        self.client = AsyncOpenAI(api_key=openai_api_key)
        self.model = model
        self.specialization = specialization

    @override
    async def on_start(self) -> None:
        """Initialize the specialist agent."""
        logger.info(f"Specialist agent {self.id} started (GATEWAY MODE)")
        logger.info(f"Specialization: {self.specialization}")
        logger.info("HIPAA-compliant: All messages audited and DLP-scanned")
        logger.info("Ready for specialist consultations...")

    @Agent.on("specialist_consultation", model=SpecialistConsultationRequest)
    async def handle_specialist_consultation(
        self, message: AgentMessage, payload: SpecialistConsultationRequest
    ) -> None:
        """
        Handle consultation requests from general practitioners.

        All messages arrive through gateway with:
        - Authentication verified
        - Authorization checked
        - Rate limits applied
        - DLP scanning completed
        - Audit log entry created

        Args:
            message: Message from a GP doctor (via gateway)
            payload: Validated specialist consultation request payload
        """
        # Only handle requests that expect a reply
        if not message.expects_reply:
            logger.warning(
                f"Received message without reply expectation from {message.sender_id}"
            )
            return

        logger.info(f"\n{'=' * 60}")
        logger.info(f"SPECIALIST CONSULTATION REQUEST from {message.sender_id}")
        logger.info(f"Patient: {payload.patient_id}")
        logger.info(f"Concern: {payload.concern}")
        logger.info(f"Patient Question: {payload.patient_question}")
        logger.info(f"GP Diagnosis: {payload.gp_diagnosis}")
        logger.info(f"{'=' * 60}")
        logger.info("✓ Gateway validated: auth, authz, rate limit, DLP passed")

        # Generate specialist advice using OpenAI
        specialist_advice = await self._generate_specialist_advice(
            payload.patient_question, payload.concern, payload.gp_diagnosis
        )

        logger.info(f"\n{'=' * 60}")
        logger.info("SPECIALIST'S EXPERT ADVICE:")
        logger.info(f"{specialist_advice}")
        logger.info(f"{'=' * 60}\n")

        # Reply to GP (correlation handled automatically by framework)
        response_payload = SpecialistConsultationResponse(
            specialist_advice=specialist_advice,
            specialization=self.specialization,
        )
        await message.reply(
            "specialist_consultation_response", response_payload.model_dump()
        )

        consultations_completed = self.state.consultations_completed + 1
        await self.update_state({"consultations_completed": consultations_completed})
        logger.info(f"Consultation #{consultations_completed} completed")

    async def _generate_specialist_advice(
        self, patient_question: str, concern: str, gp_diagnosis: str
    ) -> str:
        """
        Generate specialist medical advice for a patient question.

        Args:
            patient_question: The patient's original question
            concern: The general health concern area
            gp_diagnosis: The GP's initial diagnosis/advice

        Returns:
            The specialist's expert advice
        """
        system_prompt = f"""You are an expert medical specialist in {self.specialization}. 
A general practitioner is consulting you about a patient's {concern}. 

The GP has already provided their initial assessment:
{gp_diagnosis}

As a specialist, provide expert guidance that:
1. Validates or refines the GP's assessment from your specialized perspective
2. Offers advanced insights based on your specialized knowledge
3. Provides specific recommendations for diagnosis, treatment, or management
4. Highlights any specialist considerations or red flags
5. Suggests when/if specialist in-person follow-up is needed
6. IMPORTANT: Do NOT include specific patient identifiers, dates, or medical record numbers

Keep your response professional, evidence-based, and concise (2-4 paragraphs).
Frame your response as advice to the GP, who will relay it to the patient."""

        messages: list[ChatCompletionMessageParam] = [
            cast(
                ChatCompletionMessageParam,
                {"role": "system", "content": system_prompt},
            ),
            cast(
                ChatCompletionMessageParam,
                {"role": "user", "content": f"Patient asks: {patient_question}"},
            ),
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=500,
                temperature=0.7,
            )

            advice = response.choices[0].message.content
            return (
                advice
                if advice
                else "I apologize, I need more time to formulate a proper specialist response."
            )

        except Exception as e:
            logger.error(f"Failed to generate specialist advice: {e}")
            return f"I apologize, I'm having technical difficulties. Error: {str(e)}"
