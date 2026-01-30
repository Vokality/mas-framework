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

    Secure-by-default feature flags for message enforcement.

    Defaults are strict and simple:
    - DLP: enabled
    - RBAC: disabled (ACL only unless explicitly enabled)
    - Circuit Breaker: enabled
    """

    dlp: bool = Field(default=True, description="Enable DLP scanning")
    rbac: bool = Field(default=False, description="Enable RBAC authorization")
    circuit_breaker: bool = Field(default=True, description="Enable circuit breakers")

    model_config = SettingsConfigDict(env_prefix="GATEWAY_FEATURES_")


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
        merged = data.pop("_merged", False)
        if merged:
            super().__init__(**data)
            return

        config_file = self._resolve_config_file(data)
        if config_file:
            merged_data = self._merge_yaml(config_file, data)
            super().__init__(**merged_data)
        else:
            super().__init__(**data)

    @classmethod
    def _resolve_config_file(cls, data: dict[str, Any]) -> Optional[str]:
        """Resolve config file from parameters or environment."""
        return data.get("config_file") or os.getenv("GATEWAY_CONFIG_FILE")

    @classmethod
    def _merge_yaml(cls, config_file: str, data: dict[str, Any]) -> dict[str, Any]:
        """Load YAML config and merge with explicit parameters."""
        yaml_data = cls._load_yaml(config_file)
        merged_data = {**yaml_data, **data}
        merged_data.setdefault("config_file", config_file)
        return merged_data

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
        merged = cls._merge_yaml(file_path, {})
        return cls(_merged=True, **merged)

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

        # Load from file (standalone usage)
        settings = load_settings(config_file="gateway.yaml")

        # Load with overrides (standalone usage)
        settings = load_settings(
            config_file="gateway.yaml",
            redis={"url": "redis://override:6379"},
        )
    """
    if config_file:
        overrides["config_file"] = config_file

    return GatewaySettings(**overrides)
