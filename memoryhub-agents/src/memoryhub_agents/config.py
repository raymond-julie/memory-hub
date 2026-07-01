"""Environment-based agent configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for a curation agent, loaded from environment variables.

    Every field has a sensible default or reads from a well-known env var,
    so agents can be configured entirely through the pod spec without any
    config files.
    """

    agent_type: str = field(
        default_factory=lambda: os.environ.get("AGENT_TYPE", "unknown"),
    )
    agent_id: str = field(
        default_factory=lambda: os.environ.get("AGENT_ID", ""),
    )

    # MCP connection
    mcp_url: str = field(
        default_factory=lambda: os.environ.get("MH_MCP_URL", ""),
    )
    api_key: str = field(
        default_factory=lambda: os.environ.get("MH_API_KEY", ""),
    )

    # Valkey
    valkey_url: str = field(
        default_factory=lambda: os.environ.get(
            "VALKEY_URL", "redis://memoryhub-valkey:6379"
        ),
    )

    # LLM (for agents that need inference)
    llm_endpoint: str = field(
        default_factory=lambda: os.environ.get("LLM_ENDPOINT", ""),
    )
    llm_model: str = field(
        default_factory=lambda: os.environ.get("LLM_MODEL", ""),
    )

    # Budget
    daily_token_budget: int = field(
        default_factory=lambda: int(os.environ.get("DAILY_TOKEN_BUDGET", "100000")),
    )

    # Timing
    poll_interval_seconds: float = field(
        default_factory=lambda: float(os.environ.get("POLL_INTERVAL_SECONDS", "5.0")),
    )
    max_retries: int = field(
        default_factory=lambda: int(os.environ.get("MAX_RETRIES", "5")),
    )

    # Tenant
    tenant_id: str = field(
        default_factory=lambda: os.environ.get("TENANT_ID", "default"),
    )

    @property
    def queue_key(self) -> str:
        """Queue key for this agent's work items."""
        return f"{self.agent_type}_queue:{self.tenant_id}"

    @property
    def lock_key(self) -> str:
        """Leader election lock key."""
        return f"agent_lock:{self.agent_type}:{self.tenant_id}"
