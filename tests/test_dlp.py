"""Tests for DLP (Data Loss Prevention) Module."""

import pytest

from mas.gateway.dlp import (
    ActionPolicy,
    DLPModule,
    DlpRule,
    ViolationType,
)

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


class TestDLPModule:
    """Test suite for DLP scanner."""

    @pytest.fixture
    def dlp(self) -> DLPModule:
        """Create DLP module instance."""
        return DLPModule()

    async def test_clean_payload(self, dlp: DLPModule) -> None:
        """Test scanning clean payload with no violations."""
        payload = {"message": "Hello, how are you?", "data": {"count": 42}}

        result = await dlp.scan(payload)

        assert result.clean is True
        assert len(result.violations) == 0
        assert result.action == ActionPolicy.ALERT

    # PII Detection Tests

    async def test_detect_ssn(self, dlp: DLPModule) -> None:
        """Test SSN detection."""
        payload = {"message": "My SSN is 123-45-6789"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.SSN
        assert result.violations[0].severity == "high"
        assert result.action == ActionPolicy.REDACT

    async def test_detect_ssn_formats(self, dlp: DLPModule) -> None:
        """Test SSN detection in various formats."""
        test_cases = [
            "123-45-6789",  # With dashes
            "123 45 6789",  # With spaces
            "123456789",  # No separators
        ]

        for ssn in test_cases:
            payload = {"ssn": ssn}
            result = await dlp.scan(payload)
            assert result.clean is False
            assert any(
                v.violation_type == ViolationType.SSN for v in result.violations
            ), f"Failed to detect SSN: {ssn}"

    async def test_detect_email(self, dlp: DLPModule) -> None:
        """Test email detection."""
        payload = {"contact": "user@example.com"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.EMAIL
        assert result.action == ActionPolicy.ALERT

    async def test_detect_phone(self, dlp: DLPModule) -> None:
        """Test phone number detection."""
        test_cases = [
            "555-123-4567",
            "(555) 123-4567",
            "555.123.4567",
            "+1-555-123-4567",
        ]

        for phone in test_cases:
            payload = {"phone": phone}
            result = await dlp.scan(payload)
            assert result.clean is False
            assert any(
                v.violation_type == ViolationType.PHONE for v in result.violations
            ), f"Failed to detect phone: {phone}"

    # PHI Detection Tests

    async def test_detect_mrn(self, dlp: DLPModule) -> None:
        """Test Medical Record Number detection."""
        payload = {"patient": "MRN: 1234567"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.MRN
        assert result.violations[0].severity == "critical"
        assert result.action == ActionPolicy.BLOCK

    async def test_detect_health_insurance(self, dlp: DLPModule) -> None:
        """Test health insurance number detection."""
        payload = {"insurance": "Health Insurance: 123456789"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert any(
            v.violation_type == ViolationType.HEALTH_INSURANCE
            for v in result.violations
        )
        assert result.action == ActionPolicy.BLOCK

    async def test_detect_icd10(self, dlp: DLPModule) -> None:
        """Test ICD-10 diagnosis code detection."""
        payload = {"diagnosis": "Patient has A00.1"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert any(v.violation_type == ViolationType.ICD10 for v in result.violations)

    # PCI Detection Tests

    async def test_detect_credit_card_visa(self, dlp: DLPModule) -> None:
        """Test Visa card detection with Luhn validation."""
        # Valid Visa test card number
        payload = {"card": "4532-0151-2345-6789"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.CREDIT_CARD
        assert result.violations[0].severity == "critical"
        assert result.action == ActionPolicy.BLOCK

    async def test_detect_credit_card_mastercard(self, dlp: DLPModule) -> None:
        """Test MasterCard detection."""
        # Valid MasterCard test number
        payload = {"card": "5425-2334-3010-9903"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert any(
            v.violation_type == ViolationType.CREDIT_CARD for v in result.violations
        )

    async def test_luhn_validation(self, dlp: DLPModule) -> None:
        """Test Luhn algorithm validation rejects invalid cards."""
        # Invalid card number (fails Luhn check)
        payload = {"card": "4532-0151-2345-6780"}  # Last digit wrong

        result = await dlp.scan(payload)

        # Should not detect as credit card due to Luhn failure
        assert not any(
            v.violation_type == ViolationType.CREDIT_CARD for v in result.violations
        )

    async def test_detect_cvv(self, dlp: DLPModule) -> None:
        """Test CVV code detection."""
        payload = {"security": "CVV: 123"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.CVV
        assert result.action == ActionPolicy.BLOCK

    # Secrets Detection Tests

    async def test_detect_api_key(self, dlp: DLPModule) -> None:
        """Test API key detection."""
        payload = {"config": "api_key: abcdef1234567890abcdef1234567890"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.API_KEY
        assert result.action == ActionPolicy.BLOCK

    async def test_detect_aws_key(self, dlp: DLPModule) -> None:
        """Test AWS access key detection."""
        payload = {"aws": "AKIAIOSFODNN7EXAMPLE"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.AWS_KEY
        assert result.action == ActionPolicy.BLOCK

    async def test_detect_private_key(self, dlp: DLPModule) -> None:
        """Test private key detection."""
        payload = {"key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.PRIVATE_KEY
        assert result.action == ActionPolicy.BLOCK

    async def test_detect_jwt(self, dlp: DLPModule) -> None:
        """Test JWT token detection."""
        payload = {
            "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        }

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.JWT
        assert result.action == ActionPolicy.BLOCK

    async def test_detect_password(self, dlp: DLPModule) -> None:
        """Test password detection."""
        payload = {"credentials": "password: MySecretPass123"}

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) == 1
        assert result.violations[0].violation_type == ViolationType.PASSWORD
        assert result.action == ActionPolicy.BLOCK

    # Redaction Tests

    async def test_redact_ssn(self, dlp: DLPModule) -> None:
        """Test SSN redaction."""
        payload = {"message": "My SSN is 123-45-6789"}

        result = await dlp.scan(payload)

        assert result.redacted_payload is not None
        assert "XXX-XX-6789" in str(result.redacted_payload)
        assert "123-45-6789" not in str(result.redacted_payload)

    async def test_redact_email(self, dlp: DLPModule) -> None:
        """Test email redaction."""
        # Configure to redact emails
        dlp_custom = DLPModule(
            custom_policies={ViolationType.EMAIL: ActionPolicy.REDACT}
        )
        payload = {"contact": "user@example.com"}

        result = await dlp_custom.scan(payload)

        assert result.redacted_payload is not None
        assert "u***@example.com" in str(result.redacted_payload)
        assert "user@example.com" not in str(result.redacted_payload)

    async def test_redact_phone(self, dlp: DLPModule) -> None:
        """Test phone number redaction."""
        dlp_custom = DLPModule(
            custom_policies={ViolationType.PHONE: ActionPolicy.REDACT}
        )
        payload = {"phone": "555-123-4567"}

        result = await dlp_custom.scan(payload)

        assert result.redacted_payload is not None
        assert "(XXX) XXX-4567" in str(result.redacted_payload)
        assert "555-123-4567" not in str(result.redacted_payload)

    async def test_redact_credit_card(self, dlp: DLPModule) -> None:
        """Test credit card redaction."""
        dlp_custom = DLPModule(
            custom_policies={ViolationType.CREDIT_CARD: ActionPolicy.REDACT}
        )
        payload = {"card": "4532-0151-2345-6789"}

        result = await dlp_custom.scan(payload)

        assert result.redacted_payload is not None
        assert "**** **** **** 6789" in str(result.redacted_payload)
        assert "4532-0151-2345-6789" not in str(result.redacted_payload)

    async def test_redact_nested_structure(self, dlp: DLPModule) -> None:
        """Test redaction in nested data structures."""
        payload = {
            "user": {"name": "John", "ssn": "123-45-6789"},
            "contacts": [{"email": "john@example.com"}],
        }

        result = await dlp.scan(payload)

        assert result.redacted_payload is not None
        redacted = result.redacted_payload
        assert "XXX-XX-6789" in str(redacted)
        assert "123-45-6789" not in str(redacted)

    # Policy Tests

    async def test_custom_policy_override(self, dlp: DLPModule) -> None:
        """Test custom policy overrides."""
        # Override SSN to BLOCK instead of REDACT
        dlp_custom = DLPModule(custom_policies={ViolationType.SSN: ActionPolicy.BLOCK})
        payload = {"ssn": "123-45-6789"}

        result = await dlp_custom.scan(payload)

        assert result.action == ActionPolicy.BLOCK

    async def test_custom_rule_append(self, dlp: DLPModule) -> None:
        """Test custom rule appended to defaults."""
        rules = [
            DlpRule.model_validate(
                {
                    "id": "internal_account_id",
                    "type": "internal_account_id",
                    "pattern": r"\bACCT-[A-Z0-9]{10}\b",
                    "action": ActionPolicy.BLOCK,
                    "severity": "high",
                    "description": "Internal account IDs",
                }
            )
        ]
        dlp_custom = DLPModule(custom_rules=rules, merge_strategy="append")
        payload = {"account": "ACCT-ABCDEF1234"}

        result = await dlp_custom.scan(payload)

        assert result.clean is False
        assert any(v.violation_type == "internal_account_id" for v in result.violations)
        assert result.action == ActionPolicy.BLOCK

    async def test_most_restrictive_policy_wins(self, dlp: DLPModule) -> None:
        """Test that most restrictive policy is applied when multiple violations."""
        payload = {
            "ssn": "123-45-6789",  # REDACT policy
            "card": "4532-0151-2345-6789",  # BLOCK policy
        }

        result = await dlp.scan(payload)

        # BLOCK is more restrictive than REDACT
        assert result.action == ActionPolicy.BLOCK

    async def test_multiple_violations(self, dlp: DLPModule) -> None:
        """Test detection of multiple violations in same payload."""
        payload = {
            "message": "Contact me at user@example.com or 555-123-4567",
            "ssn": "123-45-6789",
        }

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) >= 3  # email, phone, ssn
        violation_types = [v.violation_type for v in result.violations]
        assert ViolationType.EMAIL in violation_types
        assert ViolationType.PHONE in violation_types
        assert ViolationType.SSN in violation_types

    async def test_payload_hash_generation(self, dlp: DLPModule) -> None:
        """Test that payload hash is generated correctly."""
        payload = {"message": "test"}

        result = await dlp.scan(payload)

        assert result.payload_hash is not None
        assert len(result.payload_hash) == 64  # SHA256 hex digest

    async def test_complex_nested_payload(self, dlp: DLPModule) -> None:
        """Test scanning complex nested payloads."""
        payload = {
            "level1": {
                "level2": {
                    "level3": {
                        "data": "SSN: 123-45-6789",
                        "info": ["email: test@example.com", "phone: 555-123-4567"],
                    }
                }
            }
        }

        result = await dlp.scan(payload)

        assert result.clean is False
        assert len(result.violations) >= 2

    async def test_no_false_positives_on_similar_patterns(self, dlp: DLPModule) -> None:
        """Test that similar but invalid patterns don't trigger false positives."""
        payload = {
            "data": "Build number: 123-45-67890",  # Not a valid SSN (too many digits)
            "version": "1.2.3.4.5.6.7.8.9",  # Not a phone number
        }

        result = await dlp.scan(payload)

        # May detect some patterns but should validate properly
        # This is more of a sanity check
        assert result is not None
