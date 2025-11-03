"""Tests for Gateway Configuration System."""

import os
import tempfile

import pytest
import yaml

from mas.gateway.config import (
    GatewaySettings,
    RedisSettings,
    RateLimitSettings,
    FeaturesSettings,
    CircuitBreakerSettings,
    PriorityQueueSettings,
    MessageSigningSettings,
    load_settings,
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
        """Test default feature flags (production-ready defaults)."""
        settings = FeaturesSettings()

        # All features enabled by default for production security
        assert settings.dlp is True
        assert settings.priority_queue is True
        assert settings.rbac is True
        assert settings.message_signing is True
        assert settings.circuit_breaker is True

    def test_enable_all_features(self):
        """Test enabling all features."""
        settings = FeaturesSettings(
            dlp=True,
            priority_queue=True,
            rbac=True,
            message_signing=True,
            circuit_breaker=True,
        )

        assert all(
            [
                settings.dlp,
                settings.priority_queue,
                settings.rbac,
                settings.message_signing,
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


class TestPriorityQueueSettings:
    """Test priority queue configuration."""

    def test_default_priority_queue(self):
        """Test default priority queue settings."""
        settings = PriorityQueueSettings()

        assert settings.default_ttl == 300
        assert settings.weights == {
            "CRITICAL": 10,
            "HIGH": 5,
            "NORMAL": 2,
            "LOW": 1,
            "BULK": 0,
        }

    def test_custom_weights(self):
        """Test custom priority weights."""
        settings = PriorityQueueSettings(
            critical_weight=20,
            high_weight=10,
            normal_weight=5,
            low_weight=2,
            bulk_weight=1,
        )

        assert settings.weights == {
            "CRITICAL": 20,
            "HIGH": 10,
            "NORMAL": 5,
            "LOW": 2,
            "BULK": 1,
        }


class TestMessageSigningSettings:
    """Test message signing configuration."""

    def test_default_message_signing(self):
        """Test default message signing settings."""
        settings = MessageSigningSettings()

        assert settings.max_timestamp_drift == 300
        assert settings.nonce_ttl == 300

    def test_custom_message_signing(self):
        """Test custom message signing settings."""
        settings = MessageSigningSettings(max_timestamp_drift=600, nonce_ttl=600)

        assert settings.max_timestamp_drift == 600
        assert settings.nonce_ttl == 600


class TestGatewaySettings:
    """Test main gateway configuration."""

    def test_default_gateway_settings(self):
        """Test default gateway settings (production-ready)."""
        settings = GatewaySettings()

        assert settings.redis.url == "redis://localhost:6379"
        assert settings.rate_limit.per_minute == 100
        assert settings.features.dlp is True
        assert settings.features.priority_queue is True
        assert settings.features.rbac is True
        assert settings.features.message_signing is True
        assert settings.features.circuit_breaker is True

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
                message_signing=True,
                circuit_breaker=True,
                priority_queue=True,
            )
        )
        summary = settings.summary()

        assert "DLP: ✓" in summary
        assert "RBAC: ✓" in summary
        assert "Message Signing: ✓" in summary
        assert "Circuit Breaker: ✓" in summary
        assert "Priority Queue: ✓" in summary
