"""Service layer for conversation thread CRUD operations."""

import asyncio
import io
import logging
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from memoryhub_core.config import AppSettings
from memoryhub_core.models.conversation import (
    ConversationExtraction,
    ConversationMessage,
    ConversationThread,
    PurgeLog,
)
from memoryhub_core.models.schemas import (
    ConversationMessageCreate,
    ConversationMessageRead,
    ConversationThreadCreate,
    ConversationThreadRead,
)
from memoryhub_core.services.exceptions import ThreadNotActiveError, ThreadNotFoundError
from memoryhub_core.storage.s3 import S3StorageAdapter

logger = logging.getLogger(__name__)


async def _store_message_s3(
    s3_adapter: S3StorageAdapter,
    tenant_id: str,
    thread_id: uuid.UUID,
    sequence_number: int,
    content: str,
) -> str:
    """Store message content in S3 and return the content_ref key.

    Uses the same async wrapper pattern as the S3StorageAdapter's put_content,
    but with a different key format for conversation threads.

    Key format: threads/{tenant_id}/{thread_id}/{sequence_number}
    """
    await s3_adapter.ensure_bucket()
    key = f"threads/{tenant_id}/{thread_id}/{sequence_number}"
    data = content.encode("utf-8")
    stream = io.BytesIO(data)
    # Access the private members directly to avoid rewriting the key format
    await asyncio.to_thread(
        partial(
            s3_adapter._client.put_object,
            s3_adapter._bucket,
            key,
            stream,
            length=len(data),
            content_type="text/plain; charset=utf-8",
        )
    )
    return key


async def create_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    data: ConversationThreadCreate,
    owner_id: str,
    actor_id: str | None = None,
    driver_id: str | None = None,
) -> ConversationThreadRead:
    """Create a new conversation thread.

    Args:
        session: Database session
        tenant_id: Tenant identifier
        data: Thread creation data
        owner_id: User who owns the thread
        actor_id: Actor agent identifier (optional)
        driver_id: Driver agent identifier (optional)

    Returns:
        Created thread read schema
    """
    # Build participant list: ensure owner_id is included
    participant_ids = list(data.participant_ids) if data.participant_ids else []
    if owner_id not in participant_ids:
        participant_ids.append(owner_id)

    # Resolve retention policy based on scope
    # TODO: Replace with actual policy resolution once retention policies are implemented
    if data.scope == "user":
        retention_policy = {
            "ttl_days": 90,
            "cascade_to_memories": "delete",
            "min_retention_days": 30,
            "inherited_from": "system:default",
        }
    else:  # project scope
        retention_policy = {
            "ttl_days": 365,
            "cascade_to_memories": "delete",
            "min_retention_days": 30,
            "inherited_from": "system:default",
        }

    # Compute expiration timestamp
    ttl_days = retention_policy["ttl_days"]
    created_at = datetime.now(UTC)
    expires_at = created_at + timedelta(days=ttl_days)

    # Create the thread
    # Explicit None -> omit for JSON columns to avoid JSON null vs SQL NULL
    thread_kwargs: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "scope": data.scope,
        "owner_id": owner_id,
        "actor_id": actor_id,
        "driver_id": driver_id,
        "participant_ids": participant_ids,
        "title": data.title,
        "a2a_context_id": data.a2a_context_id,
        "retention_policy": retention_policy,
        "created_at": created_at,
        "expires_at": expires_at,
        "status": "active",
    }
    if data.participant_access is not None:
        thread_kwargs["participant_access"] = data.participant_access
    if data.metadata is not None:
        thread_kwargs["metadata_"] = data.metadata
    thread = ConversationThread(**thread_kwargs)

    session.add(thread)
    await session.commit()
    await session.refresh(thread)

    return ConversationThreadRead.model_validate(thread)


