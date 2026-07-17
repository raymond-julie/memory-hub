"""Tests for the reconciliation service (dreaming pipeline Layer 2 Step 2).

Covers all threshold bands, the cheese test, and decision log completeness.
Uses monkeypatched check_similarity to control scores without PostgreSQL.

Part of #347.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from memoryhub_core.models.reconciliation import ReconciliationDecision as DecisionRow
from memoryhub_core.models.schemas import MemoryNodeCreate
from memoryhub_core.services.curation.similarity import SimilarityResult
from memoryhub_core.services.memory import create_memory
from memoryhub_core.services.reconciliation import (
    ExtractionCandidate,
    ReconciliationResult,
    reconcile_candidate,
)


TENANT = "test-tenant"
OWNER = "test-user"
RUN_ID = "test-run-001"


def _candidate(content: str, **kwargs) -> ExtractionCandidate:
    return ExtractionCandidate(content=content, **kwargs)


async def _tiebreaker_same(_cand: str, _exist: str) -> str:
    return "same"


async def _tiebreaker_different(_cand: str, _exist: str) -> str:
    return "different"


# ---------------------------------------------------------------------------
# Threshold band tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_when_no_similar(async_session, embedding_service, monkeypatch):
    """Below-threshold: no similar memory -> create."""
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(0, None, None)),
    )

    result = await reconcile_candidate(
        _candidate("The user prefers dark mode"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
    )

    assert result.action == "create"
    assert result.reason == "no_similar_memory"
    assert result.memory_id is not None


@pytest.mark.asyncio
async def test_skip_exact_duplicate(async_session, embedding_service, monkeypatch):
    """>=0.98: exact duplicate -> skip."""
    existing_id = uuid.uuid4()
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, existing_id, 0.99)),
    )

    result = await reconcile_candidate(
        _candidate("The user prefers dark mode"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
    )

    assert result.action == "skip"
    assert result.reason == "exact_duplicate"
    assert result.memory_id is None
    assert result.nearest_match_id == existing_id


@pytest.mark.asyncio
async def test_update_when_tiebreaker_same(async_session, embedding_service, monkeypatch):
    """>=0.80 + tiebreaker=same + content_type match -> update."""
    # First create a memory that the reconciliation will find as "existing"
    data = MemoryNodeCreate(
        content="User's favorite cheese is mozzarella",
        scope="user", owner_id=OWNER, content_type="experiential",
    )
    existing, _ = await create_memory(
        data, async_session, embedding_service,
        tenant_id=TENANT, force=True,
    )
    await async_session.commit()

    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, existing.id, 0.92)),
    )

    result = await reconcile_candidate(
        _candidate("User's favorite cheese is now parmesan", content_type="experiential"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
        tiebreaker_fn=_tiebreaker_same,
    )

    assert result.action == "update"
    assert result.reason == "tiebreaker_same"
    assert result.memory_id is not None
    assert result.memory_id != existing.id  # new version has new ID
    assert result.content_type_match is True


@pytest.mark.asyncio
async def test_create_when_content_type_mismatch(async_session, embedding_service, monkeypatch):
    """>=0.90 + tiebreaker=same but content_type mismatch -> create (negative case)."""
    data = MemoryNodeCreate(
        content="User mentioned mozzarella in a recipe",
        scope="user", owner_id=OWNER, content_type="behavioral",
    )
    existing, _ = await create_memory(
        data, async_session, embedding_service,
        tenant_id=TENANT, force=True,
    )
    await async_session.commit()

    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, existing.id, 0.93)),
    )

    result = await reconcile_candidate(
        _candidate("User's favorite cheese is mozzarella", content_type="experiential"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
        tiebreaker_fn=_tiebreaker_same,
    )

    assert result.action == "create"
    assert result.reason == "content_type_mismatch"
    assert result.content_type_match is False


@pytest.mark.asyncio
async def test_create_when_tiebreaker_different(async_session, embedding_service, monkeypatch):
    """0.80-0.98 + tiebreaker=different -> create."""
    data = MemoryNodeCreate(
        content="User likes pizza with mozzarella topping",
        scope="user", owner_id=OWNER,
    )
    existing, _ = await create_memory(
        data, async_session, embedding_service,
        tenant_id=TENANT, force=True,
    )
    await async_session.commit()

    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, existing.id, 0.85)),
    )

    result = await reconcile_candidate(
        _candidate("User's favorite cheese is mozzarella"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
        tiebreaker_fn=_tiebreaker_different,
    )

    assert result.action == "create"
    assert result.reason == "tiebreaker_different"


@pytest.mark.asyncio
async def test_create_when_no_tiebreaker_fn(async_session, embedding_service, monkeypatch):
    """>=0.80 but no tiebreaker function -> create (safe default)."""
    data = MemoryNodeCreate(
        content="User likes cheese", scope="user", owner_id=OWNER,
    )
    existing, _ = await create_memory(
        data, async_session, embedding_service,
        tenant_id=TENANT, force=True,
    )
    await async_session.commit()

    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, existing.id, 0.91)),
    )

    result = await reconcile_candidate(
        _candidate("User's favorite cheese is parmesan"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
    )

    # Without tiebreaker_fn, verdict is None -> falls through to create
    assert result.action == "create"
    assert result.reason == "tiebreaker_different"


# ---------------------------------------------------------------------------
# Decision log completeness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decision_log_written(async_session, embedding_service, monkeypatch):
    """Every reconciliation call writes exactly one decision log row."""
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(0, None, None)),
    )

    await reconcile_candidate(
        _candidate("Fact one"), OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
    )
    await reconcile_candidate(
        _candidate("Fact two"), OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
    )
    await async_session.commit()

    from sqlalchemy import func, select
    count = await async_session.scalar(
        select(func.count()).select_from(DecisionRow)
    )
    assert count == 2


# ---------------------------------------------------------------------------
# Cheese test (design doc acceptance criteria)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cheese_update_preserves_version_history(
    async_session, embedding_service, monkeypatch,
):
    """The cheese test from planning/memory-extraction-pipeline.md.

    1. Write "favorite cheese is mozzarella"
    2. Reconcile "favorite cheese is now parmesan" -> update
    3. Version history: v1 mozzarella, v2 parmesan
    """
    # Step 1: create the original memory
    data = MemoryNodeCreate(
        content="User's favorite cheese is mozzarella",
        scope="user", owner_id=OWNER, content_type="experiential",
    )
    mozzarella, _ = await create_memory(
        data, async_session, embedding_service,
        tenant_id=TENANT, force=True,
    )
    await async_session.commit()
    assert mozzarella is not None

    # Step 2: reconcile the update
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, mozzarella.id, 0.93)),
    )

    result = await reconcile_candidate(
        _candidate("User's favorite cheese is now parmesan", content_type="experiential"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
        tiebreaker_fn=_tiebreaker_same,
    )
    await async_session.commit()

    assert result.action == "update"
    assert result.memory_id is not None

    # Step 3: verify version history
    from sqlalchemy import select
    from memoryhub_core.models.memory import MemoryNode

    # Old version should be non-current
    old = await async_session.get(MemoryNode, mozzarella.id)
    assert old.is_current is False
    assert "mozzarella" in old.content

    # New version should be current with parmesan
    new = await async_session.get(MemoryNode, result.memory_id)
    assert new.is_current is True
    assert "parmesan" in new.content
    assert new.version == 2
    assert new.previous_version_id == mozzarella.id
