"""Gateway Configuration Module."""

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(env_prefix="GATEWAY_CIRCUIT_BREAKER_")


class PriorityQueueSettings(BaseSettings):
    """Priority queue configuration settings."""

    default_ttl: int = Field(
        default=300, ge=0, description="Default message TTL in seconds"
    )
    critical_weight: int = Field(
        default=10, ge=0, description="Weight for CRITICAL priority"
    )
    high_weight: int = Field(default=5, ge=0, description="Weight for HIGH priority")
    normal_weight: int = Field(
        default=2, ge=0, description="Weight for NORMAL priority"
    )
    low_weight: int = Field(default=1, ge=0, description="Weight for LOW priority")
    bulk_weight: int = Field(default=0, ge=0, description="Weight for BULK priority")

    model_config = SettingsConfigDict(env_prefix="GATEWAY_PRIORITY_QUEUE_")

    @property
    def weights(self) -> dict[str, int]:
        """Get priority weights as a dictionary."""
        return {
            "CRITICAL": self.critical_weight,
            "HIGH": self.high_weight,
            "NORMAL": self.normal_weight,
            "LOW": self.low_weight,
            "BULK": self.bulk_weight,
        }


class MessageSigningSettings(BaseSettings):
    """Message signing configuration settings."""

    max_timestamp_drift: int = Field(
        default=300, ge=0, description="Max timestamp drift in seconds"
    )
    nonce_ttl: int = Field(default=300, ge=0, description="Nonce TTL in seconds")

    model_config = SettingsConfigDict(env_prefix="GATEWAY_MESSAGE_SIGNING_")


class RateLimitSettings(BaseSettings):
    """Rate limiting configuration settings."""

    per_minute: int = Field(default=100, ge=0, description="Messages per minute")
    per_hour: int = Field(default=1000, ge=0, description="Messages per hour")

    model_config = SettingsConfigDict(env_prefix="GATEWAY_RATE_LIMIT_")


class RedisSettings(BaseSettings):
    """Redis configuration settings."""

    url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL",
    )
    decode_responses: bool = Field(
        default=True, description="Decode Redis responses to strings"
    )
    socket_timeout: Optional[float] = Field(
        default=None, description="Socket timeout in seconds"
    )

    model_config = SettingsConfigDict(env_prefix="GATEWAY_REDIS_")


class FeaturesSettings(BaseSettings):
    """
    Feature flags configuration.

    Production-ready defaults (all security features enabled):
    - DLP: True (data loss prevention)
    - Priority Queue: True (message prioritization)
    - RBAC: True (role-based access control)
    - Message Signing: True (integrity verification)
    - Circuit Breaker: True (reliability)

    For development/testing, you may want to disable features:
    - Set GATEWAY_FEATURES__RBAC=false for simple ACL-only mode
    - Set GATEWAY_FEATURES__MESSAGE_SIGNING=false to skip signing
    - Set GATEWAY_FEATURES__DLP=false for faster testing
    """

    dlp: bool = Field(default=True, description="Enable DLP scanning")
    priority_queue: bool = Field(default=True, description="Enable priority queues")
    rbac: bool = Field(default=True, description="Enable RBAC authorization (Phase 2)")
    message_signing: bool = Field(
        default=True, description="Enable message signing (Phase 2)"
    )
    circuit_breaker: bool = Field(default=True, description="Enable circuit breakers")

    model_config = SettingsConfigDict(env_prefix="GATEWAY_FEATURES_")


class GatewaySettings(BaseSettings):
    """
    Gateway service configuration.

    Configuration can be loaded from:
    1. Environment variables (GATEWAY_*)
    2. .env file
    3. YAML config file (specified via GATEWAY_CONFIG_FILE)
    4. Direct instantiation with parameters

    Priority (highest to lowest):
    1. Explicitly passed parameters
    2. Environment variables
    3. Config file
    4. Defaults

    Example usage:

        # From environment variables
        settings = GatewaySettings()

        # From config file
        settings = GatewaySettings(config_file="gateway.yaml")

        # Direct configuration
        settings = GatewaySettings(
            redis=RedisSettings(url="redis://prod:6379"),
            rate_limit=RateLimitSettings(per_minute=200),
        )
    """

    # Config file path
    config_file: Optional[str] = Field(
        default=None,
        description="Path to YAML config file",
    )

    # Module configurations
    redis: RedisSettings = Field(default_factory=RedisSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    features: FeaturesSettings = Field(default_factory=FeaturesSettings)
    circuit_breaker: CircuitBreakerSettings = Field(
        default_factory=CircuitBreakerSettings
    )
    priority_queue: PriorityQueueSettings = Field(default_factory=PriorityQueueSettings)
    message_signing: MessageSigningSettings = Field(
        default_factory=MessageSigningSettings
    )

    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def __init__(self, **data: Any):
        """
        Initialize settings.

        If config_file is provided or GATEWAY_CONFIG_FILE env var is set,
        load configuration from YAML file and merge with other sources.
        """
        # Check for config file in environment
        config_file = data.get("config_file") or os.getenv("GATEWAY_CONFIG_FILE")

        # Load from YAML if config file specified
        if config_file:
            yaml_data = self._load_yaml(config_file)
            # Merge YAML data with passed data (passed data takes precedence)
            merged_data = {**yaml_data, **data}
            super().__init__(**merged_data)
        else:
            super().__init__(**data)

    @staticmethod
    def _load_yaml(file_path: str) -> dict[str, Any]:
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
            data = yaml.safe_load(f)

        if data is None:
            return {}

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
            f"  Rate Limits: {self.rate_limit.per_minute}/min, {self.rate_limit.per_hour}/hour",
            "",
            "Features:",
            f"  DLP: {'✓' if self.features.dlp else '✗'}",
            f"  Priority Queue: {'✓' if self.features.priority_queue else '✗'}",
            f"  RBAC: {'✓' if self.features.rbac else '✗'}",
            f"  Message Signing: {'✓' if self.features.message_signing else '✗'}",
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

        if self.features.priority_queue:
            lines.extend(
                [
                    "",
                    "Priority Queue:",
                    f"  Default TTL: {self.priority_queue.default_ttl}s",
                    f"  Weights: CRITICAL={self.priority_queue.critical_weight}, "
                    f"HIGH={self.priority_queue.high_weight}, "
                    f"NORMAL={self.priority_queue.normal_weight}",
                ]
            )

        if self.features.message_signing:
            lines.extend(
                [
                    "",
                    "Message Signing:",
                    f"  Max Timestamp Drift: {self.message_signing.max_timestamp_drift}s",
                    f"  Nonce TTL: {self.message_signing.nonce_ttl}s",
                ]
            )

        return "\n".join(lines)


# Convenience function to load settings
def load_settings(
    config_file: Optional[str] = None, **overrides: Any
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

        # Load from file
        settings = load_settings(config_file="gateway.yaml")

        # Load with overrides
        settings = load_settings(
            config_file="gateway.yaml",
            redis={"url": "redis://override:6379"},
        )
    """
    if config_file:
        overrides["config_file"] = config_file

    return GatewaySettings(**overrides)