async def get_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
    include_messages: bool = True,
    limit: int = 50,
    before_sequence: int | None = None,
    s3_adapter: S3StorageAdapter | None = None,
    caller_id: str | None = None,
) -> dict | None:
    """Retrieve a conversation thread with optional message history.

    Args:
        session: Database session
        tenant_id: Tenant identifier
        thread_id: Thread UUID
        include_messages: Whether to include message history
        limit: Maximum number of messages to return
        before_sequence: Return messages before this sequence number
        s3_adapter: S3 adapter for fetching S3-stored message content

    Returns:
        Dict with thread and optional messages, or None if not found
    """
    # Query thread
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
        ConversationThread.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()

    if thread is None:
        return None

    # Build result with thread
    response = {"thread": ConversationThreadRead.model_validate(thread)}

    # Include messages if requested
    if include_messages:
        # Build message query
        msg_stmt = select(ConversationMessage).where(ConversationMessage.thread_id == thread_id)

        # Filter redacted messages for non-owner callers (in SQL for correct pagination)
        is_owner = caller_id is not None and caller_id == thread.owner_id
        if caller_id is not None and not is_owner:
            msg_stmt = msg_stmt.where(ConversationMessage.handoff_redacted.is_(False))

        if before_sequence is not None:
            msg_stmt = msg_stmt.where(ConversationMessage.sequence_number < before_sequence)
        msg_stmt = msg_stmt.order_by(ConversationMessage.sequence_number.asc())
        msg_stmt = msg_stmt.limit(limit + 1)  # Fetch one extra to detect has_more

        msg_result = await session.execute(msg_stmt)
        messages = list(msg_result.scalars().all())

        # Check if there are more messages
        has_more = len(messages) > limit
        if has_more:
            messages.pop()  # Remove the extra row

        # Fetch S3 content if needed
        message_reads = []
        for msg in messages:
            if msg.storage_type == "s3" and s3_adapter is not None and msg.content_ref:
                msg.content = await s3_adapter.get_content(msg.content_ref)
            message_reads.append(ConversationMessageRead.model_validate(msg))

        response["messages"] = message_reads
        response["has_more"] = has_more

    return response


