# System Benchmarks Framework

**Status:** Design (for #271, #272, #273, #274)
**Date:** 2026-06-30
**Builds on:** `tests/perf/two_vector_bench.py` (existing retrieval benchmark)

---

## 1. Scope

Four system benchmarks that measure MemoryHub infrastructure performance. These are *not* agent-level evaluation (that's #275, deferred to its own research track). Each benchmark produces reproducible numbers, commits results to git, and has a deterministic exit predicate suitable for autonomous loop execution.

| Issue | Benchmark | Measures |
|-------|-----------|----------|
| #271 | Retrieval at scale | pgvector search latency + relevance across tenant sizes |
| #274 | Cross-encoder cost/benefit | Re-ranking latency, NDCG/MRR delta, optimal candidate set size |
| #272 | Entity extraction throughput | 3-stage cascade (spaCy/GLiNER2/LLM) latency + accuracy |
| #273 | Graph vs flat retrieval | Relevance delta when using entity relationships for retrieval |

## 2. Existing Infrastructure

The two-vector retrieval benchmark (`tests/perf/`) establishes the pattern:

- **Fixtures:** `tests/perf/fixtures/` -- synthetic memories and queries with ground-truth topic labels
- **Engine:** `tests/perf/two_vector_bench.py` -- pipeline implementations, metric functions, sweep runner
- **Script:** `scripts/bench-two-vector.py` -- standalone CLI that calls the engine, prints tables, writes JSON
- **Results:** `benchmarks/*.json` -- timestamped result files committed to git
- **Test integration:** `pytest -m perf` -- opt-in marker via `tests/perf/conftest.py`
- **Metrics:** recall@k, precision@k, MRR, with aggregation by (pipeline, condition, weight)

This is a good pattern. The new benchmarks should follow it rather than introducing new tooling.

## 3. Shared Conventions (Not a Framework)

After reviewing the existing benchmark, a shared base class would be premature abstraction. The four benchmarks differ enough in what they measure that a common `BenchmarkRunner` would either be too generic to be useful or would force awkward inheritance. Instead, enforce conventions:

### Directory structure

```
tests/perf/
  conftest.py                          # existing -- auto-marks as 'perf'
  two_vector_bench.py                  # existing
  test_two_vector_retrieval.py         # existing
  fixtures/                            # existing
    queries.py
    topics/
  retrieval_scale_bench.py             # #271
  test_retrieval_scale.py              # #271
  cross_encoder_bench.py               # #274
  test_cross_encoder.py                # #274
  entity_extraction_bench.py           # #272
  test_entity_extraction.py            # #272
  graph_retrieval_bench.py             # #273
  test_graph_retrieval.py              # #273
  fixtures/
    scale_corpus/                      # #271 -- generated at seed time
    labeled_entities/                  # #272 -- manually labeled
    graph_queries/                     # #273 -- queries with expected results

scripts/
  bench-two-vector.py                  # existing
  bench-retrieval-scale.py             # #271
  bench-cross-encoder.py               # #274
  bench-entity-extraction.py           # #272
  bench-graph-retrieval.py             # #273

benchmarks/
  two-vector-retrieval-*.json          # existing
  retrieval-scale-*.json               # #271
  cross-encoder-*.json                 # #274
  entity-extraction-*.json             # #272
  graph-retrieval-*.json               # #273
```

### Result file format

Every benchmark writes a JSON file to `benchmarks/` with this envelope:

```json
{
  "benchmark": "retrieval-scale",
  "timestamp": "2026-07-01T14:30:00Z",
  "config": {
    "corpus_sizes": [100, 1000, 10000],
    "embedding_url": "https://...",
    "hardware": "auto-detected or manually noted"
  },
  "results": { },
  "timing": {
    "total_seconds": 120.5,
    "phase_breakdown": {}
  }
}
```

The `results` shape is benchmark-specific. The envelope is just timestamp + config + timing so results are self-documenting.

### Metric functions

The existing `recall_at_k`, `precision_at_k`, `mrr` functions in `two_vector_bench.py` should be extracted to a shared `tests/perf/metrics.py` module so all benchmarks can reuse them. Add NDCG as well (needed for #274). This is the only shared code.

### pytest integration

Each benchmark gets a test file that runs a minimal subset (smallest corpus, fewest iterations) to verify the benchmark doesn't crash. The full sweep runs via the standalone script. All tests auto-inherit the `perf` marker from `conftest.py`.

## 4. Per-Benchmark Design

### #271: Retrieval at Scale

**What it measures:** pgvector search latency and relevance as tenant memory count grows.

**Corpus generation:** Seed synthetic memories at 100, 1K, and 10K per tenant. Use the existing topic-based fixture pattern but scale it. Memories should have realistic content length and embedding diversity (not just copies of the same 200 memories).

**Metrics:**
- Search latency: p50, p95, p99 at each scale
- Relevance: recall@10, precision@10, MRR against topic-labeled ground truth
- Embedding throughput: memories/second during corpus seeding
- Connection pool behavior: concurrent search latency under 1/5/10 parallel queries

**Target environment:** Deployed PostgreSQL + pgvector on the mcp-rhoai cluster. Not a local SQLite test -- the point is to measure real infrastructure.

**Exit predicate for loop:** Results JSON exists for all three scale tiers, latency numbers are within 10% across 3 consecutive runs, report committed.

### #274: Cross-Encoder Cost/Benefit

**What it measures:** Whether the cross-encoder re-ranking step is worth its cost.

**Extends:** The existing two-vector benchmark already measures cross-encoder impact (NEW-1/NEW-3 vs baseline). This benchmark varies the *candidate set size* (10, 25, 50, 100) to find the diminishing returns threshold.

**Metrics:**
- Re-ranking latency per call at each candidate set size
- NDCG and MRR delta: vector-only vs vector+reranker
- Resource consumption: CPU/memory per rerank call (from pod metrics)
- Recommendation: optimal candidate set size for production

**Target environment:** Deployed reranker service on mcp-rhoai.

**Relationship to #100:** #100 asks to re-benchmark the cross-encoder on real production memories. #274 is the superset -- fold #100 into #274 by running against both synthetic and production data.

**Exit predicate for loop:** Results JSON exists, optimal candidate set size identified, recommendation documented in results file.

### #272: Entity Extraction Throughput

**What it measures:** The 3-stage extraction cascade (spaCy -> GLiNER2 -> LLM fallback) performance.

**Labeled evaluation set:** Manually label 50-100 memories with ground-truth entities (person, organization, technology, location). Store in `tests/perf/fixtures/labeled_entities/`. This is the one manual judgment step; the rest is mechanical.

**Metrics:**
- Per-stage latency: spaCy, GLiNER2, LLM fallback
- End-to-end extraction time per memory
- Backfill rate: memories/minute
- Accuracy: precision/recall per entity type against labeled set
- Stage promotion rates: what % need Stage 2? Stage 3?
- Resource consumption: CPU, memory, GPU

**Target environment:** Local (extraction runs in-process with local models, no cluster dependency).

**Exit predicate for loop:** Labeled set exists, benchmark runs clean, per-stage breakdown committed, accuracy numbers stable.

### #273: Graph vs Flat Retrieval

**What it measures:** Whether following entity relationships surfaces better results than vector similarity alone.

**Prerequisites:** Entity extraction must be running (it is -- shipped in the June extraction sprint). The relationship model must have enough data to test traversal. This may need a seeded graph corpus.

**Evaluation approach:**
1. Build a test corpus of 50+ queries with manually labeled "expected relevant memories"
2. For each query, compare: (a) flat vector search results, (b) graph-augmented results (follow entity edges from top vector hits to find related memories)
3. Measure relevance delta, latency cost, and result diversity

**Metrics:**
- Recall@10, precision@10, MRR for flat vs graph-augmented
- Latency cost of graph traversal (extra DB queries)
- Result diversity: Jaccard distance between flat and graph result sets
- Qualitative: are the extra results actually useful, or just noise?

**Judgment required:** The labeled query set is the hardest part. The queries need to be designed so that graph relationships *could* help (e.g., "what do we know about project X?" where some memories mention project X by name and others are linked via entity relationships but don't mention it in text).

**Exit predicate for loop:** Labeled query set exists, both retrieval paths benchmarked, comparison report committed, clear recommendation (pursue/skip/hybrid) documented.

## 5. Loop Execution Plan

Each benchmark is an autonomous loop target with this structure:

```
1. Check prerequisites (deployed services, fixtures exist)
2. Generate/seed test corpus if needed
3. Run benchmark sweep
4. Collect metrics into JSON
5. Verify stability (re-run, compare to prior run)
6. Commit results to benchmarks/
7. Exit: results file exists, numbers stable, report committed
```

### Execution order

1. **#272 (entity extraction)** -- runs locally, no cluster dependency, fast feedback
2. **#274 (cross-encoder)** -- extends existing benchmark, most infrastructure already in place
3. **#271 (retrieval at scale)** -- needs corpus generation, more setup
4. **#273 (graph vs flat)** -- most judgment needed (labeled query set), last

### Manual steps required before loops can run

| Benchmark | Manual step | Effort |
|-----------|-------------|--------|
| #272 | Label 50-100 memories with ground-truth entities | 2-3 hours |
| #273 | Design and label 50+ queries with expected results | 3-4 hours |
| #271 | None (corpus is generated programmatically) | -- |
| #274 | None (extends existing benchmark) | -- |

#274 and #271 are fully automatable. #272 and #273 need labeled data first.

## 6. Folding #100

Issue #100 (re-benchmark NEW-1 cross-encoder on real production memories) should be closed as subsumed by #274. Add a comment on #100 pointing to #274 and close it.
