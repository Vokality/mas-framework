"""Gateway Configuration Module."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, TypeAdapter, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .dlp import ActionPolicy, DlpRule

_CONFIG_MAPPING_ADAPTER = TypeAdapter(dict[str, object])


class CircuitBreakerSettings(BaseSettings):
    """Circuit breaker configuration settings."""

    failure_threshold: int = Field(
        default=5, ge=1, description="Failures before opening circuit"
    )
    success_threshold: int = Field(
        default=2, ge=1, description="Successes before closing circuit"
    )
    timeout_seconds: float = Field(
        default=60.0, gt=0, description="Timeout before half-open"
    )
    window_seconds: float = Field(
        default=300.0, gt=0, description="Failure counting window"
    )

    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_CIRCUIT_BREAKER_", extra="forbid"
    )


class RateLimitSettings(BaseSettings):
    """Rate limiting configuration settings."""

    per_minute: int = Field(default=100, ge=0, description="Messages per minute")
    per_hour: int = Field(default=1000, ge=0, description="Messages per hour")

    model_config = SettingsConfigDict(env_prefix="GATEWAY_RATE_LIMIT_", extra="forbid")


class RedisSettings(BaseSettings):
    """Redis configuration settings."""

    url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL",
    )
    socket_timeout: float | None = Field(
        default=None, description="Socket timeout in seconds"
    )

    model_config = SettingsConfigDict(env_prefix="GATEWAY_REDIS_", extra="forbid")


class FeaturesSettings(BaseSettings):
    """
    Feature flags configuration.

    Secure-by-default feature flags for message enforcement.

    Defaults are strict and simple:
    - DLP: enabled
    - RBAC: disabled (ACL only unless explicitly enabled)
    - Circuit Breaker: enabled
    """

    dlp: bool = Field(default=True, description="Enable DLP scanning")
    rbac: bool = Field(default=False, description="Enable RBAC authorization")
    circuit_breaker: bool = Field(default=True, description="Enable circuit breakers")

    model_config = SettingsConfigDict(env_prefix="GATEWAY_FEATURES_", extra="forbid")


class DlpSettings(BaseSettings):
    """DLP configuration settings."""

    merge_strategy: Literal["append", "replace"] = Field(
        default="append",
        description="How to combine default and custom DLP rules",
    )
    disable_defaults: list[str] = Field(
        default_factory=list,
        description="Default rule types to disable",
    )
    policy_overrides: dict[str, ActionPolicy] = Field(
        default_factory=dict,
        description="Override action policies per violation type",
    )
    rules: list[DlpRule] = Field(
        default_factory=list,
        description="Additional DLP rules",
    )

    model_config = SettingsConfigDict(env_prefix="GATEWAY_DLP_", extra="forbid")


class AuditSettings(BaseSettings):
    """Audit log configuration settings."""

    file_path: str | None = Field(
        default=None,
        description="Optional JSONL audit log path",
    )
    max_bytes: int = Field(
        default=10_000_000,
        ge=1,
        description="Max audit file size before rotation",
    )
    backup_count: int = Field(
        default=5,
        ge=0,
        description="Number of rotated audit files to keep",
    )

    model_config = SettingsConfigDict(env_prefix="GATEWAY_AUDIT_", extra="forbid")


class TelemetrySettings(BaseSettings):
    """OpenTelemetry configuration settings."""

    enabled: bool = Field(default=False, description="Enable OpenTelemetry")
    service_name: str = Field(
        default="mas-framework", description="OTel service.name resource attribute"
    )
    service_namespace: str = Field(
        default="mas", description="OTel service.namespace resource attribute"
    )
    environment: str = Field(
        default="dev", description="OTel deployment.environment resource attribute"
    )
    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP/HTTP endpoint (for example http://localhost:4318)",
    )
    sample_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Trace sampling ratio",
    )
    export_metrics: bool = Field(
        default=True,
        description="Export OTel metrics via OTLP",
    )
    metrics_export_interval_ms: int = Field(
        default=60_000,
        ge=1_000,
        description="OTLP metric export interval in milliseconds",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Optional OTLP exporter headers",
    )

    model_config = SettingsConfigDict(env_prefix="GATEWAY_TELEMETRY_", extra="forbid")


class GatewaySettings(BaseSettings):
    """
    Gateway service configuration.

    Configuration can be loaded from:
    1. Environment variables (GATEWAY_*)
    2. .env file
    3. YAML config file (standalone usage via GATEWAY_CONFIG_FILE)
    4. Direct instantiation with parameters

    Priority (highest to lowest):
    1. Explicitly passed parameters
    2. Environment variables
    3. Config file
    4. Defaults

    Example usage:

        # From environment variables
        settings = GatewaySettings()

        # From config file (standalone usage)
        settings = GatewaySettings(config_file="gateway.yaml")

        # Direct configuration
        settings = GatewaySettings(
            redis=RedisSettings(url="redis://prod:6379"),
            rate_limit=RateLimitSettings(per_minute=200),
        )
    """

    # Config file path
    config_file: str | None = Field(
        default=None,
        description="Path to YAML config file",
    )

    # Module configurations
    redis: RedisSettings = Field(default_factory=RedisSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    features: FeaturesSettings = Field(default_factory=FeaturesSettings)
    dlp: DlpSettings = Field(default_factory=DlpSettings)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    circuit_breaker: CircuitBreakerSettings = Field(
        default_factory=CircuitBreakerSettings
    )

    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _merge_config_file(cls, raw_data: object) -> object:
        """Merge YAML config before Pydantic validates settings fields."""
        if not isinstance(raw_data, Mapping):
            return raw_data

        data = _CONFIG_MAPPING_ADAPTER.validate_python(raw_data)
        config_file = cls._resolve_config_file(data)
        if not config_file:
            return data
        return cls._merge_yaml(config_file, data)

    @classmethod
    def _resolve_config_file(cls, data: Mapping[str, object]) -> str | None:
        """Resolve config file from parameters or environment."""
        config_file = data.get("config_file")
        if isinstance(config_file, str):
            return config_file
        return os.getenv("GATEWAY_CONFIG_FILE")

    @classmethod
    def _merge_yaml(
        cls, config_file: str, data: Mapping[str, object]
    ) -> dict[str, object]:
        """Load YAML config and merge with explicit parameters."""
        yaml_data = cls._load_yaml(config_file)
        merged_data = {**yaml_data, **data}
        merged_data.setdefault("config_file", config_file)
        return merged_data

    @staticmethod
    def _load_yaml(file_path: str) -> dict[str, object]:
        """
        Load configuration from YAML file.

        Args:
            file_path: Path to YAML file

        Returns:
            Dictionary with configuration data

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If YAML is invalid
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {file_path}")

        with path.open("r") as f:
            raw_data: object = yaml.safe_load(f)

        if raw_data is None:
            return {}

        if not isinstance(raw_data, Mapping):
            raise ValueError("Gateway config must be a mapping")

        data = _CONFIG_MAPPING_ADAPTER.validate_python(raw_data)
        validate_gateway_config(data)
        return data

    @classmethod
    def from_yaml(cls, file_path: str) -> "GatewaySettings":
        """
        Create settings from YAML file.

        Args:
            file_path: Path to YAML file

        Returns:
            GatewaySettings instance
        """
        return cls(config_file=file_path)

    def to_yaml(self, file_path: str) -> None:
        """
        Export settings to YAML file.

        Args:
            file_path: Path to output YAML file
        """
        # Convert to dict, excluding None values
        data = self.model_dump(exclude_none=True, exclude={"config_file"})

        path = Path(file_path)
        with path.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def summary(self) -> str:
        """
        Get human-readable configuration summary.

        Returns:
            Formatted configuration summary
        """
        lines = [
            "Gateway Configuration:",
            f"  Redis: {self.redis.url}",
            "  Rate Limits: "
            f"{self.rate_limit.per_minute}/min, {self.rate_limit.per_hour}/hour",
            f"  Telemetry: {'enabled' if self.telemetry.enabled else 'disabled'}",
            "",
            "Features:",
            f"  DLP: {'✓' if self.features.dlp else '✗'}",
            f"  RBAC: {'✓' if self.features.rbac else '✗'}",
            f"  Circuit Breaker: {'✓' if self.features.circuit_breaker else '✗'}",
        ]

        if self.features.circuit_breaker:
            lines.extend(
                [
                    "",
                    "Circuit Breaker:",
                    f"  Failure Threshold: {self.circuit_breaker.failure_threshold}",
                    f"  Timeout: {self.circuit_breaker.timeout_seconds}s",
                ]
            )

        return "\n".join(lines)


