"""Tests for memoryhub_agents.lifecycle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memoryhub_agents.config import AgentConfig
from memoryhub_agents.lifecycle import AgentPlugin, AgentRunner
from memoryhub_agents.mcp_client import MCPSession
from memoryhub_agents.queue import AgentQueue, WorkItem


class TestAgentPluginAbstract:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            AgentPlugin()  # type: ignore[abstract]

    def test_subclass_must_implement_process(self):
        class Incomplete(AgentPlugin):
            pass

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_with_process_works(self):
        class Complete(AgentPlugin):
            async def process(self, item, mcp):
                return {"status": "ok"}

        plugin = Complete()
        assert plugin is not None


class TestAgentRunnerSetupTeardown:
    @pytest.fixture
    def config(self, monkeypatch):
        monkeypatch.setenv("AGENT_TYPE", "test-agent")
        monkeypatch.setenv("AGENT_ID", "test-1")
        monkeypatch.setenv("MH_MCP_URL", "https://mcp.test/mcp/")
        monkeypatch.setenv("MH_API_KEY", "mh-dev-test")
        monkeypatch.setenv("VALKEY_URL", "redis://localhost:6379")
        return AgentConfig()

    @pytest.fixture
    def plugin(self):
        p = MagicMock(spec=AgentPlugin)
        p.process = AsyncMock(return_value={"status": "ok"})
        p.on_start = AsyncMock()
        p.on_stop = AsyncMock()
        return p

    @pytest.mark.asyncio
    async def test_setup_connects_queue_and_mcp(self, config, plugin):
        runner = AgentRunner(config, plugin)

        with (
            patch.object(AgentQueue, "connect", new_callable=AsyncMock) as mock_q,
            patch.object(MCPSession, "connect", new_callable=AsyncMock) as mock_m,
        ):
            await runner._setup()
            mock_q.assert_called_once()
            mock_m.assert_called_once()
            plugin.on_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_closes_connections(self, config, plugin):
        runner = AgentRunner(config, plugin)

        mock_queue = AsyncMock(spec=AgentQueue)
        mock_mcp = AsyncMock(spec=MCPSession)
        runner._queue = mock_queue
        runner._mcp = mock_mcp

        await runner._teardown()
        plugin.on_stop.assert_called_once()
        mock_mcp.close.assert_called_once()
        mock_queue.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_loop_processes_items(self, config, plugin):
        runner = AgentRunner(config, plugin)
        mock_queue = AsyncMock(spec=AgentQueue)
        runner._queue = mock_queue
        runner._mcp = AsyncMock(spec=MCPSession)
        runner._running = True

        # Return one item, then None to allow a stop check, then stop
        call_count = 0

        async def mock_dequeue(key, timeout=5.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return WorkItem(
                    id="w1",
                    payload={"id": "w1", "data": "test"},
                    queue_key=key,
                    raw='{"id": "w1", "data": "test"}',
                )
            runner._running = False
            return None

        mock_queue.dequeue = mock_dequeue
        await runner._loop()
        plugin.process.assert_called_once_with(
            {"id": "w1", "data": "test"}, runner._mcp
        )

    @pytest.mark.asyncio
    async def test_loop_requeues_on_failure(self, config, plugin):
        runner = AgentRunner(config, plugin)
        mock_queue = AsyncMock(spec=AgentQueue)
        runner._queue = mock_queue
        runner._mcp = AsyncMock(spec=MCPSession)
        runner._running = True

        plugin.process.side_effect = ValueError("boom")
        call_count = 0

        async def mock_dequeue(key, timeout=5.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return WorkItem(
                    id="w1",
                    payload={"id": "w1"},
                    queue_key=key,
                    raw='{"id": "w1"}',
                )
            runner._running = False
            return None

        mock_queue.dequeue = mock_dequeue
        await runner._loop()
        mock_queue.requeue.assert_called_once()

    def test_handle_signal(self, config, plugin):
        runner = AgentRunner(config, plugin)
        runner._running = True
        runner._handle_signal()
        assert runner._running is False
