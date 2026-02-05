"""Tests for Gateway Configuration System."""

import os
import tempfile

import pytest
import yaml

from mas.gateway.config import (
    CircuitBreakerSettings,
    FeaturesSettings,
    GatewaySettings,
    RateLimitSettings,
    RedisSettings,
    TelemetrySettings,
    load_settings,
    validate_gateway_config,
)


class TestRedisSettings:
    """Test Redis configuration settings."""

    def test_default_settings(self):
        """Test default Redis settings."""
        settings = RedisSettings()

        assert settings.url == "redis://localhost:6379"
        assert settings.decode_responses is True
        assert settings.socket_timeout is None

    def test_custom_settings(self):
        """Test custom Redis settings."""
        settings = RedisSettings(
            url="redis://prod:6379",
            decode_responses=False,
            socket_timeout=30.0,
        )

        assert settings.url == "redis://prod:6379"
        assert settings.decode_responses is False
        assert settings.socket_timeout == 30.0


class TestRateLimitSettings:
    """Test rate limiting configuration."""

    def test_default_rate_limits(self):
        """Test default rate limits."""
        settings = RateLimitSettings()

        assert settings.per_minute == 100
        assert settings.per_hour == 1000

    def test_custom_rate_limits(self):
        """Test custom rate limits."""
        settings = RateLimitSettings(per_minute=200, per_hour=2000)

        assert settings.per_minute == 200
        assert settings.per_hour == 2000


class TestFeaturesSettings:
    """Test feature flags configuration."""

    def test_default_features(self):
        """Test default feature flags."""
        settings = FeaturesSettings()

        assert settings.dlp is True
        assert settings.rbac is False
        assert settings.circuit_breaker is True

    def test_enable_all_features(self):
        """Test enabling all features."""
        settings = FeaturesSettings(
            dlp=True,
            rbac=True,
            circuit_breaker=True,
        )

        assert all(
            [
                settings.dlp,
                settings.rbac,
                settings.circuit_breaker,
            ]
        )


class TestCircuitBreakerSettings:
    """Test circuit breaker configuration."""

    def test_default_circuit_breaker(self):
        """Test default circuit breaker settings."""
        settings = CircuitBreakerSettings()

        assert settings.failure_threshold == 5
        assert settings.success_threshold == 2
        assert settings.timeout_seconds == 60.0
        assert settings.window_seconds == 300.0

    def test_custom_circuit_breaker(self):
        """Test custom circuit breaker settings."""
        settings = CircuitBreakerSettings(
            failure_threshold=10,
            success_threshold=3,
            timeout_seconds=120.0,
            window_seconds=600.0,
        )

        assert settings.failure_threshold == 10
        assert settings.success_threshold == 3
        assert settings.timeout_seconds == 120.0
        assert settings.window_seconds == 600.0


class TestTelemetrySettings:
    """Test OpenTelemetry configuration."""

    def test_default_telemetry_settings(self):
        """Test telemetry defaults."""
        settings = TelemetrySettings()

        assert settings.enabled is False
        assert settings.service_name == "mas-framework"
        assert settings.service_namespace == "mas"
        assert settings.environment == "dev"
        assert settings.otlp_endpoint is None
        assert settings.sample_ratio == 1.0
        assert settings.export_metrics is True
        assert settings.metrics_export_interval_ms == 60000
        assert settings.headers == {}

    def test_custom_telemetry_settings(self):
        """Test custom telemetry settings."""
        settings = TelemetrySettings(
            enabled=True,
            service_name="custom-mas",
            service_namespace="platform",
            environment="prod",
            otlp_endpoint="http://otel-collector:4318",
            sample_ratio=0.25,
            export_metrics=False,
            metrics_export_interval_ms=15000,
            headers={"authorization": "Bearer token"},
        )

        assert settings.enabled is True
        assert settings.service_name == "custom-mas"
        assert settings.service_namespace == "platform"
        assert settings.environment == "prod"
        assert settings.otlp_endpoint == "http://otel-collector:4318"
        assert settings.sample_ratio == 0.25
        assert settings.export_metrics is False
        assert settings.metrics_export_interval_ms == 15000
        assert settings.headers == {"authorization": "Bearer token"}


