"""Admin content moderation tool (issue #45).

Action-dispatch tool for admin operations: search, quarantine, restore,
and hard_delete. All actions require memory:admin scope. Cross-tenant
search additionally requires memory:admin:cross_tenant.
"""

import logging
import uuid as uuid_module
from typing import Annotated, Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from src.core.app import mcp
from src.core.audit import record_event

logger = logging.getLogger(__name__)

_VALID_ACTIONS = frozenset({"search", "quarantine", "restore", "hard_delete"})

_SEARCH_OPTS = frozenset({
    "regex", "cross_tenant", "scope_filter", "max_results",
    "include_statuses",
})
_QUARANTINE_OPTS = frozenset({"reason", "incident_reference"})
_RESTORE_OPTS = frozenset({"reason"})
_HARD_DELETE_OPTS = frozenset({
    "reason", "incident_reference", "sanitized_audit",
})


def _check_admin_scope(scopes: list[str]) -> None:
    """Verify the caller has memory:admin scope."""
    if "memory:admin" not in scopes:
        raise ToolError(
            "Forbidden: admin_memory requires 'memory:admin' scope. "
            "Your current scopes do not include admin access."
        )


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }
)
async def admin_memory(
    action: Annotated[
        str,
        Field(description=(
            "Admin action: search, quarantine, restore, hard_delete"
        )),
    ],
    memory_id: Annotated[
        str | None,
        Field(description=(
            "Memory UUID (required for quarantine/restore/hard_delete)"
        )),
    ] = None,
    query: Annotated[
        str | None,
        Field(description="Search query (required for search)"),
    ] = None,
    options: Annotated[
        dict[str, Any] | None,
        Field(description=(
            "Action-specific options. "
            "search: regex, cross_tenant, scope_filter, max_results, include_statuses. "
            "quarantine: reason (required), incident_reference. "
            "restore: reason (required). "
            "hard_delete: reason (required), incident_reference, sanitized_audit."
        )),
    ] = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Admin content moderation operations. Requires memory:admin scope.

    Actions:
      search(query, [options: regex, cross_tenant, scope_filter, max_results, include_statuses])
        Cross-owner search ignoring ownership boundaries within a tenant.
        Combines semantic similarity with optional regex matching.
        Results are NOT persisted (spill response safety).

      quarantine(memory_id, options: {reason}, [options: incident_reference])
        Hide memory from all non-admin queries immediately. Content and
        embeddings are preserved for admin review.

      restore(memory_id, options: {reason})
        Restore a quarantined memory to active status.

      hard_delete(memory_id, options: {reason}, [options: incident_reference, sanitized_audit])
        Physically remove memory from database. IRREVERSIBLE.
        Set sanitized_audit=true for classified data spill response --
        audit entry will contain only a SHA-256 content hash, no content.
    """
    if action not in _VALID_ACTIONS:
        raise ToolError(
            f"Invalid admin action '{action}'. Must be one of: "
            f"{', '.join(sorted(_VALID_ACTIONS))}."
        )

    # Resolve identity
    from src.core.authz import get_claims_from_context, get_tenant_filter

    claims = get_claims_from_context()
    scopes = claims.get("scopes", [])
    _check_admin_scope(scopes)

    tenant_id = get_tenant_filter(claims)
    actor_id = claims["sub"]
    opts = options or {}

    if action == "search":
        return await _dispatch_search(
            query, tenant_id, actor_id, scopes, opts,
        )
    if action == "quarantine":
        return await _dispatch_quarantine(
            memory_id, tenant_id, actor_id, opts,
        )
    if action == "restore":
        return await _dispatch_restore(
            memory_id, tenant_id, actor_id, opts,
        )
    # hard_delete
    return await _dispatch_hard_delete(
        memory_id, tenant_id, actor_id, scopes, opts,
    )


async def _dispatch_search(
    query: str | None,
    tenant_id: str,
    actor_id: str,
    scopes: list[str],
    opts: dict,
) -> dict:
    """Dispatch admin search."""
    from memoryhub_core.services.admin import search_memory_admin
    from src.tools._deps import get_db_session, get_embedding_service, release_db_session

    if not query or (isinstance(query, str) and not query.strip()):
        raise ToolError(
            "action='search' requires 'query'. "
            "Example: admin_memory(action='search', query='API key')"
        )

    cross_tenant = opts.get("cross_tenant", False)
    if cross_tenant and "memory:admin:cross_tenant" not in scopes:
        raise ToolError(
            "Forbidden: cross-tenant admin search requires "
            "'memory:admin:cross_tenant' scope."
        )

    session, gen = await get_db_session()
    try:
        embedding_service = get_embedding_service()

        kwargs: dict = {
            "query": query,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
        }
        if "regex" in opts:
            kwargs["regex"] = opts["regex"]
        if cross_tenant:
            kwargs["cross_tenant"] = True
        if "scope_filter" in opts:
            kwargs["scope_filter"] = opts["scope_filter"]
        if "max_results" in opts:
            kwargs["max_results"] = int(opts["max_results"])
        if "include_statuses" in opts:
            kwargs["include_statuses"] = opts["include_statuses"]

        try:
            results = await search_memory_admin(
                session, embedding_service, **kwargs,
            )
        except ValueError as e:
            raise ToolError(str(e)) from e

        return {
            "results": results,
            "total": len(results),
            "query": query,
            "regex": opts.get("regex"),
        }
    finally:
        await release_db_session(gen)


async def _dispatch_quarantine(
    memory_id: str | None,
    tenant_id: str,
    actor_id: str,
    opts: dict,
) -> dict:
    """Dispatch quarantine operation."""
    from memoryhub_core.services.admin import quarantine_memory
    from src.tools._deps import get_db_session, release_db_session

    if not memory_id:
        raise ToolError(
            "action='quarantine' requires 'memory_id'. "
            "Example: admin_memory(action='quarantine', memory_id='...')"
        )
    reason = opts.get("reason")
    if not reason or (isinstance(reason, str) and not reason.strip()):
        raise ToolError(
            "action='quarantine' requires 'reason' in options. "
            "Example: admin_memory(action='quarantine', memory_id='...', "
            "options={'reason': 'Suspected PII leak'})"
        )

    session, gen = await get_db_session()
    try:
        result = await quarantine_memory(
            session,
            memory_id=uuid_module.UUID(memory_id),
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=reason,
            incident_reference=opts.get("incident_reference"),
        )

        # MCP-layer audit
        record_event(
            event_type="admin.quarantine",
            actor_id=actor_id,
            driver_id=actor_id,
            scope="admin",
            owner_id=actor_id,
            memory_id=memory_id,
            decision="allowed",
            metadata={"reason": reason},
        )

        return result
    except Exception as e:
        if "not found" in str(e).lower():
            raise ToolError(f"Memory {memory_id} not found") from e
        raise
    finally:
        await release_db_session(gen)


async def _dispatch_restore(
    memory_id: str | None,
    tenant_id: str,
    actor_id: str,
    opts: dict,
) -> dict:
    """Dispatch restore operation."""
    from memoryhub_core.services.admin import restore_memory
    from src.tools._deps import get_db_session, release_db_session

    if not memory_id:
        raise ToolError(
            "action='restore' requires 'memory_id'. "
            "Example: admin_memory(action='restore', memory_id='...')"
        )
    reason = opts.get("reason")
    if not reason or (isinstance(reason, str) and not reason.strip()):
        raise ToolError(
            "action='restore' requires 'reason' in options. "
            "Example: admin_memory(action='restore', memory_id='...', "
            "options={'reason': 'Content reviewed, no issues found'})"
        )

    session, gen = await get_db_session()
    try:
        result = await restore_memory(
            session,
            memory_id=uuid_module.UUID(memory_id),
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=reason,
        )

        record_event(
            event_type="admin.restore",
            actor_id=actor_id,
            driver_id=actor_id,
            scope="admin",
            owner_id=actor_id,
            memory_id=memory_id,
            decision="allowed",
            metadata={"reason": reason},
        )

        return result
    except Exception as e:
        if "not found" in str(e).lower():
            raise ToolError(f"Memory {memory_id} not found") from e
        raise
    finally:
        await release_db_session(gen)


async def _dispatch_hard_delete(
    memory_id: str | None,
    tenant_id: str,
    actor_id: str,
    scopes: list[str],
    opts: dict,
) -> dict:
    """Dispatch hard delete operation."""
    from memoryhub_core.services.admin import hard_delete_memory
    from src.tools._deps import get_db_session, release_db_session

    if not memory_id:
        raise ToolError(
            "action='hard_delete' requires 'memory_id'. "
            "Example: admin_memory(action='hard_delete', memory_id='...')"
        )
    reason = opts.get("reason")
    if not reason or (isinstance(reason, str) and not reason.strip()):
        raise ToolError(
            "action='hard_delete' requires 'reason' in options. "
            "Example: admin_memory(action='hard_delete', memory_id='...', "
            "options={'reason': 'Classified data spill cleanup'})"
        )

    sanitized = opts.get("sanitized_audit", False)
    if sanitized and "memory:admin:sanitized_audit" not in scopes:
        raise ToolError(
            "Forbidden: sanitized_audit mode requires "
            "'memory:admin:sanitized_audit' scope."
        )

    session, gen = await get_db_session()
    try:
        result = await hard_delete_memory(
            session,
            memory_id=uuid_module.UUID(memory_id),
            tenant_id=tenant_id,
            actor_id=actor_id,
            reason=reason,
            incident_reference=opts.get("incident_reference"),
            sanitized_audit=sanitized,
        )

        record_event(
            event_type="admin.hard_delete",
            actor_id=actor_id,
            driver_id=actor_id,
            scope="admin",
            owner_id=actor_id,
            memory_id=memory_id,
            decision="allowed",
            metadata={
                "reason": reason,
                "sanitized_audit": sanitized,
                "versions_deleted": result.get("versions_deleted", 0),
            },
        )

        return result
    except Exception as e:
        if "not found" in str(e).lower():
            raise ToolError(f"Memory {memory_id} not found") from e
        raise
    finally:
        await release_db_session(gen)
