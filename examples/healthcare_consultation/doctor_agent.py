"""Doctor agent that provides healthcare advice (gateway mode)."""

import asyncio
import logging
from typing import Optional, override, cast

from pydantic import BaseModel
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from mas import Agent, AgentMessage, AgentRecord

logger = logging.getLogger(__name__)


class ConsultationRequest(BaseModel):
    """Consultation request from patient."""

    type: str = "consultation_request"
    question: str
    concern: str = "general health"


class ConsultationEnd(BaseModel):
    """Consultation end message from patient."""

    type: str = "consultation_end"
    message: str


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


class DoctorState(BaseModel):
    """State model for DoctorAgent."""

    consultations_completed: int = 0
    specialist_id: Optional[str] = None


class DoctorAgent(Agent[DoctorState]):
    """
    Doctor agent that provides healthcare advice and guidance.

    Works as a general practitioner who can consult with specialists
    for complex cases. The doctor receives questions from patients,
    provides initial assessment, and consults specialists when needed.

    Uses gateway mode for:
    - Complete audit trail for medical consultations
    - DLP to prevent accidental PHI disclosure
    - Authentication to verify legitimate agents
    - Rate limiting to prevent overload
    """

    def __init__(
        self,
        agent_id: str = "doctor",
        redis_url: str = "redis://localhost:6379",
        openai_api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
    ):
        """
        Initialize doctor agent with gateway mode enabled.

        Args:
            agent_id: Unique agent identifier
            redis_url: Redis connection URL
            openai_api_key: OpenAI API key
            model: OpenAI model to use
        """
        super().__init__(
            agent_id=agent_id,
            capabilities=["healthcare_doctor", "medical_advisor"],
            redis_url=redis_url,
            use_gateway=True,  # Enable gateway mode for compliance
            state_model=DoctorState,
        )
        self.client = AsyncOpenAI(api_key=openai_api_key)
        self.model = model

    @override
    async def on_start(self) -> None:
        """Initialize the doctor agent."""
        logger.info(f"Doctor agent {self.id} started (GATEWAY MODE)")
        logger.info("HIPAA-compliant: All messages audited and DLP-scanned")

        # Discover specialist agent
        await asyncio.sleep(0.5)
        specialists: list[AgentRecord] = await self.discover(
            capabilities=["healthcare_specialist"]
        )

        if specialists:
            await self.update_state({"specialist_id": specialists[0]["id"]})
            logger.info(f"Found specialist: {self.state.specialist_id}")
        else:
            logger.warning(
                "No specialist found - will handle all consultations directly"
            )

        logger.info("Ready for consultations...")

    @Agent.on("consultation_request", model=ConsultationRequest)
    async def handle_consultation_request(
        self, message: AgentMessage, payload: ConsultationRequest
    ) -> None:
        """
        Handle consultation requests from patients.

        All messages arrive through gateway with:
        - Authentication verified
        - Authorization checked
        - Rate limits applied
        - DLP scanning completed
        - Audit log entry created

        Args:
            message: Message from a patient (via gateway)
            payload: Validated consultation request payload
        """
        # Handle patient consultation (spawned as task for concurrency)
        await self._handle_patient_consultation(message, payload)

    @Agent.on("consultation_end", model=ConsultationEnd)
    async def handle_consultation_end(
        self, message: AgentMessage, payload: ConsultationEnd
    ) -> None:
        """
        Handle consultation end message from patients.

        Args:
            message: Message from a patient (via gateway)
            payload: Validated consultation end payload
        """
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Patient says: {payload.message}")
        logger.info(
            f"Total consultations completed: {self.state.consultations_completed}"
        )
        logger.info(f"{'=' * 60}\n")
        logger.info("✓ All consultations logged in audit trail")

    async def _handle_patient_consultation(
        self, message: AgentMessage, payload: ConsultationRequest
    ) -> None:
        """
        Handle a patient consultation request.

        This runs as a separate task, allowing concurrent handling of multiple patients.

        Args:
            message: Message from patient
            payload: Validated consultation request payload
        """
        question = payload.question
        concern = payload.concern
        patient_id = message.sender_id

        logger.info(f"\n{'=' * 60}")
        logger.info(f"CONSULTATION REQUEST from {patient_id}")
        logger.info(f"Question: {question}")
        logger.info(f"Concern: {concern}")
        logger.info(f"{'=' * 60}")
        logger.info("✓ Gateway validated: auth, authz, rate limit, DLP passed")

        # Generate initial diagnosis using OpenAI
        initial_diagnosis = await self._generate_initial_diagnosis(question, concern)

        logger.info(f"\n{'=' * 60}")
        logger.info("GP'S INITIAL DIAGNOSIS:")
        logger.info(f"{initial_diagnosis}")
        logger.info(f"{'=' * 60}\n")

        # Consult specialist if available (using new request-response API)
        if self.state.specialist_id:
            logger.info(f"Consulting specialist {self.state.specialist_id}...")

            try:
                # Request-response pattern - waits for specialist reply
                # But doesn't block other patients (runs in separate task)
                request_payload = SpecialistConsultationRequest(
                    patient_question=question,
                    concern=concern,
                    gp_diagnosis=initial_diagnosis,
                    patient_id=patient_id,
                )
                specialist_response = await self.request(
                    self.state.specialist_id,
                    "specialist_consultation",
                    request_payload.model_dump(),
                    timeout=30.0,
                )

                # Parse response payload
                response_payload = SpecialistConsultationResponse.model_validate(
                    specialist_response.data
                )
                specialist_advice = response_payload.specialist_advice
                specialization = response_payload.specialization

                logger.info(f"\n{'=' * 60}")
                logger.info(f"SPECIALIST RESPONSE RECEIVED ({specialization})")
                logger.info(f"{'=' * 60}\n")

                # Synthesize final advice combining GP and specialist input
                final_advice = await self._synthesize_final_advice(
                    question,
                    initial_diagnosis,
                    specialist_advice,
                    specialization,
                )

                logger.info(f"\n{'=' * 60}")
                logger.info("FINAL ADVICE TO PATIENT:")
                logger.info(f"{final_advice}")
                logger.info(f"{'=' * 60}\n")

                # Send final advice to patient
                await self.send(
                    patient_id,
                    "consultation_response",
                    {
                        "type": "consultation_response",
                        "question": question,
                        "advice": final_advice,
                    },
                )

            except asyncio.TimeoutError:
                logger.error(
                    f"Specialist consultation timed out for patient {patient_id}"
                )
                # Fall back to initial diagnosis
                try:
                    # Verify agent is still running and registered before sending
                    if not self._running or not self._token:
                        logger.error(
                            "Cannot send fallback message: agent not running or not registered"
                        )
                        return

                    await self.send(
                        patient_id,
                        "consultation_response",
                        {
                            "type": "consultation_response",
                            "question": question,
                            "advice": initial_diagnosis
                            + "\n\n(Note: Specialist consultation unavailable)",
                        },
                    )
                except RuntimeError as e:
                    logger.error(
                        f"Failed to send fallback message to patient {patient_id}: {e}"
                    )
                    # Agent may have been deregistered - log but don't crash
                    return
        else:
            # No specialist available, send initial diagnosis directly to patient
            await self.send(
                patient_id,
                "consultation_response",
                {
                    "type": "consultation_response",
                    "question": question,
                    "advice": initial_diagnosis,
                },
            )

        consultations_completed = self.state.consultations_completed + 1
        await self.update_state({"consultations_completed": consultations_completed})

    async def _generate_initial_diagnosis(self, question: str, concern: str) -> str:
        """
        Generate initial diagnosis and assessment for a patient question.

        This is the GP's preliminary assessment before consulting with a specialist.

        Args:
            question: The patient's question
            concern: The general health concern area

        Returns:
            The GP's initial diagnosis and assessment
        """
        system_prompt = f"""You are a caring and experienced general practitioner. 
A patient is consulting you about {concern}. 

Provide an initial assessment that:
1. Directly addresses their question from a general practitioner's perspective
2. Explains your medical reasoning
3. Identifies whether this requires specialist consultation
4. Provides preliminary advice
5. IMPORTANT: Do NOT include specific patient identifiers, dates, or medical record numbers

Keep your response professional but accessible (2-3 paragraphs).
Note: This is your initial assessment, which may be refined by a specialist."""

        messages: list[ChatCompletionMessageParam] = [
            cast(
                ChatCompletionMessageParam,
                {"role": "system", "content": system_prompt},
            ),
            cast(
                ChatCompletionMessageParam,
                {"role": "user", "content": question},
            ),
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=350,
                temperature=0.7,
            )

            diagnosis = response.choices[0].message.content
            return (
                diagnosis
                if diagnosis
                else "I apologize, I need more time to formulate a proper response."
            )

        except Exception as e:
            logger.error(f"Failed to generate initial diagnosis: {e}")
            return f"I apologize, I'm having technical difficulties. Error: {str(e)}"

    async def _synthesize_final_advice(
        self,
        question: str,
        gp_diagnosis: str,
        specialist_advice: str,
        specialization: str,
    ) -> str:
        """
        Synthesize final advice combining GP and specialist perspectives.

        Args:
            question: The patient's original question
            gp_diagnosis: The GP's initial diagnosis
            specialist_advice: The specialist's expert advice
            specialization: The specialist's area of expertise

        Returns:
            The final synthesized advice for the patient
        """
        system_prompt = f"""You are a caring and experienced general practitioner. 
You consulted a {specialization} specialist about a patient's question and received their expert input.

Synthesize a final response to the patient that:
1. Incorporates both your initial assessment and the specialist's expert guidance
2. Presents the information in a cohesive, patient-friendly manner
3. Highlights key recommendations from the specialist
4. Provides clear, actionable next steps
5. IMPORTANT: Do NOT include specific patient identifiers, dates, or medical record numbers

Your initial assessment:
{gp_diagnosis}

Specialist's input:
{specialist_advice}

Create a unified response that gives the patient the benefit of both perspectives.
Keep it professional but accessible (3-4 paragraphs)."""

        messages: list[ChatCompletionMessageParam] = [
            cast(
                ChatCompletionMessageParam,
                {"role": "system", "content": system_prompt},
            ),
            cast(
                ChatCompletionMessageParam,
                {"role": "user", "content": f"Patient's question: {question}"},
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
                else "I apologize, I need more time to formulate a proper response."
            )

        except Exception as e:
            logger.error(f"Failed to synthesize final advice: {e}")
            return f"I apologize, I'm having technical difficulties. Error: {str(e)}"
