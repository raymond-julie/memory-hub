"""Tests for disabled_signals parameter on search_memories_with_focus.

The disabled_signals parameter allows selectively disabling search signals:
- "reranker": Skip cross-encoder reranking even if reranker is provided
- "focus": Skip focus vector ranking (session_focus_weight effectively 0)
- "keyword": Skip keyword recall even if keyword_boost_weight > 0
- "domain": Skip domain boost even if domains are provided
- "graph": Skip graph traversal even if graph_depth > 0

This file verifies:
1. disabled_signals=None (default) doesn't change behavior
2. Each signal individually disabled produces expected metadata
3. Multiple signals disabled at once work correctly
4. FocusedSearchResult.disabled_signals reflects what was passed in
5. Empty set behaves same as None
"""

import pytest

from memoryhub_core.models.schemas import MemoryNodeCreate
from memoryhub_core.services.memory import (
    create_memory,
    search_memories_with_focus,
)


@pytest.fixture
async def _seeded_memories(async_session, embedding_service):
    """Create a small corpus for disabled_signals tests."""
    memories = [
        "PostgreSQL is a powerful relational database",
        "FastAPI is a modern Python web framework",
        "Kubernetes orchestrates containerized applications",
        "Redis provides in-memory data caching",
    ]
    ids = []
    for content in memories:
        data = MemoryNodeCreate(
            content=content,
            scope="user",
            owner_id="test-user",
        )
        result, _ = await create_memory(
            data=data,
            session=async_session,
            embedding_service=embedding_service,
            tenant_id="test-tenant",
        )
        ids.append(result.id)
    return ids


@pytest.mark.asyncio
async def test_disabled_signals_none_is_default(
    async_session, embedding_service, _seeded_memories
):
    """disabled_signals=None (default) doesn't change behavior."""
    result_default = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        owner_id="test-user",
    )

    result_explicit_none = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        owner_id="test-user",
        disabled_signals=None,
    )

    # Both should produce identical results
    assert len(result_default.results) == len(result_explicit_none.results)
    assert result_default.disabled_signals == set()
    assert result_explicit_none.disabled_signals == set()


@pytest.mark.asyncio
async def test_disabled_signals_empty_set_same_as_none(
    async_session, embedding_service, _seeded_memories
):
    """Empty set behaves same as None."""
    result = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        owner_id="test-user",
        disabled_signals=set(),
    )

    assert result.disabled_signals == set()
    assert len(result.results) > 0


@pytest.mark.asyncio
async def test_disable_reranker_signal(
    async_session, embedding_service, _seeded_memories
):
    """Disabling 'reranker' signal sets used_reranker=False."""
    # Note: In test environment, reranker is typically None, so this
    # primarily tests the flag is passed through correctly.
    result = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        owner_id="test-user",
        disabled_signals={"reranker"},
    )

    assert result.used_reranker is False
    assert "reranker" in result.disabled_signals
    assert len(result.results) > 0


@pytest.mark.asyncio
async def test_disable_focus_signal(
    async_session, embedding_service, _seeded_memories
):
    """Disabling 'focus' signal skips focus vector ranking."""
    # With focus disabled, session_focus_weight is effectively 0
    result = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        session_focus_weight=0.4,  # Provided but should be ignored
        owner_id="test-user",
        disabled_signals={"focus"},
    )

    assert "focus" in result.disabled_signals
    assert len(result.results) > 0
    # Focus was provided but disabled, so results should still exist
    # but focus ranking was not applied


@pytest.mark.asyncio
async def test_disable_keyword_signal(
    async_session, embedding_service, _seeded_memories
):
    """Disabling 'keyword' signal sets keyword_matches=0."""
    result = await search_memories_with_focus(
        query="PostgreSQL",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="databases",
        keyword_boost_weight=0.15,  # Provided but should be ignored
        owner_id="test-user",
        disabled_signals={"keyword"},
    )

    # Even with keyword_boost_weight > 0, keyword recall should be skipped
    assert result.keyword_matches == 0
    assert "keyword" in result.disabled_signals
    assert len(result.results) > 0


@pytest.mark.asyncio
async def test_disable_domain_signal(
    async_session, embedding_service, _seeded_memories
):
    """Disabling 'domain' signal skips domain boost."""
    result = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        domains=["postgresql", "databases"],  # Provided but should be ignored
        domain_boost_weight=0.3,
        owner_id="test-user",
        disabled_signals={"domain"},
    )

    assert "domain" in result.disabled_signals
    assert len(result.results) > 0
    # Domain boost was provided but disabled, ranking should ignore domains


@pytest.mark.asyncio
async def test_disable_graph_signal(
    async_session, embedding_service, _seeded_memories
):
    """Disabling 'graph' signal sets graph_neighbors_added=0."""
    result = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        graph_depth=2,  # Provided but should be ignored
        owner_id="test-user",
        disabled_signals={"graph"},
    )

    # Even with graph_depth > 0, graph traversal should be skipped
    assert result.graph_neighbors_added == 0
    assert "graph" in result.disabled_signals
    assert len(result.results) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disabled_set",
    [
        {"reranker", "focus"},
        {"keyword", "domain"},
        {"reranker", "keyword", "graph"},
        {"focus", "domain", "graph"},
        {"reranker", "focus", "keyword", "domain", "graph"},
    ],
)
async def test_multiple_signals_disabled(
    async_session, embedding_service, _seeded_memories, disabled_set
):
    """Multiple signals can be disabled simultaneously."""
    # Note: graph_depth must be 0 or graph must be disabled because
    # SQLite doesn't support unnest() used in graph traversal
    result = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        session_focus_weight=0.4,
        keyword_boost_weight=0.15,
        domains=["postgresql"],
        domain_boost_weight=0.3,
        graph_depth=2 if "graph" in disabled_set else 0,
        owner_id="test-user",
        disabled_signals=disabled_set,
    )

    # Verify the disabled_signals field reflects what was passed
    assert result.disabled_signals == disabled_set

    # Verify expected metadata based on what was disabled
    if "reranker" in disabled_set:
        assert result.used_reranker is False

    if "keyword" in disabled_set:
        assert result.keyword_matches == 0

    if "graph" in disabled_set:
        assert result.graph_neighbors_added == 0

    # Should still return results even with multiple signals disabled
    assert len(result.results) > 0


@pytest.mark.asyncio
async def test_disable_all_signals(
    async_session, embedding_service, _seeded_memories
):
    """All signals can be disabled at once (pure vector search)."""
    result = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        session_focus_weight=0.4,
        keyword_boost_weight=0.15,
        domains=["postgresql"],
        domain_boost_weight=0.3,
        graph_depth=2,
        owner_id="test-user",
        disabled_signals={"reranker", "focus", "keyword", "domain", "graph"},
    )

    # All signals disabled
    assert result.disabled_signals == {
        "reranker",
        "focus",
        "keyword",
        "domain",
        "graph",
    }
    assert result.used_reranker is False
    assert result.keyword_matches == 0
    assert result.graph_neighbors_added == 0

    # Should still return results via base vector search
    assert len(result.results) > 0


@pytest.mark.asyncio
async def test_disabled_signals_field_always_present(
    async_session, embedding_service, _seeded_memories
):
    """FocusedSearchResult always includes disabled_signals field."""
    result = await search_memories_with_focus(
        query="database systems",
        session=async_session,
        embedding_service=embedding_service,
        tenant_id="test-tenant",
        focus_string="data storage",
        owner_id="test-user",
    )

    assert hasattr(result, "disabled_signals")
    assert isinstance(result.disabled_signals, set)
