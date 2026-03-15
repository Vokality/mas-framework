"""Typed alert policy and notification route models."""

from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from mas_msp_contracts import AssetRef, ConfigDesiredState, Severity
from mas_msp_contracts.types import JSONObject, NonEmptyStr


def _metric_default(
    *,
    warning: float,
    critical: float,
    cooldown_seconds: int,
) -> dict[str, object]:
    return {
        "warning": warning,
        "critical": critical,
        "open_after_polls": 3,
        "close_after_polls": 2,
        "cooldown_seconds": cooldown_seconds,
    }


class MetricThresholdPolicy(BaseModel):
    """Thresholds and hysteresis for one numeric metric."""

    warning: float
    critical: float
    open_after_polls: PositiveInt = 3
    close_after_polls: PositiveInt = 2
    cooldown_seconds: PositiveInt = 900

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> MetricThresholdPolicy:
        if self.warning >= self.critical:
            raise ValueError("warning must be lower than critical")
        return self


class HostMetricPolicySet(BaseModel):
    """Configured metric thresholds for one host policy."""

    cpu_percent: MetricThresholdPolicy = Field(
        default_factory=lambda: MetricThresholdPolicy.model_validate(
            _metric_default(warning=80.0, critical=95.0, cooldown_seconds=900)
        )
    )
    memory_percent: MetricThresholdPolicy = Field(
        default_factory=lambda: MetricThresholdPolicy.model_validate(
            _metric_default(warning=75.0, critical=90.0, cooldown_seconds=900)
        )
    )
    disk_percent: MetricThresholdPolicy = Field(
        default_factory=lambda: MetricThresholdPolicy.model_validate(
            _metric_default(warning=85.0, critical=95.0, cooldown_seconds=900)
        )
    )

    model_config = ConfigDict(extra="forbid")


class HostServicePolicy(BaseModel):
    """Watched service behavior for one host policy."""

    watch: list[NonEmptyStr] = Field(default_factory=list)
    open_after_polls: PositiveInt = 1
    close_after_polls: PositiveInt = 1
    cooldown_seconds: PositiveInt = 300

    model_config = ConfigDict(extra="forbid")


class HostAlertPolicy(BaseModel):
    """Host alerting defaults or one host override payload."""

    metrics: HostMetricPolicySet = Field(default_factory=HostMetricPolicySet)
    services: HostServicePolicy = Field(default_factory=HostServicePolicy)

    model_config = ConfigDict(extra="forbid")


class PartialMetricThresholdPolicy(BaseModel):
    """Partial threshold override for one numeric metric."""

    warning: float | None = None
    critical: float | None = None
    open_after_polls: PositiveInt | None = None
    close_after_polls: PositiveInt | None = None
    cooldown_seconds: PositiveInt | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> PartialMetricThresholdPolicy:
        if (
            self.warning is not None
            and self.critical is not None
            and self.warning >= self.critical
        ):
            raise ValueError("warning must be lower than critical")
        return self


class PartialHostMetricPolicySet(BaseModel):
    """Partial metric overrides for one host policy."""

    cpu_percent: PartialMetricThresholdPolicy | None = None
    memory_percent: PartialMetricThresholdPolicy | None = None
    disk_percent: PartialMetricThresholdPolicy | None = None

    model_config = ConfigDict(extra="forbid")


class PartialHostServicePolicy(BaseModel):
    """Partial watched-service overrides for one host policy."""

    watch: list[NonEmptyStr] | None = None
    open_after_polls: PositiveInt | None = None
    close_after_polls: PositiveInt | None = None
    cooldown_seconds: PositiveInt | None = None

    model_config = ConfigDict(extra="forbid")


class PartialHostAlertPolicy(BaseModel):
    """Partial host override payload applied on top of host defaults."""

    metrics: PartialHostMetricPolicySet | None = None
    services: PartialHostServicePolicy | None = None

    model_config = ConfigDict(extra="forbid")


class HostAlertOverrideMatch(BaseModel):
    """Selector used to target one host alert override."""

    asset_id: NonEmptyStr | None = None
    hostname: NonEmptyStr | None = None
    tags_any: list[NonEmptyStr] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_match_shape(self) -> HostAlertOverrideMatch:
        selectors = [
            self.asset_id is not None,
            self.hostname is not None,
            bool(self.tags_any),
        ]
        if sum(selectors) != 1:
            raise ValueError(
                "match must define exactly one of asset_id, hostname, or tags_any"
            )
        return self

    def specificity(self) -> int:
        """Return the precedence weight for this selector."""

        if self.asset_id is not None:
            return 3
        if self.hostname is not None:
            return 2
        return 1


