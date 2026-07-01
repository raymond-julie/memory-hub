"""Tests for memoryhub_agents.leader."""

from unittest.mock import AsyncMock

import pytest

from memoryhub_agents.leader import LeaderElection


@pytest.fixture
def mock_redis():
    client = AsyncMock()
    client.set = AsyncMock(return_value=True)
    client.get = AsyncMock(return_value=None)
    client.expire = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.fixture
def election(mock_redis):
    return LeaderElection(mock_redis, "agent_lock:curator:default", ttl_seconds=30)


class TestLeaderElectionAcquire:
    @pytest.mark.asyncio
    async def test_acquire_success(self, election, mock_redis):
        mock_redis.set.return_value = True
        result = await election.try_acquire("pod-1")
        assert result is True
        assert election.is_leader is True
        mock_redis.set.assert_called_once_with(
            "agent_lock:curator:default", "pod-1", nx=True, ex=30
        )

    @pytest.mark.asyncio
    async def test_acquire_fails_when_held(self, election, mock_redis):
        mock_redis.set.return_value = False
        mock_redis.get.return_value = "pod-other"
        result = await election.try_acquire("pod-1")
        assert result is False
        assert election.is_leader is False

    @pytest.mark.asyncio
    async def test_acquire_reentrant(self, election, mock_redis):
        """If we already hold the lock, re-acquire refreshes TTL."""
        mock_redis.set.return_value = False
        mock_redis.get.return_value = "pod-1"
        result = await election.try_acquire("pod-1")
        assert result is True
        mock_redis.expire.assert_called_once_with(
            "agent_lock:curator:default", 30
        )


class TestLeaderElectionHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_success(self, election, mock_redis):
        # First acquire
        mock_redis.set.return_value = True
        await election.try_acquire("pod-1")
        # Then heartbeat
        mock_redis.get.return_value = "pod-1"
        result = await election.heartbeat()
        assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat_lost_leadership(self, election, mock_redis):
        mock_redis.set.return_value = True
        await election.try_acquire("pod-1")
        # Someone else took over
        mock_redis.get.return_value = "pod-other"
        result = await election.heartbeat()
        assert result is False
        assert election.is_leader is False

    @pytest.mark.asyncio
    async def test_heartbeat_without_acquiring(self, election):
        result = await election.heartbeat()
        assert result is False


class TestLeaderElectionRelease:
    @pytest.mark.asyncio
    async def test_release(self, election, mock_redis):
        mock_redis.set.return_value = True
        await election.try_acquire("pod-1")
        mock_redis.get.return_value = "pod-1"
        await election.release()
        mock_redis.delete.assert_called_once_with("agent_lock:curator:default")
        assert election.is_leader is False

    @pytest.mark.asyncio
    async def test_release_when_not_leader(self, election, mock_redis):
        await election.release()
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_when_someone_else_holds(self, election, mock_redis):
        """Release is a no-op if another instance took over."""
        mock_redis.set.return_value = True
        await election.try_acquire("pod-1")
        mock_redis.get.return_value = "pod-other"
        await election.release()
        mock_redis.delete.assert_not_called()
        assert election.is_leader is False
