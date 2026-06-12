"""Conversation thread tool with action dispatch.

Exposes thread CRUD as a single ``thread`` tool with 5 actions (Phase 2):
create, append, get, list, archive. Follows the memory(action=...) pattern.
"""

import logging
import uuid
from typing import Annotated, Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from src.core.app import mcp

logger = logging.getLogger(__name__)

_VALID_ACTIONS = frozenset({"create", "append", "get", "list", "archive"})

_CREATE_OPTS = frozenset({
    "title", "participant_ids", "participant_access",
    "a2a_context_id", "metadata",
})
_APPEND_OPTS = frozenset({"actor_id", "tool_call_id", "metadata"})
_GET_OPTS = frozenset({"limit", "before_sequence", "include_messages"})
_LIST_OPTS = frozenset({
    "scope_id", "status", "participant_id", "limit", "offset",
})
_ARCHIVE_OPTS = frozenset({"reason"})


def _require(action: str, name: str, value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolError(
            f"action='{action}' requires '{name}'. "
            f"Example: thread(action='{action}', {name}='...')"
        )
    return value


def _forward(opts: dict, valid_keys: frozenset) -> dict:
    return {k: v for k, v in opts.items() if k in valid_keys}


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def thread(
    action: Annotated[
        str,
        Field(description=(
            "The operation to perform: create, append, get, list, archive."
        )),
    ],
    thread_id: Annotated[
        str | None,
        Field(description=(
            "Thread UUID. Required for: append, get, archive."
        )),
    ] = None,
    content: Annotated[
        str | None,
        Field(description="Message content. Required for: append."),
    ] = None,
    scope: Annotated[
        str | None,
        Field(description=(
            "Scope: user, project, campaign, role, organizational, enterprise. "
            "Required for: create. Optional filter for: list."
        )),
    ] = None,
    role: Annotated[
        str | None,
        Field(description=(
            "Message role: user, assistant, tool_call, tool_result, system. "
            "Required for: append."
        )),
    ] = None,
    options: Annotated[
        dict[str, Any] | None,
        Field(description="Action-specific parameters."),
    ] = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Conversation thread operations. Call register_session first.

    Actions:
      create(scope, [options: title, participant_ids, participant_access,
             a2a_context_id, metadata])
        Create a new conversation thread.
      append(thread_id, role, content, [options: actor_id, tool_call_id,
             metadata])
        Append a message to a thread. Sequence number auto-assigned.
      get(thread_id, [options: limit (50), before_sequence, include_messages])
        Retrieve thread metadata and paginated messages.
      list([scope, options: scope_id, status (active), participant_id,
           limit (20), offset])
        List threads visible to the caller.
      archive(thread_id, [options: reason])
        Archive a thread. Immutable thereafter.
    """
    if action not in _VALID_ACTIONS:
        raise ToolError(
            f"Invalid action '{action}'. Must be one of: "
            f"{', '.join(sorted(_VALID_ACTIONS))}."
        )

    opts = options or {}

    if action == "create":
        return await _dispatch_create(scope, opts, ctx)
    if action == "append":
        return await _dispatch_append(thread_id, role, content, opts, ctx)
    if action == "get":
        return await _dispatch_get(thread_id, opts, ctx)
    if action == "list":
        return await _dispatch_list(scope, opts, ctx)
    return await _dispatch_archive(thread_id, opts, ctx)


async def _dispatch_create(scope, opts, ctx):
    from memoryhub_core.models.schemas import ConversationThreadCreate
    from memoryhub_core.services.conversation import create_thread
    from src.core.authz import (
        AuthenticationError,
        authorize_write,
        get_claims_from_context,
        get_tenant_filter,
    )
    from src.tools._deps import get_db_session, release_db_session

    _require("create", "scope", scope)

    try:
        claims = get_claims_from_context()
    except AuthenticationError as exc:
        raise ToolError(str(exc)) from exc

    tenant = get_tenant_filter(claims)
    caller_id = claims["sub"]

    if not authorize_write(claims, scope, owner_id=caller_id, tenant_id=tenant):
        raise ToolError(f"Not authorized to create threads in scope '{scope}'.")

    create_opts = _forward(opts, _CREATE_OPTS)
    data = ConversationThreadCreate(scope=scope, **create_opts)

    session, gen = await get_db_session()
    try:
        result = await create_thread(
            session,
            tenant_id=tenant,
            data=data,
            owner_id=caller_id,
            actor_id=caller_id,
            driver_id=claims.get("driver_id"),
        )
        if ctx is not None:
            await ctx.info(f"Created thread {result.id}")
        return result.model_dump(mode="json")
    finally:
        await release_db_session(gen)


async def _dispatch_append(thread_id_str, role, content, opts, ctx):
    from memoryhub_core.models.schemas import ConversationMessageCreate
    from memoryhub_core.services.conversation import append_message, get_thread
    from memoryhub_core.services.exceptions import (
        ThreadNotActiveError,
        ThreadNotFoundError,
    )
    from src.core.authz import (
        AuthenticationError,
        authorize_thread_write,
        get_claims_from_context,
        get_tenant_filter,
    )
    from src.tools._deps import get_db_session, get_s3_adapter, release_db_session

    _require("append", "thread_id", thread_id_str)
    _require("append", "role", role)
    _require("append", "content", content)

    try:
        tid = uuid.UUID(thread_id_str)
    except ValueError as exc:
        raise ToolError(f"Invalid thread_id: {thread_id_str}") from exc

    try:
        claims = get_claims_from_context()
    except AuthenticationError as exc:
        raise ToolError(str(exc)) from exc

    tenant = get_tenant_filter(claims)
    s3_adapter = get_s3_adapter()

    # Load thread to check auth
    session, gen = await get_db_session()
    try:
        thread_data = await get_thread(
            session, tenant_id=tenant, thread_id=tid, include_messages=False,
        )
        if thread_data is None:
            raise ToolError("Thread not found.")

        # Auth check against the ORM object (re-query for the actual model)
        from sqlalchemy import select

        from memoryhub_core.models.conversation import ConversationThread

        stmt = select(ConversationThread).where(
            ConversationThread.id == tid,
            ConversationThread.tenant_id == tenant,
        )
        result = await session.execute(stmt)
        thread_obj = result.scalar_one_or_none()

        if not authorize_thread_write(claims, thread_obj):
            raise ToolError("Not authorized to append to this thread.")

        append_opts = _forward(opts, _APPEND_OPTS)
        data = ConversationMessageCreate(
            thread_id=tid,
            role=role,
            content=content,
            **append_opts,
        )

        msg = await append_message(
            session, tenant_id=tenant, thread_id=tid,
            data=data, s3_adapter=s3_adapter,
        )
        if ctx is not None:
            await ctx.info(f"Appended message seq={msg.sequence_number}")
        return msg.model_dump(mode="json")
    except ThreadNotFoundError as exc:
        raise ToolError("Thread not found.") from exc
    except ThreadNotActiveError as exc:
        raise ToolError(str(exc)) from exc
    finally:
        await release_db_session(gen)


async def _dispatch_get(thread_id_str, opts, ctx):
    from memoryhub_core.services.conversation import get_thread
    from src.core.authz import (
        AuthenticationError,
        authorize_thread_read,
        get_claims_from_context,
        get_tenant_filter,
    )
    from src.tools._deps import get_db_session, get_s3_adapter, release_db_session

    _require("get", "thread_id", thread_id_str)

    try:
        tid = uuid.UUID(thread_id_str)
    except ValueError as exc:
        raise ToolError(f"Invalid thread_id: {thread_id_str}") from exc

    try:
        claims = get_claims_from_context()
    except AuthenticationError as exc:
        raise ToolError(str(exc)) from exc

    tenant = get_tenant_filter(claims)
    s3_adapter = get_s3_adapter()
    get_opts = _forward(opts, _GET_OPTS)

    session, gen = await get_db_session()
    try:
        result = await get_thread(
            session, tenant_id=tenant, thread_id=tid,
            s3_adapter=s3_adapter, **get_opts,
        )
        if result is None:
            raise ToolError("Thread not found.")

        # Auth check
        from sqlalchemy import select

        from memoryhub_core.models.conversation import ConversationThread

        stmt = select(ConversationThread).where(
            ConversationThread.id == tid,
            ConversationThread.tenant_id == tenant,
        )
        res = await session.execute(stmt)
        thread_obj = res.scalar_one_or_none()

        if not authorize_thread_read(claims, thread_obj):
            raise ToolError("Thread not found.")

        # Serialize
        output: dict[str, Any] = {
            "thread": result["thread"].model_dump(mode="json"),
        }
        if "messages" in result:
            output["messages"] = [
                m.model_dump(mode="json") for m in result["messages"]
            ]
            output["has_more"] = result.get("has_more", False)
        return output
    finally:
        await release_db_session(gen)


async def _dispatch_list(scope, opts, ctx):
    from memoryhub_core.services.conversation import list_threads
    from src.core.authz import (
        AuthenticationError,
        get_claims_from_context,
        get_tenant_filter,
    )
    from src.tools._deps import get_db_session, release_db_session

    try:
        claims = get_claims_from_context()
    except AuthenticationError as exc:
        raise ToolError(str(exc)) from exc

    tenant = get_tenant_filter(claims)
    caller_id = claims["sub"]
    list_opts = _forward(opts, _LIST_OPTS)

    session, gen = await get_db_session()
    try:
        threads, total = await list_threads(
            session,
            tenant_id=tenant,
            owner_id=caller_id,
            scope=scope,
            **list_opts,
        )
        return {
            "threads": [t.model_dump(mode="json") for t in threads],
            "total": total,
        }
    finally:
        await release_db_session(gen)


async def _dispatch_archive(thread_id_str, opts, ctx):
    from memoryhub_core.services.conversation import archive_thread
    from memoryhub_core.services.exceptions import (
        ThreadNotActiveError,
        ThreadNotFoundError,
    )
    from src.core.authz import (
        AuthenticationError,
        authorize_thread_admin,
        get_claims_from_context,
        get_tenant_filter,
    )
    from src.tools._deps import get_db_session, release_db_session

    _require("archive", "thread_id", thread_id_str)

    try:
        tid = uuid.UUID(thread_id_str)
    except ValueError as exc:
        raise ToolError(f"Invalid thread_id: {thread_id_str}") from exc

    try:
        claims = get_claims_from_context()
    except AuthenticationError as exc:
        raise ToolError(str(exc)) from exc

    tenant = get_tenant_filter(claims)

    session, gen = await get_db_session()
    try:
        # Auth check
        from sqlalchemy import select

        from memoryhub_core.models.conversation import ConversationThread

        stmt = select(ConversationThread).where(
            ConversationThread.id == tid,
            ConversationThread.tenant_id == tenant,
        )
        res = await session.execute(stmt)
        thread_obj = res.scalar_one_or_none()

        if thread_obj is None:
            raise ToolError("Thread not found.")
        if not authorize_thread_admin(claims, thread_obj):
            raise ToolError("Not authorized to archive this thread.")

        result = await archive_thread(session, tenant_id=tenant, thread_id=tid)
        if ctx is not None:
            await ctx.info(f"Archived thread {tid}")
        return result.model_dump(mode="json")
    except ThreadNotFoundError as exc:
        raise ToolError("Thread not found.") from exc
    except ThreadNotActiveError as exc:
        raise ToolError(str(exc)) from exc
    finally:
        await release_db_session(gen)
