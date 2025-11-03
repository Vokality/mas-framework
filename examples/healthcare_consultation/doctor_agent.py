"""Doctor agent that provides healthcare advice (gateway mode)."""

import logging
from typing import Optional
from openai import AsyncOpenAI
from mas import Agent, AgentMessage

logger = logging.getLogger(__name__)


class DoctorAgent(Agent):
    """
    Doctor agent that provides healthcare advice and guidance.
    
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
        
    async def on_start(self) -> None:
        """Initialize the doctor agent."""
        logger.info(f"Doctor agent {self.id} started (GATEWAY MODE)")
        logger.info(f"HIPAA-compliant: All messages audited and DLP-scanned")
        logger.info("Ready for consultations...")
    
    async def on_message(self, message: AgentMessage) -> None:
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
        """
        msg_type = message.payload.get("type")
        
        if msg_type == "consultation_request":
            question = message.payload.get("question")
            concern = message.payload.get("concern", "general health")
            
            if not question or not isinstance(question, str):
                logger.error("Received consultation without valid question")
                return
            
            logger.info(f"Received consultation request from {message.sender_id}")
            logger.info("✓ Gateway validated: auth, authz, rate limit, DLP passed")
            
            # Generate medical advice using OpenAI
            advice = await self._generate_advice(question, concern)
            
            # Send advice back to patient through gateway
            await self.send(message.sender_id, {
                "type": "consultation_response",
                "question": question,
                "advice": advice,
            })
            
            self.consultations_completed += 1
            
        elif msg_type == "consultation_end":
            logger.info(f"\n{'='*60}")
            logger.info(f"Patient says: {message.payload.get('message')}")
            logger.info(f"Total consultations completed: {self.consultations_completed}")
            logger.info(f"{'='*60}\n")
            logger.info("✓ All consultations logged in audit trail")
    
    async def _generate_advice(self, question: str, concern: str) -> str:
        """
        Generate medical advice for a patient question.
        
        Args:
            question: The patient's question
            concern: The general health concern area
            
        Returns:
            The doctor's advice
        """
        system_prompt = f"""You are a caring and experienced physician. 
A patient is consulting you about {concern}. 

Provide helpful, evidence-based medical guidance that:
1. Directly addresses their question
2. Explains the medical reasoning
3. Offers actionable advice
4. Encourages appropriate follow-up when needed
5. IMPORTANT: Do NOT include specific patient identifiers, dates, or medical record numbers

Keep your response professional but accessible (2-4 paragraphs)."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question}
        ]
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=400,
                temperature=0.7,
            )
            
            advice = response.choices[0].message.content
            return advice if advice else "I apologize, I need more time to formulate a proper response."
            
        except Exception as e:
            logger.error(f"Failed to generate advice: {e}")
            return f"I apologize, I'm having technical difficulties. Error: {str(e)}"
