"""Valkey-backed job queue for agent work items.

Uses Redis LIST operations (BRPOP/LPUSH) for reliable FIFO queueing.
Valkey is Redis-protocol-compatible, so the ``redis`` Python package
works unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import redis.asyncio as redis

logger = logging.getLogger(__name__)


@dataclass
class WorkItem:
    """A unit of work dequeued from the agent queue."""

    id: str
    payload: dict
    queue_key: str
    raw: str  # original JSON for re-enqueue on failure


class AgentQueue:
    """Async Valkey queue client for agent work items.

    Items are enqueued with LPUSH and dequeued with BRPOP (blocking right
    pop), giving FIFO ordering. Failed items can be requeued to the head
    of the queue for retry.
    """

    def __init__(self, valkey_url: str) -> None:
        self._url = valkey_url
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        """Establish connection to Valkey and verify with PING."""
        self._client = redis.from_url(self._url, decode_responses=True)
        await self._client.ping()
        logger.info("connected to Valkey at %s", self._url)

    async def close(self) -> None:
        """Close the Valkey connection."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("not connected -- call connect() first")
        return self._client

    async def dequeue(
        self, queue_key: str, timeout: float = 5.0
    ) -> WorkItem | None:
        """Block-pop the next work item. Returns None on timeout."""
        client = self._require_client()
        result = await client.brpop(queue_key, timeout=int(timeout))
        if result is None:
            return None
        _, raw = result
        data = json.loads(raw)
        return WorkItem(
            id=data.get("id", ""),
            payload=data,
            queue_key=queue_key,
            raw=raw,
        )

    async def enqueue(self, queue_key: str, item: dict) -> None:
        """Push a work item to the tail of the queue."""
        client = self._require_client()
        await client.lpush(queue_key, json.dumps(item))

    async def requeue(self, item: WorkItem) -> None:
        """Re-enqueue a failed work item (preserves original payload)."""
        client = self._require_client()
        await client.lpush(item.queue_key, item.raw)

    async def queue_length(self, queue_key: str) -> int:
        """Get the current queue depth."""
        client = self._require_client()
        return await client.llen(queue_key)
