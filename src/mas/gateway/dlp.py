"""Data Loss Prevention (DLP) Module - Scan messages for sensitive data."""

import hashlib
import json
import logging
import re
import time
from enum import Enum
from typing import Any
from pydantic import BaseModel

from .metrics import MetricsCollector

logger = logging.getLogger(__name__)


class ViolationType(str, Enum):
    """Types of DLP violations."""

    # PII - Personally Identifiable Information
    SSN = "ssn"  # Social Security Number
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"

    # PHI - Protected Health Information
    MRN = "mrn"  # Medical Record Number
    HEALTH_INSURANCE = "health_insurance"
    ICD10 = "icd10"  # Diagnosis codes

    # PCI - Payment Card Industry
    CREDIT_CARD = "credit_card"
    CVV = "cvv"

    # Secrets & Credentials
    API_KEY = "api_key"
    AWS_KEY = "aws_key"
    PRIVATE_KEY = "private_key"
    JWT = "jwt"
    PASSWORD = "password"


class ActionPolicy(str, Enum):
    """DLP action policies."""

    BLOCK = "block"  # Reject message, alert security
    REDACT = "redact"  # Remove/mask sensitive data, deliver modified
    ALERT = "alert"  # Deliver message, flag for review
    ENCRYPT = "encrypt"  # Encrypt sensitive fields, deliver encrypted


class Violation(BaseModel):
    """A DLP violation detected in content."""

    violation_type: ViolationType
    description: str
    matched_text: str  # The actual matched content (for logging/alerting)
    start_pos: int
    end_pos: int
    severity: str = "medium"  # low, medium, high, critical


class ScanResult(BaseModel):
    """Result of DLP scanning."""

    clean: bool
    violations: list[Violation]
    action: ActionPolicy
    redacted_payload: dict[str, Any] | None = None
    payload_hash: str  # Hash of original payload


