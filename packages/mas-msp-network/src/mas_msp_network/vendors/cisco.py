"""Cisco syslog, SNMP trap, and poll normalization."""

from __future__ import annotations

import re
from uuid import uuid4

from mas_msp_contracts import AlertRaised, HealthSnapshot

from mas_msp_network.common import (
    build_candidate_asset_ref,
    evaluate_health,
    map_cisco_syslog_severity,
    map_textual_severity,
)
from mas_msp_network.models import (
    SnmpPollObservation,
    SnmpTrapEvent,
    SnmpV3Target,
    SyslogEvent,
)


_CISCO_SYSLOG_PATTERN = re.compile(
    r"^%(?P<facility>[A-Z0-9_]+)-(?P<level>\d)-(?P<mnemonic>[A-Z0-9_]+): (?P<message>.+)$"
)
_INTERFACE_PATTERN = re.compile(
    r"Interface (?P<interface>[^,]+), changed state to (?P<state>[A-Za-z]+)"
)


def normalize_syslog(event: SyslogEvent) -> AlertRaised:
    """Normalize one Cisco syslog line into an alert contract."""

    match = _CISCO_SYSLOG_PATTERN.match(event.message)
    if match is None:
        raise ValueError("unsupported Cisco syslog format")
    parsed = match.groupdict()
    message = parsed["message"]
    facts: dict[str, object] = {
        "facility": parsed["facility"].lower(),
        "mnemonic": parsed["mnemonic"].lower(),
    }
    interface_match = _INTERFACE_PATTERN.search(message)
    if interface_match is not None:
        facts.update(interface_match.groupdict())
    if event.serial is not None:
        facts["serial"] = event.serial
    asset = build_candidate_asset_ref(
        client_id=event.client_id,
        fabric_id=event.fabric_id,
        vendor="Cisco",
        model=event.model,
        hostname=event.hostname,
        mgmt_address=event.mgmt_address,
        site=event.site,
        tags=list(event.tags),
    )
    return AlertRaised(
        alert_id=str(uuid4()),
        client_id=event.client_id,
        fabric_id=event.fabric_id,
        asset=asset,
        source_kind="syslog",
        occurred_at=event.occurred_at,
        severity=map_cisco_syslog_severity(int(parsed["level"])),
        category=parsed["facility"].lower(),
        title=message,
        normalized_facts=facts,
        raw_reference={
            "format": "cisco_syslog",
            "mnemonic": parsed["mnemonic"].lower(),
        },
    )


def normalize_snmp_trap(event: SnmpTrapEvent) -> AlertRaised:
    """Normalize one Cisco trap into an alert contract."""

    severity_hint = event.varbinds.get("severity")
    severity = (
        map_cisco_syslog_severity(int(severity_hint))
        if isinstance(severity_hint, int)
        else map_textual_severity(str(severity_hint or "warning"))
    )
    facts = dict(event.varbinds)
    if event.serial is not None:
        facts.setdefault("serial", event.serial)
    title = str(
        event.varbinds.get("message")
        or event.varbinds.get("summary")
        or f"Cisco trap {event.trap_oid}"
    )
    category = str(event.varbinds.get("category") or "snmp_trap")
    asset = build_candidate_asset_ref(
        client_id=event.client_id,
        fabric_id=event.fabric_id,
        vendor="Cisco",
        model=event.model,
        hostname=event.hostname,
        mgmt_address=event.mgmt_address,
        site=event.site,
        tags=list(event.tags),
    )
    return AlertRaised(
        alert_id=str(uuid4()),
        client_id=event.client_id,
        fabric_id=event.fabric_id,
        asset=asset,
        source_kind="snmp_trap",
        occurred_at=event.occurred_at,
        severity=severity,
        category=category,
        title=title,
        normalized_facts=facts,
        raw_reference={"trap_oid": event.trap_oid, "vendor": "cisco"},
    )


def normalize_snmp_poll(
    target: SnmpV3Target,
    observation: SnmpPollObservation,
) -> HealthSnapshot:
    """Normalize one Cisco poll observation into a health snapshot."""

    asset = build_candidate_asset_ref(
        client_id=target.client_id,
        fabric_id=target.fabric_id,
        vendor="Cisco",
        model=target.model,
        hostname=target.hostname,
        mgmt_address=target.mgmt_address,
        site=target.site,
        tags=list(target.tags),
    )
    metrics = dict(observation.metrics)
    if target.serial is not None:
        metrics.setdefault("serial", target.serial)
    health_state, findings = evaluate_health(
        observation,
        cpu_warning=80,
        cpu_critical=95,
        memory_warning=80,
        memory_critical=95,
        extra_warning_metric="interface_error_count",
        extra_warning_threshold=1,
    )
    return HealthSnapshot(
        snapshot_id=str(uuid4()),
        client_id=target.client_id,
        fabric_id=target.fabric_id,
        asset=asset,
        source_kind="snmp_poll",
        collected_at=observation.collected_at,
        health_state=health_state,
        metrics=metrics,
        findings=findings,
    )


__all__ = ["normalize_snmp_poll", "normalize_snmp_trap", "normalize_syslog"]
