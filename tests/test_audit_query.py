"""Tests for Audit Query API."""

import asyncio
import json
import time

import pytest

from mas.gateway.audit import AuditFileSink

from mas.gateway.audit import AuditModule

# Use anyio for async test support
pytestmark = pytest.mark.asyncio


@pytest.fixture
async def audit_module(redis):
    """Create audit module instance."""
    return AuditModule(redis)


class TestAuditQueryByDecision:
    """Test querying audit logs by decision type."""

    async def test_query_by_decision_allowed(self, audit_module):
        """Test querying for ALLOWED decisions."""
        # Log messages with different decisions
        await audit_module.log_message(
            "msg-1", "agent-a", "agent-b", "ALLOWED", 10.0, {"test": 1}
        )
        await audit_module.log_message(
            "msg-2", "agent-a", "agent-b", "DENIED", 15.0, {"test": 2}
        )
        await audit_module.log_message(
            "msg-3", "agent-a", "agent-b", "ALLOWED", 12.0, {"test": 3}
        )

        # Query for ALLOWED decisions
        results = await audit_module.query_by_decision("ALLOWED")

        assert len(results) == 2
        assert all(r["decision"] == "ALLOWED" for r in results)
        assert results[0]["message_id"] == "msg-1"
        assert results[1]["message_id"] == "msg-3"

    async def test_query_by_decision_rate_limited(self, audit_module):
        """Test querying for RATE_LIMITED decisions."""
        # Log messages
        await audit_module.log_message(
            "msg-1", "agent-a", "agent-b", "ALLOWED", 10.0, {}
        )
        await audit_module.log_message(
            "msg-2", "agent-a", "agent-b", "RATE_LIMITED", 5.0, {}
        )
        await audit_module.log_message(
            "msg-3", "agent-a", "agent-b", "RATE_LIMITED", 5.0, {}
        )

        # Query for RATE_LIMITED
        results = await audit_module.query_by_decision("RATE_LIMITED")

        assert len(results) == 2
        assert all(r["decision"] == "RATE_LIMITED" for r in results)

    async def test_query_by_decision_empty(self, audit_module):
        """Test querying for decision with no matches."""
        # Log messages
        await audit_module.log_message(
            "msg-1", "agent-a", "agent-b", "ALLOWED", 10.0, {}
        )

        # Query for non-existent decision
        results = await audit_module.query_by_decision("DLP_BLOCKED")

        assert len(results) == 0


class TestAuditQueryByViolation:
    """Test querying audit logs by violation type."""

    async def test_query_by_violation_pii(self, audit_module):
        """Test querying for PII violations."""
        # Log messages with different violations
        await audit_module.log_message(
            "msg-1",
            "agent-a",
            "agent-b",
            "DLP_BLOCKED",
            10.0,
            {},
            violations=["PII_SSN", "PII_EMAIL"],
        )
        await audit_module.log_message(
            "msg-2", "agent-a", "agent-b", "ALLOWED", 10.0, {}, violations=[]
        )
        await audit_module.log_message(
            "msg-3",
            "agent-a",
            "agent-b",
            "DLP_BLOCKED",
            10.0,
            {},
            violations=["PII_PHONE"],
        )

        # Query for PII_SSN violations
        results = await audit_module.query_by_violation("PII_SSN")

        assert len(results) == 1
        assert results[0]["message_id"] == "msg-1"
        assert "PII_SSN" in results[0]["violations"]

    async def test_query_by_violation_multiple(self, audit_module):
        """Test querying for violation that appears in multiple messages."""
        # Log messages with PCI violations
        await audit_module.log_message(
            "msg-1",
            "agent-a",
            "agent-b",
            "DLP_BLOCKED",
            10.0,
            {},
            violations=["PCI_CREDIT_CARD"],
        )
        await audit_module.log_message(
            "msg-2",
            "agent-a",
            "agent-b",
            "DLP_BLOCKED",
            10.0,
            {},
            violations=["PCI_CREDIT_CARD", "PCI_CVV"],
        )

        # Query for PCI_CREDIT_CARD
        results = await audit_module.query_by_violation("PCI_CREDIT_CARD")

        assert len(results) == 2
        assert all("PCI_CREDIT_CARD" in r["violations"] for r in results)

    async def test_query_by_violation_empty(self, audit_module):
        """Test querying for violation with no matches."""
        # Log message without violations
        await audit_module.log_message(
            "msg-1", "agent-a", "agent-b", "ALLOWED", 10.0, {}, violations=[]
        )

        # Query for non-existent violation
        results = await audit_module.query_by_violation("PHI_MRN")

        assert len(results) == 0