class HostAlertOverride(BaseModel):
    """One host-specific alert policy override."""

    match: HostAlertOverrideMatch
    host: PartialHostAlertPolicy

    model_config = ConfigDict(extra="forbid")


class SourceAlertPolicy(BaseModel):
    """Dedupe policy for push-driven alerts."""

    default_cooldown_seconds: PositiveInt = 300

    model_config = ConfigDict(extra="forbid")


class ChatOpsNotificationRoute(BaseModel):
    """Typed ChatOps route for alert delivery."""

    kind: Literal["chatops"]
    target: NonEmptyStr
    provider: NonEmptyStr
    secret_ref: NonEmptyStr
    severity_min: Severity = Severity.INFO
    category_allowlist: list[NonEmptyStr] = Field(default_factory=list)
    send_resolved: bool = False

    model_config = ConfigDict(extra="forbid")


class EmailNotificationRoute(BaseModel):
    """Typed email route for alert delivery."""

    kind: Literal["email"]
    target: NonEmptyStr
    severity_min: Severity = Severity.INFO
    category_allowlist: list[NonEmptyStr] = Field(default_factory=list)
    send_resolved: bool = False

    model_config = ConfigDict(extra="forbid")


NotificationRoute = Annotated[
    ChatOpsNotificationRoute | EmailNotificationRoute,
    Field(discriminator="kind"),
]


class AppliedAlertConfiguration(BaseModel):
    """Applied alert policy materialized inside one client fabric."""

    client_id: NonEmptyStr
    host_defaults: HostAlertPolicy = Field(default_factory=HostAlertPolicy)
    overrides: list[HostAlertOverride] = Field(default_factory=list)
    source_alerts: SourceAlertPolicy = Field(default_factory=SourceAlertPolicy)
    notification_routes: list[NotificationRoute] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_desired_state(
        cls,
        desired_state: ConfigDesiredState,
    ) -> AppliedAlertConfiguration:
        """Extract alerting configuration from the desired-state document."""

        alerting = _alerting_policy_section(desired_state.policy)
        payload = {
            "client_id": desired_state.client_id,
            "host_defaults": alerting.get("host_defaults", {}),
            "overrides": alerting.get("overrides", []),
            "source_alerts": alerting.get("source_alerts", {}),
            "notification_routes": desired_state.notification_routes,
        }
        return cls.model_validate(payload)

    def resolve_host_policy(self, asset: AssetRef) -> HostAlertPolicy:
        """Return the most specific host policy for the resolved asset."""

        best_match: HostAlertOverride | None = None
        best_specificity = -1
        hostname = asset.hostname.strip().lower() if asset.hostname else None
        asset_tags = {tag.strip().lower() for tag in asset.tags}
        for override in self.overrides:
            match = override.match
            if match.asset_id is not None:
                if match.asset_id != asset.asset_id:
                    continue
            elif match.hostname is not None:
                if hostname is None or match.hostname.lower() != hostname:
                    continue
            else:
                if not asset_tags.intersection({tag.lower() for tag in match.tags_any}):
                    continue
            specificity = match.specificity()
            if specificity > best_specificity:
                best_match = override
                best_specificity = specificity
        if best_match is None:
            return self.host_defaults
        return _merge_host_policy(self.host_defaults, best_match.host)


def _alerting_policy_section(policy: JSONObject) -> dict[str, object]:
    alerting = policy.get("alerting")
    if isinstance(alerting, dict):
        return dict(alerting)
    return {}


def _merge_host_policy(
    defaults: HostAlertPolicy,
    override: PartialHostAlertPolicy,
) -> HostAlertPolicy:
    payload = defaults.model_dump(mode="python")
    override_payload = override.model_dump(mode="python", exclude_none=True)
    _deep_merge(payload, override_payload)
    return HostAlertPolicy.model_validate(payload)


def _deep_merge(base: dict[str, object], overlay: dict[str, object]) -> None:
    for key, value in overlay.items():
        current = base.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _deep_merge(
                cast(dict[str, object], current),
                cast(dict[str, object], value),
            )
            continue
        base[key] = value


__all__ = [
    "AppliedAlertConfiguration",
    "ChatOpsNotificationRoute",
    "EmailNotificationRoute",
    "HostAlertOverride",
    "HostAlertOverrideMatch",
    "HostAlertPolicy",
    "HostMetricPolicySet",
    "HostServicePolicy",
    "MetricThresholdPolicy",
    "NotificationRoute",
    "PartialHostAlertPolicy",
    "PartialHostMetricPolicySet",
    "PartialHostServicePolicy",
    "PartialMetricThresholdPolicy",
    "SourceAlertPolicy",
]
