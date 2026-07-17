"""Tests for extraction run rollback, dry-run, and circuit breaker (#348).

Exit predicates:
- Rollback restores exact prior state
- Dry-run produces full decision set with zero writes
- Circuit breaker halts a synthetic anomalous run
"""

from unittest.mock import AsyncMock

import pytest

from memoryhub_core.models.memory import MemoryNode
from memoryhub_core.models.reconciliation import ReconciliationDecision as DecisionRow
from memoryhub_core.models.schemas import MemoryNodeCreate
from memoryhub_core.services.curation.similarity import SimilarityResult
from memoryhub_core.services.dreaming import _check_circuit_breaker
from memoryhub_core.services.memory import create_memory, update_memory
from memoryhub_core.services.reconciliation import (
    ExtractionCandidate,
    ReconciliationResult,
    reconcile_candidate,
    rollback_extraction_run,
)

TENANT = "test-tenant"
OWNER = "test-user"
RUN_ID = "test-rollback-run-001"


def _candidate(content: str, **kwargs) -> ExtractionCandidate:
    return ExtractionCandidate(content=content, **kwargs)


async def _tiebreaker_same(_cand: str, _exist: str) -> str:
    return "same"


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_restores_prior_state(
    async_session, embedding_service, monkeypatch,
):
    """Exit predicate: rollback a synthetic run and restore exact prior state.

    Run produces 3 decisions:
      1. Create (no match) -> new memory A
      2. Update (matches "mozzarella") -> cheese becomes parmesan (v2)
      3. Skip (exact dup of "blue cheese")
    Rollback should:
      - Soft-delete memory A
      - Revert cheese to v1 (mozzarella, is_current=True, expires_at=None)
      - Leave blue cheese untouched
    """
    # Pre-existing memories
    mozzarella_data = MemoryNodeCreate(
        content="User's favorite cheese is mozzarella",
        scope="user", owner_id=OWNER, content_type="experiential",
    )
    mozzarella, _ = await create_memory(
        mozzarella_data, async_session, embedding_service,
        tenant_id=TENANT, force=True,
    )

    blue_data = MemoryNodeCreate(
        content="User enjoys blue cheese on salads",
        scope="user", owner_id=OWNER, content_type="experiential",
    )
    blue, _ = await create_memory(
        blue_data, async_session, embedding_service,
        tenant_id=TENANT, force=True,
    )
    await async_session.commit()

    mozzarella_id = mozzarella.id
    blue_id = blue.id

    # Decision 1: create (no similar memory)
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(0, None, None)),
    )
    result_a = await reconcile_candidate(
        _candidate("User prefers dark mode for all IDEs"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
    )
    assert result_a.action == "create"
    created_id = result_a.memory_id
    assert created_id is not None

    # Decision 2: update (matches mozzarella, tiebreaker=same)
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, mozzarella_id, 0.92)),
    )
    result_b = await reconcile_candidate(
        _candidate("User's favorite cheese is now parmesan", content_type="experiential"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
        tiebreaker_fn=_tiebreaker_same,
    )
    assert result_b.action == "update"
    parmesan_id = result_b.memory_id
    assert parmesan_id is not None

    # Decision 3: skip (exact dup)
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, blue_id, 0.99)),
    )
    result_c = await reconcile_candidate(
        _candidate("User enjoys blue cheese on salads"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
    )
    assert result_c.action == "skip"
    await async_session.commit()

    # Verify pre-rollback state
    created_mem = await async_session.get(MemoryNode, created_id)
    assert created_mem is not None
    assert created_mem.is_current is True

    old_mozz = await async_session.get(MemoryNode, mozzarella_id)
    assert old_mozz.is_current is False  # retired by update

    new_parm = await async_session.get(MemoryNode, parmesan_id)
    assert new_parm.is_current is True
    assert "parmesan" in new_parm.content

    # ROLLBACK
    summary = await rollback_extraction_run(RUN_ID, async_session)

    assert summary["total_decisions"] == 3
    assert summary["rolled_back"]["creates"] == 1
    assert summary["rolled_back"]["updates"] == 1
    assert summary["rolled_back"]["skips"] == 1

    # Verify post-rollback state
    # Created memory should be soft-deleted
    await async_session.refresh(created_mem)
    assert created_mem.deleted_at is not None
    assert created_mem.is_current is False

    # Mozzarella v1 should be restored
    await async_session.refresh(old_mozz)
    assert old_mozz.is_current is True
    assert old_mozz.expires_at is None
    assert "mozzarella" in old_mozz.content

    # Parmesan v2 should be soft-deleted
    await async_session.refresh(new_parm)
    assert new_parm.deleted_at is not None
    assert new_parm.is_current is False

    # Blue cheese should be completely untouched
    blue_mem = await async_session.get(MemoryNode, blue_id)
    assert blue_mem.is_current is True
    assert blue_mem.deleted_at is None


@pytest.mark.asyncio
async def test_rollback_skips_post_run_modifications(
    async_session, embedding_service, monkeypatch,
):
    """Rollback skips memories that were modified after the run."""
    # Create a memory via a synthetic run
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(0, None, None)),
    )
    result = await reconcile_candidate(
        _candidate("User likes Python"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id=RUN_ID,
    )
    assert result.action == "create"
    original_id = result.memory_id
    await async_session.commit()

    # Post-run modification: update the memory independently
    from memoryhub_core.models.schemas import MemoryNodeUpdate
    updated = await update_memory(
        original_id,
        MemoryNodeUpdate(content="User loves Python and Rust"),
        async_session,
        embedding_service,
    )
    post_run_id = updated.id

    # Rollback should skip this decision
    summary = await rollback_extraction_run(RUN_ID, async_session)

    assert summary["skipped"]["post_run_modifications"] == 1
    assert summary["rolled_back"]["creates"] == 0

    # The post-run version should still be current
    post_run_mem = await async_session.get(MemoryNode, post_run_id)
    assert post_run_mem.is_current is True
    assert post_run_mem.deleted_at is None


@pytest.mark.asyncio
async def test_rollback_nonexistent_run(async_session):
    """Rollback of a run with no decisions returns empty summary."""
    summary = await rollback_extraction_run("nonexistent-run-id", async_session)
    assert summary["total_decisions"] == 0
    assert summary["rolled_back"]["creates"] == 0


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_produces_decisions_no_writes(
    async_session, embedding_service, monkeypatch,
):
    """Exit predicate: dry-run produces full decision set with zero writes."""
    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(0, None, None)),
    )

    result = await reconcile_candidate(
        _candidate("User prefers vim keybindings"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id="dry-run-001",
        dry_run=True,
    )

    # Decision is fully populated
    assert result.action == "create"
    assert result.reason == "no_similar_memory"

    # But no memory was created
    assert result.memory_id is None

    # No decision log row written
    from sqlalchemy import func, select
    decision_count = await async_session.scalar(
        select(func.count()).select_from(DecisionRow)
    )
    assert decision_count == 0

    # No memory node created
    memory_count = await async_session.scalar(
        select(func.count()).select_from(MemoryNode)
    )
    assert memory_count == 0


@pytest.mark.asyncio
async def test_dry_run_with_update_decision(
    async_session, embedding_service, monkeypatch,
):
    """Dry-run with update decision: decision logged but no version created."""
    existing_data = MemoryNodeCreate(
        content="User drinks coffee",
        scope="user", owner_id=OWNER, content_type="experiential",
    )
    existing, _ = await create_memory(
        existing_data, async_session, embedding_service,
        tenant_id=TENANT, force=True,
    )
    await async_session.commit()

    monkeypatch.setattr(
        "memoryhub_core.services.reconciliation.check_similarity",
        AsyncMock(return_value=SimilarityResult(1, existing.id, 0.92)),
    )

    result = await reconcile_candidate(
        _candidate("User drinks tea now", content_type="experiential"),
        OWNER, "user", None, async_session, embedding_service,
        tenant_id=TENANT, extraction_run_id="dry-run-002",
        dry_run=True,
        tiebreaker_fn=_tiebreaker_same,
    )

    assert result.action == "update"
    assert result.memory_id is None  # no actual update performed

    # Original memory untouched
    refreshed = await async_session.get(MemoryNode, existing.id)
    assert refreshed.is_current is True
    assert "coffee" in refreshed.content


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

def test_circuit_breaker_halts_anomalous_run():
    """Exit predicate: circuit breaker trips on all-creates anomaly."""
    all_creates = [
        ReconciliationResult(candidate_stub=f"fact {i}", action="create")
        for i in range(20)
    ]
    reason = _check_circuit_breaker(all_creates, min_decisions=5)
    assert reason is not None
    assert "creates" in reason.lower() or "create" in reason.lower()


def test_circuit_breaker_does_not_trip_on_balanced_mix():
    """Balanced create:update ratio should not trip the breaker."""
    balanced = [
        ReconciliationResult(candidate_stub=f"c{i}", action="create")
        for i in range(5)
    ] + [
        ReconciliationResult(candidate_stub=f"u{i}", action="update")
        for i in range(5)
    ] + [
        ReconciliationResult(candidate_stub=f"s{i}", action="skip")
        for i in range(5)
    ]
    reason = _check_circuit_breaker(balanced, min_decisions=5)
    assert reason is None


def test_circuit_breaker_does_not_arm_below_min():
    """Below min_decisions, the breaker should not trip."""
    few_creates = [
        ReconciliationResult(candidate_stub=f"fact {i}", action="create")
        for i in range(3)
    ]
    reason = _check_circuit_breaker(few_creates, min_decisions=5)
    assert reason is None


def test_circuit_breaker_custom_ratio():
    """Custom ratio threshold is respected."""
    decisions = [
        ReconciliationResult(candidate_stub=f"c{i}", action="create")
        for i in range(10)
    ] + [
        ReconciliationResult(candidate_stub="u0", action="update"),
    ]
    # 10:1 ratio, threshold is 5:1 -> should trip
    reason = _check_circuit_breaker(decisions, max_create_ratio=5.0, min_decisions=5)
    assert reason is not None

    # Same data, threshold is 15:1 -> should not trip
    reason = _check_circuit_breaker(decisions, max_create_ratio=15.0, min_decisions=5)
    assert reason is None
