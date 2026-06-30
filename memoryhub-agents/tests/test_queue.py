"""Tests for memoryhub_agents.queue."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from memoryhub_agents.queue import AgentQueue, WorkItem


class TestWorkItem:
    """WorkItem is a simple data holder."""

    def test_construction(self):
        item = WorkItem(
            id="abc-123",
            payload={"id": "abc-123", "thread_id": "t1"},
            queue_key="tracer_queue:default",
            raw='{"id": "abc-123", "thread_id": "t1"}',
        )
        assert item.id == "abc-123"
        assert item.payload["thread_id"] == "t1"
        assert item.queue_key == "tracer_queue:default"


class TestAgentQueueNotConnected:
    """Operations on a disconnected queue raise RuntimeError."""

    @pytest.fixture
    def queue(self):
        return AgentQueue("redis://localhost:6379")

    @pytest.mark.asyncio
    async def test_dequeue_not_connected(self, queue):
        with pytest.raises(RuntimeError, match="not connected"):
            await queue.dequeue("test_queue")

    @pytest.mark.asyncio
    async def test_enqueue_not_connected(self, queue):
        with pytest.raises(RuntimeError, match="not connected"):
            await queue.enqueue("test_queue", {"id": "1"})

    @pytest.mark.asyncio
    async def test_requeue_not_connected(self, queue):
        item = WorkItem(id="1", payload={}, queue_key="q", raw="{}")
        with pytest.raises(RuntimeError, match="not connected"):
            await queue.requeue(item)

    @pytest.mark.asyncio
    async def test_queue_length_not_connected(self, queue):
        with pytest.raises(RuntimeError, match="not connected"):
            await queue.queue_length("test_queue")


class TestAgentQueueConnected:
    """Queue operations with a mocked Redis client."""

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        client.ping = AsyncMock()
        client.brpop = AsyncMock(return_value=None)
        client.lpush = AsyncMock()
        client.llen = AsyncMock(return_value=0)
        client.aclose = AsyncMock()
        return client

    @pytest.fixture
    async def queue(self, mock_client):
        q = AgentQueue("redis://localhost:6379")
        q._client = mock_client
        return q

    @pytest.mark.asyncio
    async def test_dequeue_timeout(self, queue, mock_client):
        mock_client.brpop.return_value = None
        result = await queue.dequeue("test_queue", timeout=1.0)
        assert result is None
        mock_client.brpop.assert_called_once_with("test_queue", timeout=1)

    @pytest.mark.asyncio
    async def test_dequeue_item(self, queue, mock_client):
        payload = {"id": "work-1", "data": "test"}
        mock_client.brpop.return_value = ("test_queue", json.dumps(payload))
        result = await queue.dequeue("test_queue")
        assert result is not None
        assert result.id == "work-1"
        assert result.payload == payload

    @pytest.mark.asyncio
    async def test_enqueue(self, queue, mock_client):
        item = {"id": "work-2", "data": "test"}
        await queue.enqueue("test_queue", item)
        mock_client.lpush.assert_called_once_with(
            "test_queue", json.dumps(item)
        )

    @pytest.mark.asyncio
    async def test_requeue(self, queue, mock_client):
        raw = '{"id": "work-3"}'
        item = WorkItem(id="work-3", payload={}, queue_key="q", raw=raw)
        await queue.requeue(item)
        mock_client.lpush.assert_called_once_with("q", raw)

    @pytest.mark.asyncio
    async def test_queue_length(self, queue, mock_client):
        mock_client.llen.return_value = 5
        length = await queue.queue_length("test_queue")
        assert length == 5

    @pytest.mark.asyncio
    async def test_close(self, queue, mock_client):
        await queue.close()
        mock_client.aclose.assert_called_once()
        assert queue._client is None