async def list_threads(
    session: AsyncSession,
    *,
    tenant_id: str,
    owner_id: str | None = None,
    scope: str | None = None,
    scope_id: str | None = None,
    status: str = "active",
    participant_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ConversationThreadRead], int]:
    """List conversation threads matching filters.

    Args:
        session: Database session
        tenant_id: Tenant identifier
        owner_id: Filter by owner
        scope: Filter by scope (user/project)
        scope_id: Filter by scope_id
        status: Filter by status (default: active)
        participant_id: Filter by participant
        limit: Maximum results
        offset: Result offset for pagination

    Returns:
        Tuple of (thread list, total count)
    """
    # Build base filters
    filters = [ConversationThread.tenant_id == tenant_id]

    if owner_id is not None:
        filters.append(ConversationThread.owner_id == owner_id)
    if scope is not None:
        filters.append(ConversationThread.scope == scope)
    if scope_id is not None:
        filters.append(ConversationThread.scope_id == scope_id)
    if status is not None:
        filters.append(ConversationThread.status == status)
    if participant_id is not None:
        # PostgreSQL array contains check
        filters.append(ConversationThread.participant_ids.any(participant_id))

    # Count query
    count_stmt = select(func.count()).select_from(ConversationThread).where(*filters)
    count_result = await session.execute(count_stmt)
    total_count = count_result.scalar_one()

    # Data query
    data_stmt = (
        select(ConversationThread)
        .where(*filters)
        .order_by(ConversationThread.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    data_result = await session.execute(data_stmt)
    threads = data_result.scalars().all()

    thread_reads = [ConversationThreadRead.model_validate(t) for t in threads]
    return thread_reads, total_count


async def append_message(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
    data: ConversationMessageCreate,
    s3_adapter: S3StorageAdapter | None = None,
) -> ConversationMessageRead:
    """Append a message to a conversation thread.

    Args:
        session: Database session
        tenant_id: Tenant identifier
        thread_id: Thread UUID
        data: Message creation data
        s3_adapter: S3 adapter for storing large message content

    Returns:
        Created message read schema

    Raises:
        ThreadNotFoundError: If thread does not exist
        ThreadNotActiveError: If thread is not active
    """
    # Load thread
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
        ConversationThread.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()

    if thread is None:
        raise ThreadNotFoundError(thread_id)

    if thread.status != "active":
        raise ThreadNotActiveError(thread_id, thread.status)

    # Compute sequence number
    seq_stmt = select(func.coalesce(func.max(ConversationMessage.sequence_number), 0) + 1).where(
        ConversationMessage.thread_id == thread_id
    )
    seq_result = await session.execute(seq_stmt)
    sequence_number = seq_result.scalar_one()

    # Compute content size and determine storage
    content_size = len(data.content.encode("utf-8")) if data.content else None
    storage_type = "inline"
    content_ref = None
    content_to_store = data.content

    # Check if we should use S3
    app_settings = AppSettings()
    if (
        data.content is not None
        and content_size is not None
        and content_size > app_settings.conv_inline_max_bytes
        and s3_adapter is not None
    ):
        # Upload to S3
        content_ref = await _store_message_s3(s3_adapter, tenant_id, thread_id, sequence_number, data.content)
        storage_type = "s3"
        content_to_store = None  # Don't store in DB

    # Create message
    message = ConversationMessage(
        id=uuid.uuid4(),
        thread_id=thread_id,
        sequence_number=sequence_number,
        role=data.role,
        actor_id=data.actor_id,
        content=content_to_store,
        tool_call_id=data.tool_call_id,
        metadata_=data.metadata,
        storage_type=storage_type,
        content_size=content_size,
        content_ref=content_ref,
        tenant_id=tenant_id,
        created_at=datetime.now(UTC),
    )

    session.add(message)
    await session.commit()
    await session.refresh(message)

    # For S3 messages, restore content for the response
    if storage_type == "s3" and data.content is not None:
        message.content = data.content

    return ConversationMessageRead.model_validate(message)


async def archive_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
) -> ConversationThreadRead:
    """Archive a conversation thread.

    Args:
        session: Database session
        tenant_id: Tenant identifier
        thread_id: Thread UUID

    Returns:
        Updated thread read schema

    Raises:
        ThreadNotFoundError: If thread does not exist
        ThreadNotActiveError: If thread is not active
    """
    # Load thread
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
        ConversationThread.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()

    if thread is None:
        raise ThreadNotFoundError(thread_id)

    if thread.status != "active":
        raise ThreadNotActiveError(thread_id, thread.status)

    # Archive the thread
    thread.status = "archived"
    thread.archived_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(thread)

    return ConversationThreadRead.model_validate(thread)


async def fork_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
    from_sequence: int,
    owner_id: str,
    actor_id: str | None = None,
    title: str | None = None,
) -> ConversationThreadRead:
    """Create a divergent copy of a thread up to a fork point.

    Copies messages with sequence_number <= from_sequence into a new thread.
    The new thread gets a fresh ID, resets extraction_cursor to 0, and is
    owned by the forking caller.
    """
    # Load source thread
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
        ConversationThread.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    source = result.scalar_one_or_none()

    if source is None:
        raise ThreadNotFoundError(thread_id)

    # Create forked thread
    new_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    retention = source.retention_policy or {}
    ttl_days = retention.get("ttl_days", 90)

    forked = ConversationThread(
        id=new_id,
        tenant_id=tenant_id,
        scope=source.scope,
        scope_id=source.scope_id,
        owner_id=owner_id,
        actor_id=actor_id,
        participant_ids=[owner_id],
        title=title or f"Fork of {source.title or str(thread_id)[:8]}",
        retention_policy=source.retention_policy,
        status="active",
        extraction_cursor=0,
        created_at=created_at,
        expires_at=created_at + timedelta(days=ttl_days),
        metadata_={"forked_from": str(thread_id), "fork_sequence": from_sequence},
    )
    session.add(forked)

    # Copy messages up to fork point
    msg_stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.thread_id == thread_id,
            ConversationMessage.sequence_number <= from_sequence,
        )
        .order_by(ConversationMessage.sequence_number.asc())
    )
    msg_result = await session.execute(msg_stmt)
    source_messages = list(msg_result.scalars().all())

    for seq, msg in enumerate(source_messages, start=1):
        new_msg = ConversationMessage(
            id=uuid.uuid4(),
            thread_id=new_id,
            sequence_number=seq,
            role=msg.role,
            actor_id=msg.actor_id,
            content=msg.content,
            storage_type=msg.storage_type,
            content_ref=msg.content_ref,
            content_size=msg.content_size,
            tool_call_id=msg.tool_call_id,
            metadata_=msg.metadata_,
            tenant_id=tenant_id,
            created_at=datetime.now(UTC),
        )
        session.add(new_msg)

    await session.commit()
    await session.refresh(forked)

    return ConversationThreadRead.model_validate(forked)


