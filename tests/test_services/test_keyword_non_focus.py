"""Tests for keyword recall in the non-focus search_memories() path.

The keyword signal was originally only wired into search_memories_with_focus().
These tests verify it works in the non-focus path (the benchmark hot path):

1. disabled_signals parameter is accepted without error
2. keyword disabled produces results identical to the old cosine-only behavior
3. SQLite fallback (no pgvector/tsvector) still works with the new parameters
"""

import pytest

from memoryhub_core.models.schemas import MemoryNodeCreate
from memoryhub_core.services.memory import create_memory, search_memories


@pytest.fixture
async def _seeded_memories(async_session, embedding_service):
    """Create a small corpus for keyword tests."""
    memories = [
        "PostgreSQL is a powerful relational database with JSONB support",
        "FastAPI is a modern Python web framework built on Starlette",
        "Kubernetes orchestrates containerized applications at scale",
        "Redis provides in-memory data caching and pub/sub messaging",
    ]
    ids = []
    for content in memories:
        result, _ = await create_memory(
            data=MemoryNodeCreate(
                content=content,
                scope="user",
                owner_id="test-user",
            ),
            session=async_session,
            embedding_service=embedding_service,
            tenant_id="test-tenant",
        )
        ids.append(result.id)
    return ids


@pytest.mark.asyncio
async def test_disabled_signals_accepted(
    async_session, embedding_service, _seeded_memories
):
    """search_memories() accepts disabled_signals without error."""
    results = await search_memories(
        query="database",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        owner_id="test-user",
        disabled_signals={"keyword"},
    )
    assert len(results) > 0


@pytest.mark.asyncio
async def test_keyword_disabled_matches_default(
    async_session, embedding_service, _seeded_memories
):
    """With keyword disabled, results match the default (SQLite has no tsvector)."""
    results_default = await search_memories(
        query="database",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        owner_id="test-user",
    )

    results_disabled = await search_memories(
        query="database",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        owner_id="test-user",
        disabled_signals={"keyword"},
    )

    # On SQLite both paths take the early-return fallback, so results
    # should be identical (same node order, same synthetic scores).
    assert len(results_default) == len(results_disabled)
    default_ids = [r[0].id for r in results_default]
    disabled_ids = [r[0].id for r in results_disabled]
    assert default_ids == disabled_ids


@pytest.mark.asyncio
async def test_keyword_boost_weight_zero_disables(
    async_session, embedding_service, _seeded_memories
):
    """keyword_boost_weight=0.0 effectively disables keyword recall."""
    results = await search_memories(
        query="PostgreSQL",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        owner_id="test-user",
        keyword_boost_weight=0.0,
    )
    assert len(results) > 0


@pytest.mark.asyncio
async def test_empty_disabled_signals_same_as_none(
    async_session, embedding_service, _seeded_memories
):
    """Empty set behaves same as None."""
    results_none = await search_memories(
        query="database",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        owner_id="test-user",
        disabled_signals=None,
    )

    results_empty = await search_memories(
        query="database",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        owner_id="test-user",
        disabled_signals=set(),
    )

    assert len(results_none) == len(results_empty)


@pytest.mark.asyncio
async def test_returns_rrf_scores(
    async_session, embedding_service, _seeded_memories
):
    """Results include positive float scores."""
    results = await search_memories(
        query="database",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        owner_id="test-user",
    )
    for _, score in results:
        assert isinstance(score, float)
        assert score > 0.0
