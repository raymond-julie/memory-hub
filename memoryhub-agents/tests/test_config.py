"""Tests for memoryhub_agents.config."""

import os

import pytest

from memoryhub_agents.config import AgentConfig


class TestAgentConfigDefaults:
    """AgentConfig loads sensible defaults when no env vars are set."""

    def test_default_agent_type(self, monkeypatch):
        monkeypatch.delenv("AGENT_TYPE", raising=False)
        cfg = AgentConfig()
        assert cfg.agent_type == "unknown"

    def test_default_valkey_url(self, monkeypatch):
        monkeypatch.delenv("VALKEY_URL", raising=False)
        cfg = AgentConfig()
        assert cfg.valkey_url == "redis://memoryhub-valkey:6379"

    def test_default_poll_interval(self, monkeypatch):
        monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
        cfg = AgentConfig()
        assert cfg.poll_interval_seconds == 5.0

    def test_default_max_retries(self, monkeypatch):
        monkeypatch.delenv("MAX_RETRIES", raising=False)
        cfg = AgentConfig()
        assert cfg.max_retries == 5

    def test_default_tenant_id(self, monkeypatch):
        monkeypatch.delenv("TENANT_ID", raising=False)
        cfg = AgentConfig()
        assert cfg.tenant_id == "default"


class TestAgentConfigFromEnv:
    """AgentConfig reads values from environment variables."""

    def test_reads_agent_type(self, monkeypatch):
        monkeypatch.setenv("AGENT_TYPE", "curator")
        cfg = AgentConfig()
        assert cfg.agent_type == "curator"

    def test_reads_mcp_url(self, monkeypatch):
        monkeypatch.setenv("MH_MCP_URL", "https://mcp.test/mcp/")
        cfg = AgentConfig()
        assert cfg.mcp_url == "https://mcp.test/mcp/"

    def test_reads_numeric_fields(self, monkeypatch):
        monkeypatch.setenv("DAILY_TOKEN_BUDGET", "50000")
        monkeypatch.setenv("POLL_INTERVAL_SECONDS", "2.5")
        monkeypatch.setenv("MAX_RETRIES", "3")
        cfg = AgentConfig()
        assert cfg.daily_token_budget == 50000
        assert cfg.poll_interval_seconds == 2.5
        assert cfg.max_retries == 3


class TestAgentConfigProperties:
    """Derived properties generate correct Valkey key patterns."""

    def test_queue_key(self, monkeypatch):
        monkeypatch.setenv("AGENT_TYPE", "tracer")
        monkeypatch.setenv("TENANT_ID", "acme")
        cfg = AgentConfig()
        assert cfg.queue_key == "tracer_queue:acme"

    def test_lock_key(self, monkeypatch):
        monkeypatch.setenv("AGENT_TYPE", "curator")
        monkeypatch.setenv("TENANT_ID", "acme")
        cfg = AgentConfig()
        assert cfg.lock_key == "agent_lock:curator:acme"

    def test_frozen(self, monkeypatch):
        monkeypatch.setenv("AGENT_TYPE", "tracer")
        cfg = AgentConfig()
        with pytest.raises(AttributeError):
            cfg.agent_type = "curator"  # type: ignore[misc]
