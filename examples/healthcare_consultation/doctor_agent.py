"""Doctor agent that provides healthcare advice (gateway mode)."""

import asyncio
import logging
from typing import Optional
from openai import AsyncOpenAI
from mas import Agent, AgentMessage

logger = logging.getLogger(__name__)


class DoctorAgent(Agent):
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
        )
        self.client = AsyncOpenAI(api_key=openai_api_key)
        self.model = model
        self.consultations_completed = 0
        self.specialist_id: Optional[str] = None
        self.pending_consultations: dict[
            str, dict
        ] = {}  # Track consultations awaiting specialist response

    async def on_start(self) -> None:
        """Initialize the doctor agent."""
        logger.info(f"Doctor agent {self.id} started (GATEWAY MODE)")
        logger.info("HIPAA-compliant: All messages audited and DLP-scanned")

        # Discover specialist agent
        await asyncio.sleep(0.5)
        specialists = await self.discover(capabilities=["healthcare_specialist"])

        if specialists:
            self.specialist_id = specialists[0]["id"]
            logger.info(f"Found specialist: {self.specialist_id}")
        else:
            logger.warning(
                "No specialist found - will handle all consultations directly"
            )

        logger.info("Ready for consultations...")

    async def on_message(self, message: AgentMessage) -> None:
        """
        Handle consultation requests from patients and responses from specialists.

        All messages arrive through gateway with:
        - Authentication verified
        - Authorization checked
        - Rate limits applied
        - DLP scanning completed
        - Audit log entry created

        Args:
            message: Message from a patient or specialist (via gateway)
        """
        msg_type = message.payload.get("type")

        if msg_type == "consultation_request":
            question = message.payload.get("question")
            concern = message.payload.get("concern", "general health")

            if not question or not isinstance(question, str):
                logger.error("Received consultation without valid question")
                return

            logger.info(f"\n{'=' * 60}")
            logger.info(f"CONSULTATION REQUEST from {message.sender_id}")
            logger.info(f"Question: {question}")
            logger.info(f"Concern: {concern}")
            logger.info(f"{'=' * 60}")
            logger.info("✓ Gateway validated: auth, authz, rate limit, DLP passed")

            # Generate initial diagnosis using OpenAI
            initial_diagnosis = await self._generate_initial_diagnosis(
                question, concern
            )

            logger.info(f"\n{'=' * 60}")
            logger.info("GP'S INITIAL DIAGNOSIS:")
            logger.info(f"{initial_diagnosis}")
            logger.info(f"{'=' * 60}\n")

            # Consult specialist if available
            if self.specialist_id:
                logger.info(f"Consulting specialist {self.specialist_id}...")

                # Store consultation context
                consultation_id = f"{message.sender_id}_{self.consultations_completed}"
                self.pending_consultations[consultation_id] = {
                    "patient_id": message.sender_id,
                    "question": question,
                    "concern": concern,
                    "initial_diagnosis": initial_diagnosis,
                }

                # Send consultation request to specialist
                await self.send(
                    self.specialist_id,
                    {
                        "type": "specialist_consultation_request",
                        "patient_question": question,
                        "concern": concern,
                        "gp_diagnosis": initial_diagnosis,
                        "patient_id": message.sender_id,
                        "consultation_id": consultation_id,
                    },
                )
                logger.info("✓ Specialist consultation request sent")
            else:
                # No specialist available, send initial diagnosis directly to patient
                await self.send(
                    message.sender_id,
                    {
                        "type": "consultation_response",
                        "question": question,
                        "advice": initial_diagnosis,
                    },
                )
                self.consultations_completed += 1

        elif msg_type == "specialist_consultation_response":
            # Received specialist's expert advice
            consultation_id = message.payload.get("consultation_id")
            specialist_advice = message.payload.get("specialist_advice")
            patient_question = message.payload.get("patient_question")
            specialization = message.payload.get("specialization")

            if not consultation_id or not isinstance(consultation_id, str):
                logger.error(
                    "Received specialist response without valid consultation_id"
                )
                return

            if consultation_id not in self.pending_consultations:
                logger.error(
                    f"Received specialist response for unknown consultation: {consultation_id}"
                )
                return

            if not specialist_advice or not isinstance(specialist_advice, str):
                logger.error("Received specialist response without valid advice")
                return

            if not patient_question or not isinstance(patient_question, str):
                logger.error(
                    "Received specialist response without valid patient question"
                )
                return

            consultation = self.pending_consultations.pop(consultation_id)
            patient_id = consultation["patient_id"]
            initial_diagnosis = consultation["initial_diagnosis"]
            spec = specialization if isinstance(specialization, str) else "specialist"

            logger.info(f"\n{'=' * 60}")
            logger.info(f"SPECIALIST RESPONSE RECEIVED ({spec})")
            logger.info(f"{'=' * 60}\n")

            # Synthesize final advice combining GP and specialist input
            final_advice = await self._synthesize_final_advice(
                patient_question,
                initial_diagnosis,
                specialist_advice,
                spec,
            )

            logger.info(f"\n{'=' * 60}")
            logger.info("FINAL ADVICE TO PATIENT:")
            logger.info(f"{final_advice}")
            logger.info(f"{'=' * 60}\n")

            # Send final advice to patient through gateway
            await self.send(
                patient_id,
                {
                    "type": "consultation_response",
                    "question": patient_question,
                    "advice": final_advice,
                },
            )

            self.consultations_completed += 1

        elif msg_type == "consultation_end":
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Patient says: {message.payload.get('message')}")
            logger.info(
                f"Total consultations completed: {self.consultations_completed}"
            )
            logger.info(f"{'=' * 60}\n")
            logger.info("✓ All consultations logged in audit trail")

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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Patient's question: {question}"},
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
