#!/usr/bin/env python3
"""Cluster retrieval benchmark -- real embeddings, real data.

Runs search queries against the production MemoryHub database using the
deployed embedding service. Compares vector-only vs hybrid (keyword+vector)
search to measure the improvement from #305.

Prerequisites:
- Port-forward to memoryhub-pg: oc port-forward statefulset/memoryhub-pg 25432:5432 --context mcp-rhoai -n memoryhub-db
- Embedding service accessible at the cluster route

Usage:
    python scripts/bench-cluster-retrieval.py
    python scripts/bench-cluster-retrieval.py --queries-file scripts/bench-queries.json
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memoryhub_core.services.memory import search_memories, search_memories_with_focus
from memoryhub_core.services.embeddings import HttpEmbeddingService
from memoryhub_core.services.rerank import HttpRerankerService

logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("MEMORYHUB_DB_HOST", "localhost")
DB_PORT = os.environ.get("MEMORYHUB_DB_PORT", "25432")
DB_USER = os.environ.get("MEMORYHUB_DB_USER", "memoryhub")
DB_PASS = os.environ.get("MEMORYHUB_DB_PASS", "d64c86093e57f4e94aa4740974e70ad3")
DB_NAME = os.environ.get("MEMORYHUB_DB_NAME", "memoryhub")

EMBEDDING_URL = os.environ.get(
    "MEMORYHUB_EMBEDDING_URL",
    "https://all-minilm-l6-v2-embedding-model.apps.cluster-n7pd5.n7pd5.sandbox5167.opentlc.com/embed",
)
RERANKER_URL = os.environ.get(
    "MEMORYHUB_RERANKER_URL",
    "https://ms-marco-minilm-l12-v2-reranker-model.apps.cluster-n7pd5.n7pd5.sandbox5167.opentlc.com",
)

# Queries designed to test keyword recall on real MemoryHub data.
# Mix of exact-match keywords (CLI commands, config keys) and semantic queries.
DEFAULT_QUERIES = [
    # Exact keyword matches -- should benefit from tsvector
    {"query": "parmesan cheese", "description": "exact preference recall"},
    {"query": "CORS_ALLOWED_ORIGINS", "description": "config key exact match"},
    {"query": "kubectl apply deployment.yaml", "description": "CLI command recall"},
    {"query": "register_session api_key", "description": "API function name"},
    {"query": "content_type experiential behavioral", "description": "enum values"},
    {"query": "pgvector cosine_distance", "description": "library function"},
    {"query": "alembic migration upgrade", "description": "tool command"},
    {"query": "FIPS compliance", "description": "acronym + term"},
    # Semantic queries -- vector search should handle well
    {"query": "how does authentication work", "description": "semantic auth"},
    {"query": "what decisions were made about the database", "description": "semantic db"},
    {"query": "user preferences and settings", "description": "semantic prefs"},
    {"query": "deployment architecture for the MCP server", "description": "semantic deploy"},
    {"query": "how to search for memories", "description": "semantic search"},
    {"query": "conversation thread persistence", "description": "semantic threads"},
    {"query": "agent memory governance and compliance", "description": "semantic governance"},
    {"query": "integration with external systems", "description": "semantic integration"},
]


@dataclass
class QueryResult:
    query: str
    description: str
    vector_latency_ms: float
    vector_results: int
    vector_top3: list[str]
    hybrid_latency_ms: float
    hybrid_results: int
    hybrid_top3: list[str]
    keyword_matches: int
    new_in_hybrid: int  # results in hybrid but not in vector top-10


@dataclass
class ClusterBenchResult:
    benchmark: str = "cluster-retrieval"
    timestamp: str = ""
    config: dict = field(default_factory=dict)
    memory_count: int = 0
    queries: list[QueryResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    total_seconds: float = 0.0


async def run_cluster_benchmark(
    query_list: list[dict] | None = None,
    db_url: str | None = None,
) -> ClusterBenchResult:
    if query_list is None:
        query_list = DEFAULT_QUERIES

    if db_url is None:
        db_url = (
            f"postgresql+asyncpg://{DB_USER}:{DB_PASS}"
            f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        )

    engine = create_async_engine(db_url, pool_size=5, max_overflow=10)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    embedding_service = HttpEmbeddingService(url=EMBEDDING_URL)
    reranker = HttpRerankerService(url=RERANKER_URL)

    result = ClusterBenchResult(
        timestamp=datetime.now(UTC).isoformat(),
        config={
            "embedding_url": EMBEDDING_URL,
            "reranker_url": RERANKER_URL,
            "db_host": DB_HOST,
        },
    )

    t_total = time.perf_counter()

    try:
        # Count memories
        from sqlalchemy import text
        async with session_factory() as session:
            row = await session.execute(
                text("SELECT count(*) FROM memory_nodes WHERE is_current = true AND deleted_at IS NULL")
            )
            result.memory_count = row.scalar()

        logger.info("Running against %d memories", result.memory_count)

        for q in query_list:
            query = q["query"]
            desc = q.get("description", "")

            # Vector-only search
            async with session_factory() as session:
                t0 = time.perf_counter()
                vector_results = await search_memories(
                    query=query,
                    session=session,
                    embedding_service=embedding_service,
                    tenant_id="default",
                    max_results=10,
                    weight_threshold=0.0,
                )
                vector_ms = (time.perf_counter() - t0) * 1000

            vector_ids = {str(item.id) for item, _ in vector_results}
            vector_stubs = [item.stub[:80] for item, _ in vector_results[:3]]

            # Hybrid search (keyword + vector + reranker)
            async with session_factory() as session:
                t0 = time.perf_counter()
                hybrid_bundle = await search_memories_with_focus(
                    query=query,
                    session=session,
                    embedding_service=embedding_service,
                    tenant_id="default",
                    focus_string=query,
                    reranker=reranker,
                    max_results=10,
                    weight_threshold=0.0,
                    keyword_boost_weight=0.15,
                )
                hybrid_ms = (time.perf_counter() - t0) * 1000

            hybrid_ids = {str(item.id) for item, _ in hybrid_bundle.results}
            hybrid_stubs = [item.stub[:80] for item, _ in hybrid_bundle.results[:3]]
            new_in_hybrid = len(hybrid_ids - vector_ids)

            qr = QueryResult(
                query=query,
                description=desc,
                vector_latency_ms=round(vector_ms, 1),
                vector_results=len(vector_results),
                vector_top3=vector_stubs,
                hybrid_latency_ms=round(hybrid_ms, 1),
                hybrid_results=len(hybrid_bundle.results),
                hybrid_top3=hybrid_stubs,
                keyword_matches=hybrid_bundle.keyword_matches,
                new_in_hybrid=new_in_hybrid,
            )
            result.queries.append(qr)
            logger.info(
                "%-40s vec=%5.0fms(%d) hyb=%5.0fms(%d) kw=%d new=%d",
                query[:40], vector_ms, len(vector_results),
                hybrid_ms, len(hybrid_bundle.results),
                hybrid_bundle.keyword_matches, new_in_hybrid,
            )

        # Summary stats
        vec_lats = [q.vector_latency_ms for q in result.queries]
        hyb_lats = [q.hybrid_latency_ms for q in result.queries]
        kw_matches = [q.keyword_matches for q in result.queries]
        new_results = [q.new_in_hybrid for q in result.queries]

        result.summary = {
            "vector_avg_latency_ms": round(sum(vec_lats) / len(vec_lats), 1),
            "hybrid_avg_latency_ms": round(sum(hyb_lats) / len(hyb_lats), 1),
            "avg_keyword_matches": round(sum(kw_matches) / len(kw_matches), 1),
            "queries_with_keyword_hits": sum(1 for k in kw_matches if k > 0),
            "queries_with_new_results": sum(1 for n in new_results if n > 0),
            "avg_new_results_per_query": round(sum(new_results) / len(new_results), 2),
        }

        result.total_seconds = time.perf_counter() - t_total

    finally:
        await engine.dispose()

    return result


def main():
    parser = argparse.ArgumentParser(description="Cluster retrieval benchmark")
    parser.add_argument("--queries-file", type=str, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    queries = None
    if args.queries_file:
        with open(args.queries_file) as f:
            queries = json.load(f)

    result = asyncio.run(run_cluster_benchmark(query_list=queries))

    print("\n" + "=" * 90)
    print("CLUSTER RETRIEVAL BENCHMARK -- VECTOR vs HYBRID")
    print(f"Memories: {result.memory_count}")
    print("=" * 90)
    print(f"{'Query':<42} {'Vec ms':>7} {'Hyb ms':>7} {'KW':>4} {'New':>4}")
    print("-" * 90)
    for q in result.queries:
        print(f"{q.query:<42} {q.vector_latency_ms:>7.0f} {q.hybrid_latency_ms:>7.0f} "
              f"{q.keyword_matches:>4} {q.new_in_hybrid:>4}")
    print("-" * 90)
    s = result.summary
    print(f"{'AVERAGE':<42} {s['vector_avg_latency_ms']:>7.0f} {s['hybrid_avg_latency_ms']:>7.0f} "
          f"{s['avg_keyword_matches']:>4.0f} {s['avg_new_results_per_query']:>4.1f}")
    print(f"\nQueries with keyword hits: {s['queries_with_keyword_hits']}/{len(result.queries)}")
    print(f"Queries with new results from keywords: {s['queries_with_new_results']}/{len(result.queries)}")
    print(f"Total time: {result.total_seconds:.1f}s")

    benchmarks_dir = Path(__file__).resolve().parent.parent / "benchmarks"
    benchmarks_dir.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    out_path = benchmarks_dir / f"cluster-retrieval-{date_str}.json"
    with open(out_path, "w") as f:
        json.dump(asdict(result), f, indent=2)
    print(f"\nResults written to: {out_path}")


if __name__ == "__main__":
    main()
