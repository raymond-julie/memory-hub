# Content Moderation

> Status: Design complete -- open questions resolved 2026-06-30

## Problem

Sensitive information ends up in memories that should not contain it. A curation filter misses PII, credentials leak through a regex gap, or -- in the worst case -- data from a higher classification level spills into a lower one. When this happens, an admin needs to find every instance of the problematic content, review the matches, and remove them. Speed and completeness matter: a partial cleanup is worse than none because it creates a false sense of security.

This is an incident-response workflow, not a routine operation. The design must support both the common case (a few memories containing an accidentally-stored API key) and the severe case (a classified data spill requiring physical deletion with sanitized audit trails).

## Classified Data Spill Scenario

This is the highest-severity case and the one that drives the most demanding requirements. When data leaks from a higher classification area to a lower one, the requirement is immediate and complete deletion. Quarantine is insufficient -- the data must be physically removed from all storage layers.

The audit trail creates a tension. Normally, deletion audit entries include `state_before` with the full content of the deleted memory. For a classified spill, that would be another spill -- the audit log would contain the very content being purged. To handle this, the core deletion operations support a `sanitized_audit` mode where the audit entry records only: memory ID, a SHA-256 content hash (for dedup verification -- confirming the same content isn't reintroduced), timestamp, actor identity, and an incident reference string. No content, no embeddings, no metadata that could reconstruct the data.

The search phase creates a second tension. The admin needs broad search capabilities to find all instances of the spilled content, but once a spill is confirmed, the search results themselves become sensitive. The workflow must support an atomic "search, review, confirm spill, hard-delete all matches" flow where intermediate search results are not persisted to any durable store. Results exist only in the admin agent's session context and are discarded when the operation completes or the session ends.

Embeddings must also be deleted. Vector embeddings are not opaque -- research has demonstrated content recovery via embedding inversion attacks. A memory's embedding is as sensitive as its text content and must be removed during spill cleanup.

## Memory Status Model

Memories carry a `status` field that governs their visibility:

| Status | Visible to regular queries | Visible to admin queries | Storage |
|--------|---------------------------|-------------------------|---------|
| `active` | Yes | Yes | Full row in PostgreSQL |
| `quarantined` | No | Yes | Full row, hidden from non-admin queries |
| `soft_deleted` | No | No (unless explicitly queried) | Row retained, pending garbage collection |
| *(hard deleted)* | -- | -- | Row physically removed |

The `quarantined` status is the key intermediate state. It hides a memory from all regular queries immediately -- no agent will retrieve it, no search will return it -- while preserving it for admin review. This lets an admin act fast (quarantine on suspicion) and confirm later (review, then decide between restore and delete).

Soft-delete is the existing mechanism from #42. Hard delete is physical row removal -- after a hard delete, the row is gone from PostgreSQL entirely.

## Operations

Each operation below is a function in the core admin library at `src/core/admin/operations.py`. Authorization is enforced inside the function against the supplied `Identity`; the function refuses calls whose identity does not carry the required scope. Audit entries are written by the core, not by the calling transport. The Transports subsection on each operation lists the wrappers that currently expose it.

### `search_memory_admin`

Cross-owner search that combines keyword/regex matching with semantic similarity. Regular `search_memory` is scoped to the caller's own memories and accessible scopes; `search_memory_admin` ignores ownership boundaries within a tenant. By default it searches the caller's own tenant; cross-tenant search is gated behind a `cross_tenant=True` flag and requires the `memory:admin:cross_tenant` sub-scope. This prevents an admin in one organization from inadvertently (or intentionally) reaching into another tenant's data.

The function runs both search modes (keyword/regex against content text, and embedding similarity against vectors) and unions the results, deduplicating by memory ID. This matters for spill response: a regex catches exact string matches, while embedding similarity catches paraphrased or summarized versions of the same content.

Search results are returned to the caller but not persisted. There is no server-side result cache, no temporary table, no audit entry containing the result content. The audit log records that an admin search was performed, with the query parameters and the count of results -- not the result content itself.

```python
def search_memory_admin(
    identity: Identity,
    query: str,
    regex: str | None = None,
    cross_tenant: bool = False,
    scope_filter: str | None = None,
    max_results: int = 50,
) -> list[MemoryHit]
```

**Authorization.** Identity must carry `memory:admin`. If `cross_tenant=True`, identity must also carry `memory:admin:cross_tenant`. Otherwise the function raises `AuthorizationError`.

**Audit.** A single `admin_search` entry is written before results are returned. The entry records the query parameters and result count; it does not record result content.

**Transports.**
- MCP tool: `admin_search_memory` (parameters as above)
- BFF route: `POST /api/admin/memories/search`
- Worker entry point: not applicable (interactive operation)

### `quarantine_memory`

Sets a memory's status to `quarantined`, hiding it from all non-admin queries immediately. The memory still exists in PostgreSQL with its full content, embeddings, and relationships intact -- it's just invisible to regular search and read operations.

Quarantine is the recommended first response when suspicious content is identified. It stops the bleeding (no agent will retrieve the content) without destroying evidence (admin can still review before deciding on deletion).

```python
def quarantine_memory(
    identity: Identity,
    memory_id: UUID,
    reason: str,
    incident_reference: str | None = None,
) -> QuarantineResult
```

**Authorization.** Identity must carry `memory:admin`.

**Audit.** A `quarantine` entry is written within the same transaction as the status update. Includes `state_before` (the prior status) and the supplied reason and incident reference.

**Transports.**
- MCP tool: `admin_quarantine`
- BFF route: `POST /api/admin/memories/{memory_id}/quarantine`
- Worker entry point: callable from a periodic content scanner that auto-quarantines on regex hit

### `hard_delete_memory`

Physically removes a memory row from PostgreSQL. This is irreversible. The deletion cascades to all associated data:

- The memory row itself (content, metadata, embeddings)
- All rows in `memory_relationships` where this memory is source or target
- All rows in `contradiction_reports` for this memory
- All version chain entries (previous versions linked via `previous_version_id`)
- The embedding vector (stored on the memory row; removed with the row)
- Any MinIO objects referenced by `content_ref` (if the memory uses document storage)

The audit entry is created *before* the delete operation, within the same database transaction. If the transaction fails, neither the audit entry nor the deletion is committed.

```python
def hard_delete_memory(
    identity: Identity,
    memory_id: UUID,
    reason: str,
    sanitized_audit: bool = False,
    incident_reference: str | None = None,
) -> DeletionResult
```

**Authorization.** Identity must carry `memory:admin`. Sanitized-audit deletions additionally require `memory:admin:sanitized_audit` to prevent routine admin sessions from suppressing audit content.

**Audit.** Two modes:

- **Normal mode** (default): the audit entry includes `state_before` with the full memory content, as with any other mutation. Use this for routine deletions where the content itself is not sensitive (e.g., removing test data, cleaning up duplicates).
- **Sanitized audit mode** (`sanitized_audit=True`): the audit entry contains only the memory ID, a SHA-256 hash of the content, the timestamp, the actor identity, and the incident reference. No content, no metadata, no embedding data. Use this for classified spill response where the audit log must not contain the sensitive content.

**Transports.**
- MCP tool: `admin_hard_delete`
- BFF route: `DELETE /api/admin/memories/{memory_id}` (with `sanitized_audit` and `incident_reference` in the request body)
- Worker entry point: called by the TTL pruner worker for expired memories (always normal-mode audit; the worker identity is not granted `memory:admin:sanitized_audit`)

### `bulk_delete_memories`

Atomic batch deletion from a set of memory IDs (typically the result of a `search_memory_admin` call). Applies the same cascade and audit behavior as `hard_delete_memory` to each memory in the set, within a single database transaction. All deletions succeed or none do -- there is no partial completion.

This function exists because spill response requires deleting all instances of the problematic content in one operation. Deleting them one at a time creates a window where some instances remain accessible while others have been removed.

```python
def bulk_delete_memories(
    identity: Identity,
    memory_ids: list[UUID],
    reason: str,
    sanitized_audit: bool = False,
    incident_reference: str | None = None,
) -> BulkDeletionResult
```

**Authorization.** Same as `hard_delete_memory`.

**Audit.** One audit entry per memory, all in the same transaction. The `incident_reference` is applied to every entry, making it possible to query the full set of deletions associated with a single incident.

**Transports.**
- MCP tool: `admin_bulk_delete`
- BFF route: `POST /api/admin/memories/bulk-delete`
- Worker entry point: called by the TTL pruner for batched expirations

## Audit Trail for Deletions

The [governance.md](../governance.md) audit trail schema supports deletion logging through its existing `state_before` field. Admin deletions extend this with two modes:

**Normal deletion audit entry:**

```json
{
  "operation": "hard_delete",
  "actor_id": "admin-agent-01",
  "actor_type": "service",
  "memory_id": "abc-123",
  "decision": "permitted",
  "state_before": { "content": "...", "scope": "user", "owner_id": "..." },
  "request_context": {
    "reason": "Duplicate cleanup",
    "incident_reference": null
  }
}
```

**Sanitized deletion audit entry (classified spill):**

```json
{
  "operation": "hard_delete",
  "actor_id": "admin-agent-01",
  "actor_type": "service",
  "memory_id": "abc-123",
  "decision": "permitted",
  "state_before": null,
  "request_context": {
    "sanitized_audit": true,
    "content_hash": "sha256:e3b0c44298fc1c149afb...",
    "reason": "Classified data spill response",
    "incident_reference": "INC-2026-0042"
  }
}
```

The content hash serves a specific purpose: after a spill cleanup, an automated check can hash incoming memory content against the stored hashes to detect reintroduction of the same data. This is a defense-in-depth measure, not a primary control.

## Dependencies

- **Core admin library structure**: `src/core/admin/` (operations, authorization, audit) does not yet exist and must be scaffolded as part of the first operation landed. See [README](README.md) for the current location and the future extraction plan.
- **#42 -- Memory deletion**: Provides the soft-delete infrastructure that `hard_delete_memory` builds on. The status enum (`active`, `quarantined`, `soft_deleted`) extends the soft-delete model.
- **RBAC `memory:admin` scope**: Defined in [governance.md](../governance.md). All operations in this document require this scope (some require additional sub-scopes).
- **Audit trail schema**: The `audit_log` table defined in [governance.md](../governance.md) supports the audit entries described here. The sanitized-audit mode is a new convention on top of the existing schema (using `state_before: null` and putting the content hash in `request_context`). The schema comment on `request_context` in governance.md should be updated to acknowledge admin operations.
- **Memory `status` column**: Not yet defined in [storage-layer.md](../storage-layer.md). The `status` enum described above (`active`, `quarantined`, `soft_deleted`) requires a schema migration that adds a `status` column to `memory_nodes` and updates all read queries to filter by `status = 'active'` for non-admin callers. This is in-scope for the same milestone as `quarantine_memory`.
- **`Identity` type**: A shared type passed into every core operation, capturing actor id, actor type (`user`, `service`, `worker`), tenant, and scopes. Defined as part of the core library scaffold.

## Decisions (resolved 2026-06-30)

These were open questions during the design phase. Resolved during backlog refinement.

1. **Admin search result TTL.** Session-scoped lifetime is sufficient. No server-side expiration or TTL enforcement. If a long-running admin session becomes a concern, address it operationally (session timeouts) rather than adding application complexity.

2. **Spill cleanup completeness verification.** Re-run the search after deletion; zero results means complete. No write-blocking for v1. The concurrent-write race is real but low-probability during an active spill response, and adding write-blocking introduces its own complexity and blast radius. Accept the race; document the "re-run search" verification step in the operator runbook.

3. **"Spill response" meta-operation.** No. Compose individual operations (`search_memory_admin` -> review -> `bulk_delete_memories`). A meta-operation adds complexity and is harder to test. Revisit if operator error during spill response becomes a pattern.

4. **External incident management integration.** Out of scope. Audit log entries are the integration surface. Downstream consumers (ServiceNow, PagerDuty) consume the audit log; the admin operations do not push to external systems.

5. **Embedding inversion risk.** Defense in depth. Delete embeddings with the row (they're stored on the row, so this is automatic). No special treatment beyond ensuring the row is physically removed. The threat is real in research but low-probability for short content with our embedding model.

6. **content_hash reintroduction detection indexing.** Separate `deletion_hashes` table, populated as a side effect of sanitized deletions. This isolates the access pattern from the audit log's append-only role and avoids GIN index overhead on `request_context`.

7. **Atomic audit-before-delete role isolation.** Stored procedure running as `SECURITY DEFINER` (option c). The procedure encapsulates the audit-insert-then-memory-delete sequence, runs with the privileges of the definer role (which has both INSERT on audit_log and DELETE on memory_nodes), and is callable by the application role. This preserves the audit_writer isolation for all non-admin paths while allowing the admin path to do both operations atomically.