async def share_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
    grantee_id: str,
    access_level: str,
    authorized_by: str,
) -> ConversationThreadRead:
    """Grant access to a thread participant.

    Adds the grantee to participant_ids and sets their access level in
    participant_access. Idempotent: re-sharing updates the access level.
    """
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
        ConversationThread.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()

    if thread is None:
        raise ThreadNotFoundError(thread_id)

    # Add to participant_ids if not present
    participants = list(thread.participant_ids or [])
    if grantee_id not in participants:
        participants.append(grantee_id)
    thread.participant_ids = participants

    # Set access level
    access = dict(thread.participant_access or {})
    access[grantee_id] = access_level
    thread.participant_access = access
    flag_modified(thread, "participant_access")

    # Record who authorized this share
    meta = dict(thread.metadata_ or {})
    shares = meta.get("share_grants", [])
    shares.append({
        "grantee_id": grantee_id,
        "access_level": access_level,
        "authorized_by": authorized_by,
        "granted_at": datetime.now(UTC).isoformat(),
    })
    meta["share_grants"] = shares
    thread.metadata_ = meta
    flag_modified(thread, "metadata_")

    await session.commit()
    await session.refresh(thread)

    return ConversationThreadRead.model_validate(thread)


async def lookup_thread_by_a2a_context(
    session: AsyncSession,
    *,
    tenant_id: str,
    a2a_context_id: str,
) -> uuid.UUID | None:
    """Look up a thread by A2A context ID. Returns thread_id or None."""
    stmt = select(ConversationThread.id).where(
        ConversationThread.a2a_context_id == a2a_context_id,
        ConversationThread.tenant_id == tenant_id,
        ConversationThread.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Retention, deletion, and purge
# ---------------------------------------------------------------------------


async def _cascade_to_memories(
    session: AsyncSession,
    thread_id: uuid.UUID,
    cascade_mode: str,
    s3_adapter: S3StorageAdapter | None = None,
) -> int:
    """Apply cascade policy to extracted memories. Returns count affected."""
    from memoryhub_core.services.memory import delete_memory

    if cascade_mode == "preserve":
        return 0

    # Find extraction records for this thread
    ext_stmt = select(ConversationExtraction).where(
        ConversationExtraction.thread_id == thread_id,
    )
    ext_result = await session.execute(ext_stmt)
    extractions = list(ext_result.scalars().all())

    if not extractions:
        return 0

    if cascade_mode == "orphan":
        # Sever provenance links -- delete extraction records, keep memories
        await session.execute(
            sa_delete(ConversationExtraction).where(
                ConversationExtraction.thread_id == thread_id,
            )
        )
        return len(extractions)

    # cascade_mode == "delete" (default)
    # Soft-delete memories that have no other provenance source
    deleted_count = 0
    for ext in extractions:
        # Count how many extraction records point to this memory
        count_stmt = select(func.count()).select_from(ConversationExtraction).where(
            ConversationExtraction.memory_node_id == ext.memory_node_id,
        )
        count_result = await session.execute(count_stmt)
        total_refs = count_result.scalar_one()

        if total_refs <= 1:
            try:
                await delete_memory(ext.memory_node_id, session, s3_adapter=s3_adapter)
                deleted_count += 1
            except Exception:
                logger.warning(
                    "Failed to cascade-delete memory %s from thread %s",
                    ext.memory_node_id, thread_id, exc_info=True,
                )

    return deleted_count


async def soft_delete_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
    purged_by: str,
    reason: str = "retention",
    cascade_override: str | None = None,
) -> ConversationThreadRead:
    """Soft-delete a thread with cascade per retention policy.

    If the thread has legal_hold=True, status is set to 'pending_deletion'
    instead of 'deleted'. Cascade is applied regardless.
    """
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()

    if thread is None:
        raise ThreadNotFoundError(thread_id)

    now = datetime.now(UTC)

    if thread.legal_hold:
        thread.status = "pending_deletion"
    else:
        thread.status = "deleted"
        thread.deleted_at = now

    # Determine cascade mode
    retention = thread.retention_policy or {}
    cascade_mode = cascade_override or retention.get("cascade_to_memories", "delete")

    await _cascade_to_memories(session, thread_id, cascade_mode)

    # Audit record
    log_entry = PurgeLog(
        id=uuid.uuid4(),
        resource_type="thread",
        resource_id=thread_id,
        purged_by=purged_by,
        reason=reason,
    )
    session.add(log_entry)

    await session.commit()
    await session.refresh(thread)

    return ConversationThreadRead.model_validate(thread)


