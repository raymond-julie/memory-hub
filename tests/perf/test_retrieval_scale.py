"""Minimal test to verify the retrieval-scale benchmark harness doesn't crash.

Runs the smallest corpus size (100) with 1 run to validate the harness
structure and metric computation. Full sweeps run via the standalone script.
"""

import pytest

from tests.perf.retrieval_scale_bench import (
    BENCHMARK_QUERIES,
    MockEmbeddingForBench,
    generate_corpus,
)


def test_corpus_generation_produces_correct_count():
    for scale in [100, 1000]:
        corpus = generate_corpus(scale)
        assert len(corpus) == scale, f"Expected {scale}, got {len(corpus)}"


def test_corpus_has_topic_labels():
    corpus = generate_corpus(100)
    topics = {m["topic"] for m in corpus}
    assert len(topics) == 8


def test_corpus_ids_are_unique():
    corpus = generate_corpus(1000)
    ids = [m["id"] for m in corpus]
    assert len(ids) == len(set(ids))


def test_benchmark_queries_have_relevant_topics():
    valid_topics = {m["topic"] for m in generate_corpus(100)}
    for q in BENCHMARK_QUERIES:
        assert q["relevant_topic"] in valid_topics, (
            f"Query topic {q['relevant_topic']} not in corpus topics"
        )


@pytest.mark.asyncio
async def test_mock_embedding_deterministic():
    emb = MockEmbeddingForBench()
    v1 = await emb.embed("hello world")
    v2 = await emb.embed("hello world")
    assert v1 == v2
    assert len(v1) == 384

    v3 = await emb.embed("different text")
    assert v3 != v1
