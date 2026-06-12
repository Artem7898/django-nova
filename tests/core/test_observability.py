"""Tests for structured observability layer."""
from __future__ import annotations

import json
import logging
import structlog
import pytest

from nova.core.observability import setup_nova_logging, get_logger


class TestObservabilitySetup:
    """Test suite for structlog configuration."""

    def test_setup_configures_structlog(self) -> None:
        """setup_nova_logging must configure structlog processors."""
        setup_nova_logging()
        assert structlog.is_configured()

    def test_get_logger_returns_bound_logger(self) -> None:
        """get_logger must return a structlog BoundLogger."""
        setup_nova_logging()
        logger = get_logger("nova.cache")
        assert hasattr(logger, "info")
        assert hasattr(logger, "debug")
        assert hasattr(logger, "error")

    def test_logger_outputs_json_event(self, caplog: pytest.LogCaptureFixture) -> None:
        """
        Logger must output structured data that can be parsed as JSON.
        This is the core requirement for ELK/Datadog ingestion.
        """
        setup_nova_logging()
        logger = get_logger("nova.test")
        
        with caplog.at_level(logging.INFO):
            logger.info("cache_hit", model="Article", pk=42, cache_key="abc123")
        
        log_record = caplog.records[0]
        parsed_log = json.loads(log_record.message)
        
        assert parsed_log["event"] == "cache_hit"
        assert parsed_log["model"] == "Article"
        assert parsed_log["pk"] == 42
        assert parsed_log["cache_key"] == "abc123"
        assert "timestamp" in parsed_log