async def hard_delete_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
    s3_adapter: S3StorageAdapter | None = None,
) -> None:
    """Physically remove a thread and all associated data.

    Raises ThreadNotFoundError if the thread doesn't exist.
    Raises ValueError if the thread has legal_hold=True.
    """
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()

    if thread is None:
        raise ThreadNotFoundError(thread_id)

    if thread.legal_hold:
        raise ValueError(
            f"Cannot hard-delete thread {thread_id}: legal_hold is active. "
            "Use admin_purge_thread with justification to override."
        )

    # Collect S3 refs before deletion
    if s3_adapter is not None:
        s3_stmt = select(ConversationMessage.content_ref).where(
            ConversationMessage.thread_id == thread_id,
            ConversationMessage.storage_type == "s3",
            ConversationMessage.content_ref.isnot(None),
        )
        s3_result = await session.execute(s3_stmt)
        s3_refs = [row[0] for row in s3_result.all()]
    else:
        s3_refs = []

    # Delete extraction records (RESTRICT FK requires explicit delete)
    await session.execute(
        sa_delete(ConversationExtraction).where(
            ConversationExtraction.thread_id == thread_id,
        )
    )

    # Delete thread (CASCADE handles messages and extraction_failures)
    await session.execute(
        sa_delete(ConversationThread).where(
            ConversationThread.id == thread_id,
        )
    )

    await session.commit()

    # Clean up S3 objects after DB commit
    if s3_refs and s3_adapter is not None:
        await s3_adapter.delete_contents(s3_refs)


async def admin_purge_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
    purged_by: str,
    justification: str,
    s3_adapter: S3StorageAdapter | None = None,
) -> None:
    """Hard-delete with audit. Overrides legal hold with justification."""
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()

    if thread is None:
        raise ThreadNotFoundError(thread_id)

    # Cascade-delete extracted memories
    await _cascade_to_memories(session, thread_id, "delete", s3_adapter=s3_adapter)

    # Audit record
    log_entry = PurgeLog(
        id=uuid.uuid4(),
        resource_type="thread",
        resource_id=thread_id,
        purged_by=purged_by,
        reason="admin",
        incident_ref=justification,
    )
    session.add(log_entry)

    # Clear legal hold to allow hard delete
    if thread.legal_hold:
        thread.legal_hold = False
        await session.flush()

    # Collect S3 refs
    s3_refs: list[str] = []
    if s3_adapter is not None:
        s3_stmt = select(ConversationMessage.content_ref).where(
            ConversationMessage.thread_id == thread_id,
            ConversationMessage.storage_type == "s3",
            ConversationMessage.content_ref.isnot(None),
        )
        s3_result = await session.execute(s3_stmt)
        s3_refs = [row[0] for row in s3_result.all()]

    # Delete extraction records, then thread (CASCADE handles messages)
    await session.execute(
        sa_delete(ConversationExtraction).where(
            ConversationExtraction.thread_id == thread_id,
        )
    )
    await session.execute(
        sa_delete(ConversationThread).where(ConversationThread.id == thread_id)
    )

    await session.commit()

    if s3_refs and s3_adapter is not None:
        await s3_adapter.delete_contents(s3_refs)


