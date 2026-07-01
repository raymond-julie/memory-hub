"""Leader election for singleton agents using Valkey distributed locks.

Some agents (Curator, Statistician) should run as singletons per tenant
to avoid duplicate work. This module implements leader election using
Redis SET NX with TTL. The leader must call ``heartbeat()`` periodically
to maintain leadership; if it fails to do so, the lock expires and
another instance can take over.
"""

from __future__ import annotations

import logging

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class LeaderElection:
    """Distributed lock for singleton agents.

    Usage::

        election = LeaderElection(client, "agent_lock:curator:default")
        if await election.try_acquire("pod-abc123"):
            # We are the leader -- do work
            await election.heartbeat()
        else:
            # Another instance is leader -- wait or exit
            pass
    """

    def __init__(
        self, client: redis.Redis, lock_key: str, ttl_seconds: int = 30
    ) -> None:
        self._client = client
        self._lock_key = lock_key
        self._ttl = ttl_seconds
        self._holder_id: str | None = None

    async def try_acquire(self, holder_id: str) -> bool:
        """Attempt to acquire leadership.

        Returns True if this instance is now leader. Re-entrant: if we
        already hold the lock, the TTL is refreshed.
        """
        acquired = await self._client.set(
            self._lock_key, holder_id, nx=True, ex=self._ttl
        )
        if acquired:
            self._holder_id = holder_id
            logger.info("acquired leadership: %s", self._lock_key)
            return True
        # Check if we already hold it (re-entrant call)
        current = await self._client.get(self._lock_key)
        if current == holder_id:
            await self._client.expire(self._lock_key, self._ttl)
            self._holder_id = holder_id
            return True
        return False

    async def heartbeat(self) -> bool:
        """Refresh the lock TTL.

        Returns False if we lost leadership (another instance took over
        or the lock expired between heartbeats).
        """
        if not self._holder_id:
            return False
        current = await self._client.get(self._lock_key)
        if current != self._holder_id:
            self._holder_id = None
            logger.warning("lost leadership: %s", self._lock_key)
            return False
        await self._client.expire(self._lock_key, self._ttl)
        return True

    async def release(self) -> None:
        """Release leadership if we hold it."""
        if self._holder_id:
            current = await self._client.get(self._lock_key)
            if current == self._holder_id:
                await self._client.delete(self._lock_key)
                logger.info("released leadership: %s", self._lock_key)
            self._holder_id = None

    @property
    def is_leader(self) -> bool:
        """Whether this instance currently believes it holds the lock."""
        return self._holder_id is not None
