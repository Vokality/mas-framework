"""FortiGate syslog, SNMP trap, and poll normalization."""

from __future__ import annotations

import re
from uuid import uuid4

from mas_msp_contracts import AlertRaised, HealthSnapshot

from mas_msp_network.common import (
    build_candidate_asset_ref,
    evaluate_health,
    map_textual_severity,
)
from mas_msp_network.models import (
    SnmpPollObservation,
    SnmpTrapEvent,
    SnmpV3Target,
    SyslogEvent,
)


_PAIR_PATTERN = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>\"[^\"]*\"|[^ ]+)")


def normalize_syslog(event: SyslogEvent) -> AlertRaised:
    """Normalize one FortiGate key/value syslog message into an alert contract."""

    pairs = _parse_pairs(event.message)
    devname = _get_string(pairs, "devname")
    devid = _get_string(pairs, "devid")
    level = _get_string(pairs, "level") or "warning"
    title = _get_string(pairs, "msg") or "FortiGate syslog event"
    category = _get_string(pairs, "subtype") or _get_string(pairs, "type") or "system"

    asset = build_candidate_asset_ref(
        client_id=event.client_id,
        fabric_id=event.fabric_id,
        vendor="Fortinet",
        model=event.model,
        hostname=event.hostname or devname,
        mgmt_address=event.mgmt_address,
        site=event.site,
        tags=list(event.tags),
    )
    facts = dict(pairs)
    if event.serial is not None:
        facts.setdefault("serial", event.serial)
    if devid is not None:
        facts.setdefault("serial", devid)
    return AlertRaised(
        alert_id=str(uuid4()),
        client_id=event.client_id,
        fabric_id=event.fabric_id,
        asset=asset,
        source_kind="syslog",
        occurred_at=event.occurred_at,
        severity=map_textual_severity(level),
        category=category,
        title=title,
        normalized_facts=facts,
        raw_reference={"format": "fortigate_syslog"},
    )


def normalize_snmp_trap(event: SnmpTrapEvent) -> AlertRaised:
    """Normalize one FortiGate trap into an alert contract."""

    title = str(
        event.varbinds.get("msg")
        or event.varbinds.get("message")
        or f"FortiGate trap {event.trap_oid}"
    )
    level = str(event.varbinds.get("level") or "warning")
    category = str(
        event.varbinds.get("subtype") or event.varbinds.get("type") or "system"
    )
    facts = dict(event.varbinds)
    if event.serial is not None:
        facts.setdefault("serial", event.serial)
    asset = build_candidate_asset_ref(
        client_id=event.client_id,
        fabric_id=event.fabric_id,
        vendor="Fortinet",
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
        severity=map_textual_severity(level),
        category=category,
        title=title,
        normalized_facts=facts,
        raw_reference={"trap_oid": event.trap_oid, "vendor": "fortigate"},
    )


def normalize_snmp_poll(
    target: SnmpV3Target,
    observation: SnmpPollObservation,
) -> HealthSnapshot:
    """Normalize one FortiGate poll observation into a health snapshot."""

    asset = build_candidate_asset_ref(
        client_id=target.client_id,
        fabric_id=target.fabric_id,
        vendor="Fortinet",
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
        cpu_warning=75,
        cpu_critical=90,
        memory_warning=80,
        memory_critical=95,
        extra_warning_metric="session_utilization_percent",
        extra_warning_threshold=80,
        extra_critical_metric="session_utilization_percent",
        extra_critical_threshold=95,
        critical_string_metric=("ha_state", "down"),
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


def _parse_pairs(message: str) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for match in _PAIR_PATTERN.finditer(message):
        key = match.group("key")
        raw_value = match.group("value")
        parsed[key] = raw_value.strip('"')
    if not parsed:
        raise ValueError("unsupported FortiGate syslog format")
    return parsed


def _get_string(pairs: dict[str, object], key: str) -> str | None:
    value = pairs.get(key)
    return value if isinstance(value, str) and value.strip() else None


__all__ = ["normalize_snmp_poll", "normalize_snmp_trap", "normalize_syslog"]