class TestAuditFileSink:
    """Test audit file sink behavior."""

    async def test_audit_file_written(self, redis, tmp_path):
        file_path = tmp_path / "audit.log"
        sink = AuditFileSink(str(file_path), max_bytes=200, backup_count=1)
        audit_module = AuditModule(redis, file_sink=sink)

        await audit_module.log_message(
            "msg-1",
            "agent-a",
            "agent-b",
            "ALLOWED",
            10.0,
            {"test": "data"},
        )
        await audit_module.log_message(
            "msg-2",
            "agent-a",
            "agent-b",
            "ALLOWED",
            10.0,
            {"test": "data"},
        )

        rotated = file_path.with_suffix(file_path.suffix + ".1")
        assert file_path.exists() or rotated.exists()

        target = file_path if file_path.exists() else rotated
        content = target.read_text(encoding="utf-8")
        assert '"decision"' in content


class TestAuditQueryAll:
    """Test querying all audit log entries."""

    async def test_query_all(self, audit_module):
        """Test querying all entries."""
        # Log multiple messages
        for i in range(5):
            await audit_module.log_message(
                f"msg-{i}",
                "agent-a",
                "agent-b",
                "ALLOWED",
                10.0 + i,
                {"index": i},
            )

        # Query all
        results = await audit_module.query_all(count=10)

        assert len(results) == 5
        assert results[0]["message_id"] == "msg-0"
        assert results[4]["message_id"] == "msg-4"

    async def test_query_all_with_limit(self, audit_module):
        """Test querying all entries with count limit."""
        # Log many messages
        for i in range(10):
            await audit_module.log_message(
                f"msg-{i}", "agent-a", "agent-b", "ALLOWED", 10.0, {}
            )

        # Query with limit
        results = await audit_module.query_all(count=5)

        assert len(results) == 5


class TestAuditTimeRange:
    """Test time range queries."""

    async def test_query_by_time_range(self, audit_module):
        """Test querying within specific time range."""
        # Log message at start
        await audit_module.log_message(
            "msg-1", "agent-a", "agent-b", "ALLOWED", 10.0, {}
        )

        # Wait a bit
        await asyncio.sleep(0.1)
        middle_time = time.time()

        # Log message in middle
        await audit_module.log_message(
            "msg-2", "agent-a", "agent-b", "ALLOWED", 10.0, {}
        )

        # Wait a bit more
        await asyncio.sleep(0.1)

        # Query for messages in middle period only
        results = await audit_module.query_all(
            start_time=middle_time - 0.05, end_time=middle_time + 0.05
        )

        # Should only get msg-2
        assert len(results) >= 1
        assert any(r["message_id"] == "msg-2" for r in results)


class TestAuditExportCSV:
    """Test CSV export functionality."""

    async def test_export_to_csv_empty(self, audit_module):
        """Test exporting empty list to CSV."""
        csv_data = await audit_module.export_to_csv([])
        assert csv_data == ""

    async def test_export_to_csv_single(self, audit_module):
        """Test exporting single entry to CSV."""
        # Log a message
        await audit_module.log_message(
            "msg-1",
            "agent-a",
            "agent-b",
            "ALLOWED",
            10.5,
            {"test": "data"},
            violations=["TEST"],
        )

        # Query and export
        results = await audit_module.query_all()
        csv_data = await audit_module.export_to_csv(results)

        # Verify CSV structure
        lines = csv_data.strip().split("\n")
        assert len(lines) == 2  # Header + 1 row

        # Check header
        header = lines[0]
        assert "message_id" in header
        assert "sender_id" in header
        assert "target_id" in header
        assert "decision" in header

        # Check data row
        data_row = lines[1]
        assert "msg-1" in data_row
        assert "agent-a" in data_row
        assert "agent-b" in data_row
        assert "ALLOWED" in data_row

    async def test_export_to_csv_multiple(self, audit_module):
        """Test exporting multiple entries to CSV."""
        # Log multiple messages
        for i in range(3):
            await audit_module.log_message(
                f"msg-{i}", "agent-a", "agent-b", "ALLOWED", 10.0, {}
            )

        # Query and export
        results = await audit_module.query_all()
        csv_data = await audit_module.export_to_csv(results)

        # Verify CSV structure
        lines = csv_data.strip().split("\n")
        assert len(lines) == 4  # Header + 3 rows

    async def test_export_to_csv_violations(self, audit_module):
        """Test CSV export with violations."""
        # Log message with violations
        await audit_module.log_message(
            "msg-1",
            "agent-a",
            "agent-b",
            "DLP_BLOCKED",
            10.0,
            {},
            violations=["PII_SSN", "PII_EMAIL"],
        )

        # Query and export
        results = await audit_module.query_all()
        csv_data = await audit_module.export_to_csv(results)

        # Violations should be semicolon-separated
        assert "PII_SSN;PII_EMAIL" in csv_data or "PII_EMAIL;PII_SSN" in csv_data


