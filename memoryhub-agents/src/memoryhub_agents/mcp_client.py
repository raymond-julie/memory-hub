"""MCP session management with exponential backoff retry.

Wraps the MemoryHub SDK client to provide the lifecycle curation agents
need: connect with API key auth, call tools with automatic retry on
transient failures, and clean shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)


class MCPSession:
    """Managed wrapper around ``MemoryHubClient`` with retry logic.

    Handles connection, API-key authentication, tool calls with
    exponential backoff, and clean shutdown. Import of the SDK is
    deferred to ``connect()`` so that unit tests can mock the client
    without installing the full SDK.

    Usage::

        session = MCPSession(
            mcp_url="https://mcp.example.com/mcp/",
            api_key="mh-dev-abc123",
            agent_id="curator-pod-xyz",
        )
        await session.connect()
        results = await session.call_tool("search", query="deployment")
        await session.close()
    """

    def __init__(
        self,
        mcp_url: str,
        api_key: str,
        agent_id: str,
        max_retries: int = 5,
    ) -> None:
        self._url = mcp_url
        self._api_key = api_key
        self._agent_id = agent_id
        self._max_retries = max_retries
        self._client: Any = None  # MemoryHubClient, typed as Any to defer import

    async def connect(self) -> None:
        """Connect to MCP server and authenticate via API key."""
        from memoryhub import MemoryHubClient

        self._client = MemoryHubClient(url=self._url, api_key=self._api_key)
        await self._client.__aenter__()
        logger.info("registered MCP session for %s", self._agent_id)

    async def call_tool(self, action: str, **kwargs: Any) -> Any:
        """Call a MemoryHub SDK method with exponential backoff retry.

        The ``action`` maps to a method name on ``MemoryHubClient``
        (e.g., ``"search"``, ``"write"``, ``"read"``).

        Raises ``RuntimeError`` if all retries are exhausted.
        """
        if not self._client:
            raise RuntimeError("not connected -- call connect() first")

        method = getattr(self._client, action, None)
        if method is None:
            raise ValueError(f"unknown action: {action!r}")

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                return await method(**kwargs)
            except Exception as exc:
                last_error = exc
                if attempt < self._max_retries - 1:
                    delay = min(2**attempt + random.uniform(0, 1), 30)
                    logger.warning(
                        "tool call %s failed (attempt %d/%d), retrying in %.1fs: %s",
                        action,
                        attempt + 1,
                        self._max_retries,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"tool call {action} failed after {self._max_retries} retries"
        ) from last_error

    async def close(self) -> None:
        """Disconnect from the MCP server."""
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None