# Convenience function to load settings
def load_settings(
    config_file: str | None = None,
    *,
    redis: RedisSettings | Mapping[str, object] | None = None,
    rate_limit: RateLimitSettings | Mapping[str, object] | None = None,
    features: FeaturesSettings | Mapping[str, object] | None = None,
    dlp: DlpSettings | Mapping[str, object] | None = None,
    audit: AuditSettings | Mapping[str, object] | None = None,
    telemetry: TelemetrySettings | Mapping[str, object] | None = None,
    circuit_breaker: CircuitBreakerSettings | Mapping[str, object] | None = None,
) -> GatewaySettings:
    """
    Load gateway settings with optional overrides.

    Args:
        config_file: Optional path to YAML config file
        **overrides: Optional setting overrides

    Returns:
        GatewaySettings instance

    Example:
        # Load from environment
        settings = load_settings()

        # Load from file (standalone usage)
        settings = load_settings(config_file="gateway.yaml")

        # Load with overrides (standalone usage)
        settings = load_settings(
            config_file="gateway.yaml",
            redis={"url": "redis://override:6379"},
        )
    """
    settings = GatewaySettings(config_file=config_file)
    updates: dict[str, object] = {}
    if redis is not None:
        updates["redis"] = RedisSettings.model_validate(redis)
    if rate_limit is not None:
        updates["rate_limit"] = RateLimitSettings.model_validate(rate_limit)
    if features is not None:
        updates["features"] = FeaturesSettings.model_validate(features)
    if dlp is not None:
        updates["dlp"] = DlpSettings.model_validate(dlp)
    if audit is not None:
        updates["audit"] = AuditSettings.model_validate(audit)
    if telemetry is not None:
        updates["telemetry"] = TelemetrySettings.model_validate(telemetry)
    if circuit_breaker is not None:
        updates["circuit_breaker"] = CircuitBreakerSettings.model_validate(
            circuit_breaker
        )

    if not updates:
        return settings
    return settings.model_copy(update=updates)


def validate_gateway_config(data: Mapping[str, object]) -> None:
    """Validate gateway config keys against known settings."""
    gateway_keys = set(GatewaySettings.model_fields)
    unknown = {
        key
        for key in set(data) - gateway_keys
        if key.upper() not in os.environ and f"GATEWAY_{key.upper()}" not in os.environ
    }
    if unknown:
        unknown_text = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown keys in gateway: {unknown_text}")

    for field_name, settings_type in (
        ("redis", RedisSettings),
        ("rate_limit", RateLimitSettings),
        ("features", FeaturesSettings),
        ("dlp", DlpSettings),
        ("audit", AuditSettings),
        ("telemetry", TelemetrySettings),
        ("circuit_breaker", CircuitBreakerSettings),
    ):
        value = data.get(field_name)
        if isinstance(value, Mapping):
            try:
                settings_type.model_validate(value)
            except ValueError as exc:
                raise ValueError(f"Unknown keys in gateway.{field_name}") from exc
