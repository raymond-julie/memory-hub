# Storage Architecture FAQ

Common questions from colleagues and stakeholders about MemoryHub's storage architecture choices.

## Why PostgreSQL + pgvector?

MemoryHub's storage needs are governed assertions with provenance chains, vector similarity search, scope-isolated access control, tenant isolation, and temporal versioning. PostgreSQL handles all of these natively:

- **Vector similarity search**: pgvector provides HNSW indexes for cosine distance queries over 384-dimensional embeddings. No separate vector database needed.
- **Graph traversal**: Memory trees are shallow by design (2-3 hops of depth). PostgreSQL recursive CTEs handle this without a graph database. Phase 1 graph-enhanced retrieval is shipped and performing in production.
- **Scope isolation**: SQL WHERE clauses enforce RBAC at the query level. Every query is filtered by tenant, scope, and authorization claims. No path-traversal ambiguity.
- **Temporal versioning**: Version chains, soft-delete, and temporal validity on relationship edges are all standard relational patterns.
- **Operational simplicity**: PostgreSQL ships with OpenShift. No additional product to license, deploy, monitor, or patch.

## Why not Neo4j?

Neo4j is an excellent graph database. MemoryHub doesn't use it for three reasons: operational dependency, deployment story, and fitness for purpose.

**Operational dependency.** PostgreSQL ships with OpenShift out of the box. Neo4j is an additional product that enterprises must license (Enterprise Edition for production), deploy as a separate cluster, monitor, back up, and maintain HA for. For regulated enterprises -- MemoryHub's target market -- every dependency is a compliance surface. Adding Neo4j doubles the database operations burden for a capability PostgreSQL already provides.

**Deployment story.** MemoryHub deploys with a single script (`deploy-full.sh`) that stands up everything it needs. Adding Neo4j means a second database cluster, a second backup strategy, a second HA configuration, a second set of credentials to manage. The golden test -- "uninstall and redeploy with zero manual steps" -- gets significantly harder with two database products.

**Fitness for purpose.** Graph databases shine at unknown-depth recursive traversal: supply chains, social networks, fraud detection paths. MemoryHub's memory trees have bounded, shallow depth. The access patterns that matter for agent memory -- scope-filtered vector search, tenant-isolated reads, provenance chain traversal with known depth -- are relational patterns, not graph patterns.

The security model is particularly instructive. In a graph database, if there's a traversal path from A to C through D but the user can't see D, the behavior is non-monotonic and role-dependent. In MemoryHub, scope isolation is enforced at the SQL level: you don't see nodes outside your authorized scopes. No path-traversal ambiguity, no information leakage through graph structure.

Neo4j Agent Memory (neo4j-labs/agent-memory) is a strong project that provides richer graph traversal and a mature entity extraction pipeline. It focuses on different priorities than MemoryHub: traversal expressiveness and framework integrations over scope hierarchy, RBAC, governed compaction, and compliance. Both are valid design centers -- they serve different deployment contexts.

## Do we need a graph database?

It's a fair question. The reasoning usually goes: ontologies are graphs, agent memory involves relationships, therefore you need a graph database. Sakhatsky (April 2026) offers a useful counterpoint -- each step in that chain is weaker than it looks, and it's worth examining why.

MemoryHub is a context graph, not a knowledge graph (see `research/knowledge-graphs-vs-context-graphs.md`). Context graphs capture decisions, experiences, and institutional memory -- the "why" and "how" of organizational operations. Knowledge graphs capture domain ontologies -- entities, taxonomies, and static relationships. These are different concerns with different access patterns.

Context graph access patterns:

- "Find memories similar to this query within my authorized scopes" -- vector search with SQL filters
- "What was the rationale behind this decision?" -- parent-child traversal, 1-2 hops
- "How has this knowledge evolved?" -- version chain traversal, linear
- "What contradicts this assertion?" -- embedding similarity within scope, SQL query

None of these require unbounded graph traversal. All of them require scope isolation, tenant filtering, and vector search -- PostgreSQL's strengths.

MemoryHub does have a deferred decision point (Phase 3 of graph-enhanced memory design). If production observability shows we need:

- Depth > 3 hops in production queries
- Community detection or centrality algorithms
- Graph neural network workloads
- p95 latency > 200ms on graph traversal

...then we evaluate Apache AGE (PostgreSQL extension for Cypher), NetworkX (in-memory for analytics), or Neo4j as a dedicated analytics sidecar. We'd rather make that decision based on observed production access patterns than predict it upfront.

## Could I plug in my own storage backend?

Not today, but the architecture doesn't prevent it.

The storage layer sits behind a service abstraction (`services/memory.py`, `services/database.py`). An adapter pattern that swaps PostgreSQL for another backend is architecturally feasible. MemoryHub doesn't ship a pluggable interface today because:

**The governance substrate is tightly coupled to SQL.** Scope isolation, tenant filtering, curation pipeline queries, similarity search, and temporal versioning are all SQL-level operations. Abstracting them into a storage-agnostic interface is a significant engineering investment with unclear demand.

**Pluggability is a framework concern, not a service concern.** MemoryHub is a deployed service, not a library. Agents talk to it over MCP. They don't care what database sits behind the API. The question "can I plug in my own storage?" is really "can I deploy MemoryHub on my preferred database?" -- which is a deployment option, not a plugin architecture.

**For teams with existing Neo4j investment.** If your organization already operates Neo4j and wants MemoryHub's governance model on top of it, the natural path is a Neo4j storage adapter behind the existing service interface. PostgreSQL remains the zero-dependency default; Neo4j would be an opt-in for teams that already have the operational expertise. This is tracked as a future consideration (see issue backlog).

## What about Apache AGE?

Apache AGE is a PostgreSQL extension that adds Cypher query support. It's the most natural evolution path if MemoryHub's graph needs outgrow recursive CTEs, because it doesn't introduce a new database product -- it extends the one we already run. AGE is under evaluation as part of the Phase 3 graph-enhanced memory decision, but only if production data shows the need. As of April 2026, AGE is still in the Apache Incubator, which means its stability and index management story are not yet production-grade for our purposes.

## What about pluggable embedding models?

The embedding model (currently sentence-transformers/all-MiniLM-L6-v2, 384 dimensions) is already configurable via the embedding service. Changing models requires re-embedding existing memories (a migration task), but the architecture supports it. The vector dimension is defined in one place and propagated through the schema.

## Summary

| Question | Short answer |
|---|---|
| Why PostgreSQL? | Handles all access patterns natively, ships with OpenShift, zero additional dependencies |
| Why not Neo4j? | Additional operational dependency; our access patterns (shallow traversal, scope-filtered search) are well-served by PostgreSQL |
| Is it a graph database? | No. It's a governed memory service that uses graph structures (trees, edges) on PostgreSQL |
| Can I plug in Neo4j? | Not today. Architecturally feasible as a contributed adapter. Tracked as future consideration |
| What if you outgrow PostgreSQL? | Phase 3 decision point: Apache AGE (Cypher on PostgreSQL) or Neo4j analytics sidecar, driven by production data |

## Sources

- Michael Sakhatsky, "You Probably Don't Need a Graph Database for Your Knowledge Graph" (April 2026)
- Gartner, "Context Graphs" research (March 2026)
- MemoryHub internal: `research/knowledge-graphs-vs-context-graphs.md`, `research/agent-memory-landscape-2026.md`, `docs/graph-enhanced-memory.md`