async def spill_response_thread(
    session: AsyncSession,
    *,
    tenant_id: str,
    thread_id: uuid.UUID,
    purged_by: str,
    incident_ref: str,
    silent: bool = False,
    s3_adapter: S3StorageAdapter | None = None,
) -> None:
    """Atomic hard-delete for spill response. Bypasses all holds."""
    stmt = select(ConversationThread).where(
        ConversationThread.id == thread_id,
        ConversationThread.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()

    if thread is None:
        raise ThreadNotFoundError(thread_id)

    # Collect S3 refs
    s3_refs: list[str] = []
    if s3_adapter is not None:
        s3_stmt = select(ConversationMessage.content_ref).where(
            ConversationMessage.thread_id == thread_id,
            ConversationMessage.storage_type == "s3",
            ConversationMessage.content_ref.isnot(None),
        )
        s3_result = await session.execute(s3_stmt)
        s3_refs = [row[0] for row in s3_result.all()]

    # Tombstone (unless silent)
    if not silent:
        log_entry = PurgeLog(
            id=uuid.uuid4(),
            resource_type="thread",
            resource_id=thread_id,
            purged_by=purged_by,
            reason="spill",
            incident_ref=incident_ref,
        )
        session.add(log_entry)

    # Clear legal hold, delete extractions, delete thread
    if thread.legal_hold:
        thread.legal_hold = False
        await session.flush()

    await session.execute(
        sa_delete(ConversationExtraction).where(
            ConversationExtraction.thread_id == thread_id,
        )
    )
    await session.execute(
        sa_delete(ConversationThread).where(ConversationThread.id == thread_id)
    )

    await session.commit()

    if s3_refs and s3_adapter is not None:
        await s3_adapter.delete_contents(s3_refs)


async def run_retention_sweep(
    session: AsyncSession,
    *,
    s3_adapter: S3StorageAdapter | None = None,
) -> dict:
    """Run the daily retention sweep. Idempotent.

    Phase 1: Soft-delete threads where expires_at <= now() and status='active'.
    Phase 2: Hard-delete threads where deleted_at + min_retention expired and no legal hold.
    """
    now = datetime.now(UTC)
    summary = {"soft_deleted": 0, "hard_deleted": 0, "skipped_legal_hold": 0}

    # Phase 1: soft-delete expired threads
    expired_stmt = select(ConversationThread).where(
        ConversationThread.status == "active",
        ConversationThread.expires_at.isnot(None),
        ConversationThread.expires_at <= now,
    )
    expired_result = await session.execute(expired_stmt)
    expired_threads = list(expired_result.scalars().all())

    for thread in expired_threads:
        try:
            await soft_delete_thread(
                session,
                tenant_id=thread.tenant_id,
                thread_id=thread.id,
                purged_by="retention_sweep",
                reason="retention",
            )
            summary["soft_deleted"] += 1
        except Exception:
            logger.warning("Failed to soft-delete thread %s", thread.id, exc_info=True)

    # Phase 2: hard-delete threads past min_retention_days
    deleted_stmt = select(ConversationThread).where(
        ConversationThread.status == "deleted",
        ConversationThread.deleted_at.isnot(None),
        ConversationThread.legal_hold.is_(False),
    )
    deleted_result = await session.execute(deleted_stmt)
    deleted_threads = list(deleted_result.scalars().all())

    for thread in deleted_threads:
        retention = thread.retention_policy or {}
        min_days = retention.get("min_retention_days", 30)
        cutoff = thread.deleted_at + timedelta(days=min_days)

        if now < cutoff:
            continue

        try:
            await hard_delete_thread(
                session,
                tenant_id=thread.tenant_id,
                thread_id=thread.id,
                s3_adapter=s3_adapter,
            )
            summary["hard_deleted"] += 1
        except Exception:
            logger.warning("Failed to hard-delete thread %s", thread.id, exc_info=True)

    # Count legal hold threads that would otherwise be deleted
    hold_stmt = select(func.count()).select_from(ConversationThread).where(
        ConversationThread.status == "pending_deletion",
        ConversationThread.legal_hold.is_(True),
    )
    hold_result = await session.execute(hold_stmt)
    summary["skipped_legal_hold"] = hold_result.scalar_one()

    return summary