class TestGatewaySettings:
    """Test main gateway configuration."""

    def test_default_gateway_settings(self):
        """Test default gateway settings."""
        settings = GatewaySettings()

        assert settings.redis.url == "redis://localhost:6379"
        assert settings.rate_limit.per_minute == 100
        assert settings.features.dlp is True
        assert settings.features.rbac is False
        assert settings.features.circuit_breaker is True
        assert settings.telemetry.enabled is False

    def test_custom_gateway_settings(self):
        """Test custom gateway settings."""
        settings = GatewaySettings(
            redis=RedisSettings(url="redis://custom:6379"),
            rate_limit=RateLimitSettings(per_minute=200),
            features=FeaturesSettings(dlp=False, rbac=True),
        )

        assert settings.redis.url == "redis://custom:6379"
        assert settings.rate_limit.per_minute == 200
        assert settings.features.dlp is False
        assert settings.features.rbac is True

    def test_nested_dict_initialization(self):
        """Test initialization with nested dictionaries."""
        settings = GatewaySettings(
            redis={"url": "redis://dict:6379"},
            rate_limit={"per_minute": 150},
        )

        assert settings.redis.url == "redis://dict:6379"
        assert settings.rate_limit.per_minute == 150

    def test_unknown_gateway_key_rejected(self):
        """Test unknown gateway keys are rejected."""
        with pytest.raises(ValueError, match="Unknown keys in gateway"):
            validate_gateway_config({"unknown_setting": True})

    def test_unknown_telemetry_key_rejected(self):
        """Test unknown telemetry keys are rejected."""
        with pytest.raises(ValueError, match="Unknown keys in gateway.telemetry"):
            validate_gateway_config({"telemetry": {"unknown": True}})


class TestYAMLConfiguration:
    """Test YAML configuration loading."""

    def test_load_from_yaml(self):
        """Test loading configuration from YAML file."""
        yaml_content = """
redis:
  url: redis://yaml:6379

rate_limit:
  per_minute: 250
  per_hour: 2500

features:
  dlp: false
  rbac: true

circuit_breaker:
  failure_threshold: 10
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            config_file = f.name

        try:
            settings = GatewaySettings.from_yaml(config_file)

            assert settings.redis.url == "redis://yaml:6379"
            assert settings.rate_limit.per_minute == 250
            assert settings.rate_limit.per_hour == 2500
            assert settings.features.dlp is False
            assert settings.features.rbac is True
            assert settings.circuit_breaker.failure_threshold == 10
        finally:
            os.unlink(config_file)

    def test_yaml_with_overrides(self):
        """Test YAML config with parameter overrides."""
        yaml_content = """
redis:
  url: redis://yaml:6379

rate_limit:
  per_minute: 100
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            config_file = f.name

        try:
            # Parameter overrides should take precedence
            settings = GatewaySettings(
                config_file=config_file,
                rate_limit=RateLimitSettings(per_minute=200),
            )

            assert settings.redis.url == "redis://yaml:6379"
            assert settings.rate_limit.per_minute == 200  # Override
        finally:
            os.unlink(config_file)

    def test_nonexistent_yaml_file(self):
        """Test loading from nonexistent YAML file."""
        with pytest.raises(FileNotFoundError):
            GatewaySettings.from_yaml("/nonexistent/config.yaml")

    def test_export_to_yaml(self):
        """Test exporting settings to YAML."""
        settings = GatewaySettings(
            redis=RedisSettings(url="redis://export:6379"),
            rate_limit=RateLimitSettings(per_minute=150),
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            output_file = f.name

        try:
            settings.to_yaml(output_file)

            # Load it back and verify
            with open(output_file) as f:
                data = yaml.safe_load(f)

            assert data["redis"]["url"] == "redis://export:6379"
            assert data["rate_limit"]["per_minute"] == 150
        finally:
            os.unlink(output_file)


class TestLoadSettings:
    """Test convenience load_settings function."""

    def test_load_settings_defaults(self):
        """Test loading settings with defaults."""
        settings = load_settings()

        assert isinstance(settings, GatewaySettings)
        assert settings.redis.url == "redis://localhost:6379"

    def test_load_settings_with_overrides(self):
        """Test loading settings with overrides."""
        settings = load_settings(redis={"url": "redis://override:6379"})

        assert settings.redis.url == "redis://override:6379"

    def test_load_settings_from_file(self):
        """Test loading settings from config file."""
        yaml_content = """
redis:
  url: redis://file:6379
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            config_file = f.name

        try:
            settings = load_settings(config_file=config_file)
            assert settings.redis.url == "redis://file:6379"
        finally:
            os.unlink(config_file)


class TestSettingsSummary:
    """Test settings summary output."""

    def test_summary_basic(self):
        """Test basic settings summary."""
        settings = GatewaySettings()
        summary = settings.summary()

        assert "Gateway Configuration:" in summary
        assert "redis://localhost:6379" in summary
        assert "100/min" in summary
        assert "1000/hour" in summary

    def test_summary_with_features(self):
        """Test summary with enabled features."""
        settings = GatewaySettings(
            features=FeaturesSettings(
                dlp=True,
                rbac=True,
                circuit_breaker=True,
            )
        )
        summary = settings.summary()

        assert "DLP: ✓" in summary
        assert "RBAC: ✓" in summary
        assert "Circuit Breaker: ✓" in summary
