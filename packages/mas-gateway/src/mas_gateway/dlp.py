"""Data Loss Prevention (DLP) Module - Scan messages for sensitive data."""

import hashlib
import json
import logging
import re
import time
from enum import Enum
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

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

    violation_type: str
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


class DlpRule(BaseModel):
    """Custom DLP rule definition."""

    rule_id: str = Field(alias="id")
    violation_type: str = Field(alias="type")
    pattern: str
    action: ActionPolicy
    severity: str = "medium"
    description: str = ""
    enabled: bool = True
    case_insensitive: bool = False

    model_config = ConfigDict(populate_by_name=True)


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
        result = await dlp.scan(message.data)
        if not result.clean and result.action == ActionPolicy.BLOCK:
            # Reject message
    """

    # Pattern definitions
    DEFAULT_PATTERNS: dict[str, list[re.Pattern[str]]] = {
        # PII Patterns
        ViolationType.SSN.value: [re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")],
        ViolationType.EMAIL.value: [
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
        ],
        ViolationType.PHONE.value: [
            re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
        ],
        # PHI Patterns
        ViolationType.MRN.value: [
            re.compile(r"\b(?:MRN|Medical\s+Record)[:\s]+\d{6,10}\b", re.IGNORECASE)
        ],
        ViolationType.HEALTH_INSURANCE.value: [
            re.compile(
                r"\b(?:Health\s+Insurance|Policy)[:\s#]+\d{8,12}\b",
                re.IGNORECASE,
            )
        ],
        ViolationType.ICD10.value: [re.compile(r"\b[A-Z]\d{2}(?:\.\d{1,4})?\b")],
        # PCI Patterns
        ViolationType.CREDIT_CARD.value: [
            re.compile(
                r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))"
                r"[-\s]?(?:\d{4}[-\s]?){2}\d{3,4}\b"
            )
        ],
        ViolationType.CVV.value: [
            re.compile(r"\b(?:CVV|CVC)[:\s]+\d{3,4}\b", re.IGNORECASE)
        ],
        # Secrets Patterns
        ViolationType.API_KEY.value: [
            re.compile(
                r"\b(?:api[_-]?key|apikey)[:\s=]+['\"]?([A-Za-z0-9_\-]{32,})['\"]?",
                re.IGNORECASE,
            )
        ],
        ViolationType.AWS_KEY.value: [re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")],
        ViolationType.PRIVATE_KEY.value: [
            re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", re.IGNORECASE)
        ],
        ViolationType.JWT.value: [
            re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
        ],
        ViolationType.PASSWORD.value: [
            re.compile(
                r"\b(?:password|passwd|pwd)[:\s=]+['\"]?([^\s'\"]{6,})['\"]?",
                re.IGNORECASE,
            )
        ],
    }

    # Default action policies by violation type
    DEFAULT_POLICIES: dict[str, ActionPolicy] = {
        # PII - Redact by default
        ViolationType.SSN.value: ActionPolicy.REDACT,
        ViolationType.EMAIL.value: ActionPolicy.ALERT,
        ViolationType.PHONE.value: ActionPolicy.ALERT,
        ViolationType.ADDRESS.value: ActionPolicy.ALERT,
        # PHI - Block by default (HIPAA compliance)
        ViolationType.MRN.value: ActionPolicy.BLOCK,
        ViolationType.HEALTH_INSURANCE.value: ActionPolicy.BLOCK,
        ViolationType.ICD10.value: ActionPolicy.ALERT,
        # PCI - Block by default (PCI-DSS compliance)
        ViolationType.CREDIT_CARD.value: ActionPolicy.BLOCK,
        ViolationType.CVV.value: ActionPolicy.BLOCK,
        # Secrets - Block by default (security)
        ViolationType.API_KEY.value: ActionPolicy.BLOCK,
        ViolationType.AWS_KEY.value: ActionPolicy.BLOCK,
        ViolationType.PRIVATE_KEY.value: ActionPolicy.BLOCK,
        ViolationType.JWT.value: ActionPolicy.BLOCK,
        ViolationType.PASSWORD.value: ActionPolicy.BLOCK,
    }

    # Severity levels by violation type
    SEVERITY_LEVELS: dict[str, str] = {
        ViolationType.SSN.value: "high",
        ViolationType.EMAIL.value: "low",
        ViolationType.PHONE.value: "low",
        ViolationType.ADDRESS.value: "medium",
        ViolationType.MRN.value: "critical",
        ViolationType.HEALTH_INSURANCE.value: "critical",
        ViolationType.ICD10.value: "medium",
        ViolationType.CREDIT_CARD.value: "critical",
        ViolationType.CVV.value: "critical",
        ViolationType.API_KEY.value: "critical",
        ViolationType.AWS_KEY.value: "critical",
        ViolationType.PRIVATE_KEY.value: "critical",
        ViolationType.JWT.value: "high",
        ViolationType.PASSWORD.value: "critical",
    }

    def __init__(
        self,
        custom_policies: dict[str | ViolationType, ActionPolicy] | None = None,
        custom_rules: list[DlpRule] | None = None,
        merge_strategy: Literal["append", "replace"] = "append",
        disable_defaults: list[str | ViolationType] | None = None,
    ):
        """
        Initialize DLP module.

        Args:
            custom_policies: Override default action policies per violation type
            custom_rules: Additional regex rules to scan for
            merge_strategy: Append to or replace default rules
            disable_defaults: Default rule types to disable
        """
        if merge_strategy not in {"append", "replace"}:
            raise ValueError("merge_strategy must be 'append' or 'replace'")

        disabled = {
            self._normalize_violation_type(value) for value in (disable_defaults or [])
        }

        if merge_strategy == "append":
            self.patterns = {
                key: list(patterns)
                for key, patterns in self.DEFAULT_PATTERNS.items()
                if key not in disabled
            }
            self.policies = {
                key: policy
                for key, policy in self.DEFAULT_POLICIES.items()
                if key not in disabled
            }
            self.severity_levels = {
                key: severity
                for key, severity in self.SEVERITY_LEVELS.items()
                if key not in disabled
            }
        else:
            self.patterns = {}
            self.policies = {}
            self.severity_levels = {}

        for rule in custom_rules or []:
            if not rule.enabled:
                continue
            violation_type = self._normalize_violation_type(rule.violation_type)
            pattern = self._compile_pattern(rule.pattern, rule.case_insensitive)
            self.patterns.setdefault(violation_type, []).append(pattern)
            self.policies[violation_type] = rule.action
            self.severity_levels[violation_type] = rule.severity

        if custom_policies:
            for violation_type, policy in custom_policies.items():
                normalized = self._normalize_violation_type(violation_type)
                self.policies[normalized] = policy

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

        scan_duration = time.time() - scan_start

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
                "duration_seconds": round(scan_duration, 6),
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
        violations: list[Violation] = []

        for violation_type, patterns in self.patterns.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    # Additional validation for credit cards (Luhn algorithm)
                    if violation_type == ViolationType.CREDIT_CARD.value:
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
                            severity=self.severity_levels.get(violation_type, "medium"),
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
        replacements: dict[str, str] = {}
        for violation in violations:
            if self.policies.get(violation.violation_type) == ActionPolicy.REDACT:
                replacements[violation.matched_text] = self._get_redaction_mask(
                    violation.violation_type, violation.matched_text
                )

        # Apply replacements recursively
        redacted_result: Any = self._redact_recursive(redacted, replacements)

        logger.debug(
            "Applied redaction",
            extra={
                "violations": len(violations),
                "replacements": len(replacements),
            },
        )

        # Ensure we return a dict
        if isinstance(redacted_result, dict):
            return cast(dict[str, Any], redacted_result)
        empty: dict[str, Any] = {}
        return empty  # Fallback to empty dict if not a dict

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
            obj_dict = cast(dict[str, Any], obj)
            return {
                key: self._redact_recursive(value, replacements)
                for key, value in obj_dict.items()
            }
        elif isinstance(obj, list):
            obj_list = cast(list[Any], obj)
            return [self._redact_recursive(item, replacements) for item in obj_list]
        elif isinstance(obj, str):
            # Apply all replacements to string
            for original, redacted in replacements.items():
                obj = obj.replace(original, redacted)
            return obj
        else:
            return obj

    def _get_redaction_mask(self, violation_type: str, text: str) -> str:
        """
        Get redaction mask for violation type.

        Args:
            violation_type: Type of violation
            text: Original text

        Returns:
            Redacted/masked text
        """
        # Different masking strategies by type
        if violation_type == ViolationType.SSN.value:
            # Show last 4 digits: XXX-XX-1234
            if len(text) >= 4:
                return "XXX-XX-" + text[-4:]
            return "XXX-XX-XXXX"

        elif violation_type == ViolationType.CREDIT_CARD.value:
            # Show last 4 digits: **** **** **** 1234
            digits = re.sub(r"[-\s]", "", text)
            if len(digits) >= 4:
                return "**** **** **** " + digits[-4:]
            return "**** **** **** ****"

        elif violation_type == ViolationType.EMAIL.value:
            # Mask local part: r***@example.com
            if "@" in text:
                local, domain = text.split("@", 1)
                return f"{local[0]}***@{domain}"
            return "[REDACTED EMAIL]"

        elif violation_type == ViolationType.PHONE.value:
            # Show last 4 digits: (XXX) XXX-1234
            digits = re.sub(r"[^\d]", "", text)
            if len(digits) >= 4:
                return f"(XXX) XXX-{digits[-4:]}"
            return "(XXX) XXX-XXXX"

        else:
            # Generic redaction
            return f"[REDACTED {violation_type.upper()}]"

    def _get_violation_description(self, violation_type: str) -> str:
        """
        Get human-readable description of violation type.

        Args:
            violation_type: Type of violation

        Returns:
            Description string
        """
        descriptions = {
            ViolationType.SSN.value: "Social Security Number detected",
            ViolationType.EMAIL.value: "Email address detected",
            ViolationType.PHONE.value: "Phone number detected",
            ViolationType.ADDRESS.value: "Physical address detected",
            ViolationType.MRN.value: "Medical Record Number detected",
            ViolationType.HEALTH_INSURANCE.value: "Health insurance number detected",
            ViolationType.ICD10.value: "ICD-10 diagnosis code detected",
            ViolationType.CREDIT_CARD.value: "Credit card number detected",
            ViolationType.CVV.value: "CVV code detected",
            ViolationType.API_KEY.value: "API key detected",
            ViolationType.AWS_KEY.value: "AWS access key detected",
            ViolationType.PRIVATE_KEY.value: "Private key detected",
            ViolationType.JWT.value: "JWT token detected",
            ViolationType.PASSWORD.value: "Password detected",
        }
        return descriptions.get(violation_type, f"Violation: {violation_type}")

    @staticmethod
    def _normalize_violation_type(value: str | ViolationType) -> str:
        if isinstance(value, ViolationType):
            return value.value
        return value

    @staticmethod
    def _compile_pattern(pattern: str, case_insensitive: bool) -> re.Pattern[str]:
        flags = re.IGNORECASE if case_insensitive else 0
        return re.compile(pattern, flags)
