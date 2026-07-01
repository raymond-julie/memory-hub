"""Main agent lifecycle loop: authenticate, dequeue, process, report.

Each curation agent implements ``AgentPlugin`` with its domain-specific
logic. ``AgentRunner`` handles the common lifecycle: connect to Valkey
and MCP, enter the dequeue-process loop, and shut down gracefully on
SIGTERM/SIGINT.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from abc import ABC, abstractmethod

from memoryhub_agents.config import AgentConfig
from memoryhub_agents.mcp_client import MCPSession
from memoryhub_agents.queue import AgentQueue

logger = logging.getLogger(__name__)


class AgentPlugin(ABC):
    """Base class for agent-specific processing logic.

    Subclass this and implement ``process()`` to create a curation agent.
    ``on_start()`` and ``on_stop()`` are optional lifecycle hooks.

    Example::

        class TracerPlugin(AgentPlugin):
            async def process(self, item, mcp):
                thread_id = item["thread_id"]
                # ... extract memories from conversation thread ...
                return {"status": "ok", "extracted": 3}
    """

    @abstractmethod
    async def process(self, item: dict, mcp: MCPSession) -> dict:
        """Process a single work item. Returns a result dict."""

    async def on_start(self, config: AgentConfig, mcp: MCPSession) -> None:
        """Called once after authentication succeeds."""

    async def on_stop(self) -> None:
        """Called during graceful shutdown."""


class AgentRunner:
    """Orchestrates the agent lifecycle.

    Usage::

        config = AgentConfig()
        plugin = MyAgentPlugin()
        runner = AgentRunner(config, plugin)
        await runner.run()
    """

    def __init__(self, config: AgentConfig, plugin: AgentPlugin) -> None:
        self.config = config
        self.plugin = plugin
        self._running = False
        self._queue: AgentQueue | None = None
        self._mcp: MCPSession | None = None

    async def run(self) -> None:
        """Main entry point. Runs until SIGTERM/SIGINT."""
        self._running = True
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)

        try:
            await self._setup()
            await self._loop()
        finally:
            await self._teardown()

    async def _setup(self) -> None:
        """Connect to Valkey and MCP, call plugin.on_start."""
        logger.info("starting %s agent", self.config.agent_type)

        self._queue = AgentQueue(self.config.valkey_url)
        await self._queue.connect()

        self._mcp = MCPSession(
            self.config.mcp_url,
            self.config.api_key,
            self.config.agent_id,
            max_retries=self.config.max_retries,
        )
        await self._mcp.connect()

        await self.plugin.on_start(self.config, self._mcp)
        logger.info("%s agent ready", self.config.agent_type)

    async def _loop(self) -> None:
        """Dequeue and process work items until stopped."""
        while self._running:
            item = await self._queue.dequeue(
                self.config.queue_key,
                timeout=self.config.poll_interval_seconds,
            )
            if item is None:
                continue

            try:
                result = await self.plugin.process(item.payload, self._mcp)
                logger.info(
                    "processed %s: %s", item.id, result.get("status", "ok")
                )
            except Exception:
                logger.exception("failed to process %s, requeuing", item.id)
                await self._queue.requeue(item)

    async def _teardown(self) -> None:
        """Clean shutdown."""
        logger.info("shutting down %s agent", self.config.agent_type)
        await self.plugin.on_stop()
        if self._mcp:
            await self._mcp.close()
        if self._queue:
            await self._queue.close()

    def _handle_signal(self) -> None:
        logger.info("received shutdown signal")
        self._running = False
