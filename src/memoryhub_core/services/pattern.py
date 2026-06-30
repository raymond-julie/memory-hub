"""Within-user pattern detection for search-time signals.

Detects clusters of similar recent memories within a single user's
memory stream. When a search query overlaps with a cluster of recent
memories (3+ with high cosine similarity in a configurable time window),
the search response is annotated with pattern_signals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memoryhub_core.models.memory import MemoryNode

logger = logging.getLogger(__name__)


@dataclass
class PatternSignal:
    """A detected pattern in the user's recent memories."""

    pattern: str  # e.g. "topic_cluster"
    matching_memories: int
    time_window_days: int
    representative_id: str  # UUID of the most recent matching memory
    summary_hint: str  # Human-readable description


async def detect_patterns(
    query_embedding: list[float],
    session: AsyncSession,
    *,
    owner_id: str,
    tenant_id: str,
    similarity_threshold: float = 0.80,
    min_cluster_size: int = 3,
    time_window_days: int = 30,
    max_candidates: int = 50,
) -> list[PatternSignal]:
    """Detect within-user patterns by querying recent memories similar to the search query.

    Runs a single pgvector cosine-distance query against the caller's recent
    memories (last ``time_window_days`` days). If the number of matches above
    ``similarity_threshold`` meets or exceeds ``min_cluster_size``, a
    ``PatternSignal`` is returned with a count, time window, and the most
    recent matching memory's ID.

    This is a best-effort read-path enhancement. If pgvector is unavailable
    or the query fails for any reason, an empty list is returned -- matching
    the existing fallback patterns in search.
    """
    try:
        cutoff = datetime.now(UTC) - timedelta(days=time_window_days)
        distance_threshold = 1.0 - similarity_threshold

        distance_expr = MemoryNode.embedding.cosine_distance(query_embedding)
        stmt = (
            select(
                func.count().label("match_count"),
                func.max(MemoryNode.created_at).label("latest_at"),
            )
            .where(
                MemoryNode.owner_id == owner_id,
                MemoryNode.tenant_id == tenant_id,
                MemoryNode.is_current.is_(True),
                MemoryNode.created_at >= cutoff,
                MemoryNode.deleted_at.is_(None),
                MemoryNode.embedding.isnot(None),
                distance_expr <= distance_threshold,
            )
        )
        result = await session.execute(stmt)
        row = result.one()

        if row.match_count < min_cluster_size:
            return []

        # Fetch the representative memory ID (most recent match).
        rep_stmt = (
            select(MemoryNode.id)
            .where(
                MemoryNode.owner_id == owner_id,
                MemoryNode.tenant_id == tenant_id,
                MemoryNode.is_current.is_(True),
                MemoryNode.created_at >= cutoff,
                MemoryNode.deleted_at.is_(None),
                MemoryNode.embedding.isnot(None),
                distance_expr <= distance_threshold,
            )
            .order_by(MemoryNode.created_at.desc())
            .limit(1)
        )
        rep_result = await session.execute(rep_stmt)
        representative_id = str(rep_result.scalar_one())

        return [
            PatternSignal(
                pattern="topic_cluster",
                matching_memories=row.match_count,
                time_window_days=time_window_days,
                representative_id=representative_id,
                summary_hint=(
                    f"{row.match_count} recent memories (last {time_window_days}d) "
                    f"cluster around this topic"
                ),
            )
        ]

    except Exception:
        logger.debug("pattern detection skipped (pgvector unavailable or query error)", exc_info=True)
        return []