class TestAuditExportJSON:
    """Test JSON export functionality."""

    async def test_export_to_json_empty(self, audit_module):
        """Test exporting empty list to JSON."""
        json_data = await audit_module.export_to_json([])
        assert json_data == "[]"

    async def test_export_to_json_single(self, audit_module):
        """Test exporting single entry to JSON."""
        # Log a message
        await audit_module.log_message(
            "msg-1", "agent-a", "agent-b", "ALLOWED", 10.5, {"test": "data"}
        )

        # Query and export
        results = await audit_module.query_all()
        json_data = await audit_module.export_to_json(results)

        # Parse JSON
        parsed = json.loads(json_data)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["message_id"] == "msg-1"
        assert parsed[0]["sender_id"] == "agent-a"
        assert parsed[0]["target_id"] == "agent-b"
        assert parsed[0]["decision"] == "ALLOWED"

    async def test_export_to_json_pretty(self, audit_module):
        """Test pretty-printed JSON export."""
        # Log a message
        await audit_module.log_message(
            "msg-1", "agent-a", "agent-b", "ALLOWED", 10.0, {}
        )

        # Query and export (pretty)
        results = await audit_module.query_all()
        json_data = await audit_module.export_to_json(results, pretty=True)

        # Should have indentation
        assert "  " in json_data or "\t" in json_data

    async def test_export_to_json_compact(self, audit_module):
        """Test compact JSON export."""
        # Log a message
        await audit_module.log_message(
            "msg-1", "agent-a", "agent-b", "ALLOWED", 10.0, {}
        )

        # Query and export (compact)
        results = await audit_module.query_all()
        json_data = await audit_module.export_to_json(results, pretty=False)

        # Should be compact (no extra whitespace)
        assert "\n  " not in json_data


class TestAuditComplianceReport:
    """Test compliance report generation."""

    async def test_export_compliance_report_csv(self, audit_module):
        """Test generating compliance report in CSV format."""
        # Use buffer to avoid timing edge cases where messages fall outside range
        start_time = time.time() - 0.1

        # Log some messages
        for i in range(3):
            await audit_module.log_message(
                f"msg-{i}", "agent-a", "agent-b", "ALLOWED", 10.0, {}
            )

        end_time = time.time() + 0.1

        # Generate report
        report = await audit_module.export_compliance_report(
            start_time, end_time, format_type="csv"
        )

        # Should be CSV format
        lines = report.strip().split("\n")
        assert len(lines) >= 4  # Header + 3 rows
        assert "message_id" in lines[0]  # Header

    async def test_export_compliance_report_json(self, audit_module):
        """Test generating compliance report in JSON format."""
        # Use buffer to avoid timing edge cases where messages fall outside range
        start_time = time.time() - 0.1

        # Log some messages
        for i in range(3):
            await audit_module.log_message(
                f"msg-{i}", "agent-a", "agent-b", "ALLOWED", 10.0, {}
            )

        end_time = time.time() + 0.1

        # Generate report
        report = await audit_module.export_compliance_report(
            start_time, end_time, format_type="json"
        )

        # Should be valid JSON
        parsed = json.loads(report)
        assert isinstance(parsed, list)
        assert len(parsed) == 3

    async def test_export_compliance_report_invalid_format(self, audit_module):
        """Test generating report with invalid format."""
        start_time = time.time()
        end_time = time.time()

        # Should raise ValueError
        with pytest.raises(ValueError, match="Unsupported format"):
            await audit_module.export_compliance_report(
                start_time, end_time, format_type="pdf"
            )


class TestAuditIntegration:
    """Integration tests for audit query API."""

    async def test_full_audit_workflow(self, audit_module):
        """Test complete audit workflow: log, query, export."""
        # Use unique IDs for this test
        sender = "workflow-sender"
        target1 = "workflow-target1"
        target2 = "workflow-target2"

        # 1. Log various messages
        await audit_module.log_message("wf-msg-1", sender, target1, "ALLOWED", 10.0, {})
        await audit_module.log_message("wf-msg-2", sender, target2, "DENIED", 5.0, {})
        await audit_module.log_message(
            "wf-msg-3",
            target1,
            sender,
            "DLP_BLOCKED",
            15.0,
            {},
            violations=["PII_SSN"],
        )

        # 2. Query by different criteria
        sender_results = await audit_module.query_by_sender(sender)
        assert len(sender_results) == 2

        decision_results = await audit_module.query_by_decision("DENIED")
        assert len(decision_results) == 1

        violation_results = await audit_module.query_by_violation("PII_SSN")
        assert len(violation_results) == 1

        # 3. Export to different formats
        all_results = await audit_module.query_all()

        csv_export = await audit_module.export_to_csv(all_results)
        assert len(csv_export) > 0

        json_export = await audit_module.export_to_json(all_results)
        assert len(json_export) > 0