class DLPModule:
    """
    Data Loss Prevention module for scanning messages.

    Detects sensitive data patterns:
    - PII: SSN, email, phone, addresses
    - PHI: Medical record numbers, health insurance, diagnosis codes
    - PCI: Credit card numbers, CVV codes
    - Secrets: API keys, AWS credentials, private keys, JWTs

    Action policies:
    - BLOCK: Reject message
    - REDACT: Remove sensitive data
    - ALERT: Allow but flag
    - ENCRYPT: Encrypt sensitive fields

    Usage:
        dlp = DLPModule()
        result = await dlp.scan(message.payload)
        if not result.clean and result.action == ActionPolicy.BLOCK:
            # Reject message
    """

    # Pattern definitions
    PATTERNS = {
        # PII Patterns
        ViolationType.SSN: re.compile(
            r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"  # SSN: XXX-XX-XXXX
        ),
        ViolationType.EMAIL: re.compile(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        ),
        ViolationType.PHONE: re.compile(
            r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
        ),
        # PHI Patterns
        ViolationType.MRN: re.compile(
            r"\b(?:MRN|Medical\s+Record)[:\s]+\d{6,10}\b", re.IGNORECASE
        ),
        ViolationType.HEALTH_INSURANCE: re.compile(
            r"\b(?:Health\s+Insurance|Policy)[:\s#]+\d{8,12}\b", re.IGNORECASE
        ),
        ViolationType.ICD10: re.compile(
            r"\b[A-Z]\d{2}(?:\.\d{1,4})?\b"  # ICD-10 codes like A00.1
        ),
        # PCI Patterns
        ViolationType.CREDIT_CARD: re.compile(
            # Visa, MC, Amex, Discover patterns
            r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
            r"[-\s]?(?:\d{4}[-\s]?){2}\d{3,4}\b"
        ),
        ViolationType.CVV: re.compile(r"\b(?:CVV|CVC)[:\s]+\d{3,4}\b", re.IGNORECASE),
        # Secrets Patterns
        ViolationType.API_KEY: re.compile(
            r"\b(?:api[_-]?key|apikey)[:\s=]+['\"]?([A-Za-z0-9_\-]{32,})['\"]?",
            re.IGNORECASE,
        ),
        ViolationType.AWS_KEY: re.compile(
            r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"  # AWS Access Key ID
        ),
        ViolationType.PRIVATE_KEY: re.compile(
            r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", re.IGNORECASE
        ),
        ViolationType.JWT: re.compile(
            r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
        ),
        ViolationType.PASSWORD: re.compile(
            r"\b(?:password|passwd|pwd)[:\s=]+['\"]?([^\s'\"]{6,})['\"]?",
            re.IGNORECASE,
        ),
    }

    # Default action policies by violation type
    DEFAULT_POLICIES = {
        # PII - Redact by default
        ViolationType.SSN: ActionPolicy.REDACT,
        ViolationType.EMAIL: ActionPolicy.ALERT,
        ViolationType.PHONE: ActionPolicy.ALERT,
        ViolationType.ADDRESS: ActionPolicy.ALERT,
        # PHI - Block by default (HIPAA compliance)
        ViolationType.MRN: ActionPolicy.BLOCK,
        ViolationType.HEALTH_INSURANCE: ActionPolicy.BLOCK,
        ViolationType.ICD10: ActionPolicy.ALERT,
        # PCI - Block by default (PCI-DSS compliance)
        ViolationType.CREDIT_CARD: ActionPolicy.BLOCK,
        ViolationType.CVV: ActionPolicy.BLOCK,
        # Secrets - Block by default (security)
        ViolationType.API_KEY: ActionPolicy.BLOCK,
        ViolationType.AWS_KEY: ActionPolicy.BLOCK,
        ViolationType.PRIVATE_KEY: ActionPolicy.BLOCK,
        ViolationType.JWT: ActionPolicy.BLOCK,
        ViolationType.PASSWORD: ActionPolicy.BLOCK,
    }

    # Severity levels by violation type
    SEVERITY_LEVELS = {
        ViolationType.SSN: "high",
        ViolationType.EMAIL: "low",
        ViolationType.PHONE: "low",
        ViolationType.ADDRESS: "medium",
        ViolationType.MRN: "critical",
        ViolationType.HEALTH_INSURANCE: "critical",
        ViolationType.ICD10: "medium",
        ViolationType.CREDIT_CARD: "critical",
        ViolationType.CVV: "critical",
        ViolationType.API_KEY: "critical",
        ViolationType.AWS_KEY: "critical",
        ViolationType.PRIVATE_KEY: "critical",
        ViolationType.JWT: "high",
        ViolationType.PASSWORD: "critical",
    }

    def __init__(
        self, custom_policies: dict[ViolationType, ActionPolicy] | None = None
    ):
        """
        Initialize DLP module.

        Args:
            custom_policies: Override default action policies per violation type
        """
        self.policies = {**self.DEFAULT_POLICIES}
        if custom_policies:
            self.policies.update(custom_policies)

    async def scan(self, payload: dict[str, Any]) -> ScanResult:
        """
        Scan payload for sensitive data violations.

        Args:
            payload: Message payload to scan

        Returns:
            ScanResult with violations and action policy
        """
        # Track scan duration
        scan_start = time.time()

        # Convert payload to text for scanning
        payload_text = json.dumps(payload, default=str)
        payload_hash = hashlib.sha256(payload_text.encode()).hexdigest()

        # Detect violations
        violations = await self._detect_violations(payload_text)

        # Record scan duration metric
        scan_duration = time.time() - scan_start
        MetricsCollector.record_dlp_scan_duration(scan_duration)

        if not violations:
            return ScanResult(
                clean=True,
                violations=[],
                action=ActionPolicy.ALERT,
                payload_hash=payload_hash,
            )

        # Determine action policy (most restrictive wins)
        action = self._determine_action(violations)

        # Apply redaction if needed
        redacted_payload = None
        if action == ActionPolicy.REDACT:
            redacted_payload = await self._apply_redaction(payload, violations)

        logger.info(
            "DLP scan completed",
            extra={
                "violations": len(violations),
                "action": action,
                "violation_types": [v.violation_type for v in violations],
            },
        )

        return ScanResult(
            clean=False,
            violations=violations,
            action=action,
            redacted_payload=redacted_payload,
            payload_hash=payload_hash,
        )

    async def _detect_violations(self, text: str) -> list[Violation]:
        """
        Detect all violations in text.

        Args:
            text: Text to scan

        Returns:
            List of violations found
        """
        violations = []

        for violation_type, pattern in self.PATTERNS.items():
            for match in pattern.finditer(text):
                # Additional validation for credit cards (Luhn algorithm)
                if violation_type == ViolationType.CREDIT_CARD:
                    card_number = re.sub(r"[-\s]", "", match.group(0))
                    if not self._validate_luhn(card_number):
                        continue

                violations.append(
                    Violation(
                        violation_type=violation_type,
                        description=self._get_violation_description(violation_type),
                        matched_text=match.group(0),
                        start_pos=match.start(),
                        end_pos=match.end(),
                        severity=self.SEVERITY_LEVELS.get(violation_type, "medium"),
                    )
                )

        return violations

    def _validate_luhn(self, card_number: str) -> bool:
        """
        Validate credit card number using Luhn algorithm.

        Args:
            card_number: Card number string (digits only)

        Returns:
            True if valid, False otherwise
        """
        if not card_number.isdigit():
            return False

        # Luhn algorithm
        digits = [int(d) for d in card_number]
        checksum = 0

        # Process from right to left
        for i in range(len(digits) - 1, -1, -1):
            digit = digits[i]

            # Double every second digit from right
            if (len(digits) - i) % 2 == 0:
                digit *= 2
                if digit > 9:
                    digit -= 9

            checksum += digit

        return checksum % 10 == 0

    def _determine_action(self, violations: list[Violation]) -> ActionPolicy:
        """
        Determine action policy based on violations.

        Uses most restrictive policy:
        BLOCK > REDACT > ENCRYPT > ALERT

        Args:
            violations: List of violations

        Returns:
            Action policy to apply
        """
        if not violations:
            return ActionPolicy.ALERT

        # Policy priority order (most to least restrictive)
        priority = [
            ActionPolicy.BLOCK,
            ActionPolicy.REDACT,
            ActionPolicy.ENCRYPT,
            ActionPolicy.ALERT,
        ]

        # Find most restrictive policy among all violations
        for policy in priority:
            for violation in violations:
                if self.policies.get(violation.violation_type) == policy:
                    return policy

        return ActionPolicy.ALERT

    async def _apply_redaction(
        self, payload: dict[str, Any], violations: list[Violation]
    ) -> dict[str, Any]:
        """
        Apply redaction to payload based on violations.

        Masks sensitive data while preserving structure.

        Args:
            payload: Original payload
            violations: List of violations to redact

        Returns:
            Redacted payload
        """
        # Convert to JSON and back for deep copy
        redacted = json.loads(json.dumps(payload, default=str))

        # Build replacement mapping
        replacements = {}
        for violation in violations:
            if self.policies.get(violation.violation_type) == ActionPolicy.REDACT:
                replacements[violation.matched_text] = self._get_redaction_mask(
                    violation.violation_type, violation.matched_text
                )

        # Apply replacements recursively
        redacted_result = self._redact_recursive(redacted, replacements)

        logger.debug(
            "Applied redaction",
            extra={
                "violations": len(violations),
                "replacements": len(replacements),
            },
        )

        # Ensure we return a dict
        if isinstance(redacted_result, dict):
            return redacted_result
        return {}  # Fallback to empty dict if not a dict

    def _redact_recursive(self, obj: Any, replacements: dict[str, str]) -> Any:
        """
        Recursively redact sensitive data in object.

        Args:
            obj: Object to redact (dict, list, or primitive)
            replacements: Mapping of text to redact -> replacement

        Returns:
            Redacted object
        """
        if isinstance(obj, dict):
            return {k: self._redact_recursive(v, replacements) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._redact_recursive(item, replacements) for item in obj]
        elif isinstance(obj, str):
            # Apply all replacements to string
            for original, redacted in replacements.items():
                obj = obj.replace(original, redacted)
            return obj
        else:
            return obj

    def _get_redaction_mask(self, violation_type: ViolationType, text: str) -> str:
        """
        Get redaction mask for violation type.

        Args:
            violation_type: Type of violation
            text: Original text

        Returns:
            Redacted/masked text
        """
        # Different masking strategies by type
        if violation_type == ViolationType.SSN:
            # Show last 4 digits: XXX-XX-1234
            if len(text) >= 4:
                return "XXX-XX-" + text[-4:]
            return "XXX-XX-XXXX"

        elif violation_type == ViolationType.CREDIT_CARD:
            # Show last 4 digits: **** **** **** 1234
            digits = re.sub(r"[-\s]", "", text)
            if len(digits) >= 4:
                return "**** **** **** " + digits[-4:]
            return "**** **** **** ****"

        elif violation_type == ViolationType.EMAIL:
            # Mask local part: r***@example.com
            if "@" in text:
                local, domain = text.split("@", 1)
                return f"{local[0]}***@{domain}"
            return "[REDACTED EMAIL]"

        elif violation_type == ViolationType.PHONE:
            # Show last 4 digits: (XXX) XXX-1234
            digits = re.sub(r"[^\d]", "", text)
            if len(digits) >= 4:
                return f"(XXX) XXX-{digits[-4:]}"
            return "(XXX) XXX-XXXX"

        else:
            # Generic redaction
            return f"[REDACTED {violation_type.value.upper()}]"

    def _get_violation_description(self, violation_type: ViolationType) -> str:
        """
        Get human-readable description of violation type.

        Args:
            violation_type: Type of violation

        Returns:
            Description string
        """
        descriptions = {
            ViolationType.SSN: "Social Security Number detected",
            ViolationType.EMAIL: "Email address detected",
            ViolationType.PHONE: "Phone number detected",
            ViolationType.ADDRESS: "Physical address detected",
            ViolationType.MRN: "Medical Record Number detected",
            ViolationType.HEALTH_INSURANCE: "Health insurance number detected",
            ViolationType.ICD10: "ICD-10 diagnosis code detected",
            ViolationType.CREDIT_CARD: "Credit card number detected",
            ViolationType.CVV: "CVV code detected",
            ViolationType.API_KEY: "API key detected",
            ViolationType.AWS_KEY: "AWS access key detected",
            ViolationType.PRIVATE_KEY: "Private key detected",
            ViolationType.JWT: "JWT token detected",
            ViolationType.PASSWORD: "Password detected",
        }
        return descriptions.get(violation_type, f"Violation: {violation_type.value}")
