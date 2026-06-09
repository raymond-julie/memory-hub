"""Tests for the entity extraction pipeline."""

import uuid
from collections import namedtuple
from unittest.mock import patch

import pytest

from memoryhub_core.services.embeddings import MockEmbeddingService
from memoryhub_core.services.extraction import (
    _merge_entities,
    _should_run_stage2,
    _tag_extractor,
    extract_entities_from_memory,
    run_gliner_ner,
    run_spacy_ner,
)

# ── spaCy mock helpers ──────────────────────────────────────────────────────

SpacyEntity = namedtuple("SpacyEntity", ["text", "label_", "start_char", "end_char"])


class FakeDoc:
    def __init__(self, ents):
        self.ents = ents


class FakeNlp:
    def __init__(self, ents=None):
        self._ents = ents or []

    def __call__(self, text):
        return FakeDoc(self._ents)


# ── run_spacy_ner tests ─────────────────────────────────────────────────────


def test_run_spacy_ner_extracts_entities():
    fake = FakeNlp([
        SpacyEntity("Alice", "PERSON", 0, 5),
        SpacyEntity("New York", "GPE", 13, 21),
    ])
    with patch("memoryhub_core.services.extraction._get_nlp", return_value=fake):
        result = run_spacy_ner("Alice went to New York.")

    assert len(result) == 2
    assert result[0] == {"name": "Alice", "type": "person", "label": "PERSON", "start": 0, "end": 5, "confidence": 1.0}
    assert result[1] == {
        "name": "New York", "type": "location", "label": "GPE", "start": 13, "end": 21, "confidence": 1.0,
    }


def test_run_spacy_ner_empty_text():
    with patch("memoryhub_core.services.extraction._get_nlp", return_value=FakeNlp()):
        assert run_spacy_ner("") == []
        assert run_spacy_ner("   ") == []


def test_run_spacy_ner_no_recognized_entities():
    fake = FakeNlp([SpacyEntity("42", "CARDINAL", 0, 2)])
    with patch("memoryhub_core.services.extraction._get_nlp", return_value=fake):
        result = run_spacy_ner("The answer is 42.")
    assert result == []


def test_run_spacy_ner_deduplicates_within_text():
    fake = FakeNlp([
        SpacyEntity("Alice", "PERSON", 0, 5),
        SpacyEntity("Alice", "PERSON", 20, 25),
    ])
    with patch("memoryhub_core.services.extraction._get_nlp", return_value=fake):
        result = run_spacy_ner("Alice met someone. Alice left.")
    assert len(result) == 1


def test_run_spacy_ner_maps_all_supported_labels():
    ents = [
        SpacyEntity("Bob", "PERSON", 0, 3),
        SpacyEntity("Acme", "ORG", 4, 8),
        SpacyEntity("Paris", "GPE", 9, 14),
        SpacyEntity("Central Park", "LOC", 15, 27),
        SpacyEntity("the Tower", "FAC", 28, 37),
        SpacyEntity("the summit", "EVENT", 38, 48),
    ]
    fake = FakeNlp(ents)
    with patch("memoryhub_core.services.extraction._get_nlp", return_value=fake):
        result = run_spacy_ner("Bob Acme Paris Central Park the Tower the summit")

    types = [e["type"] for e in result]
    assert types == ["person", "organization", "location", "location", "location", "event"]


# ── extract_entities_from_memory tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_entities_creates_nodes_and_edges(async_session):
    """Full pipeline: spaCy -> entity nodes -> MENTIONS edges."""
    embedding_service = MockEmbeddingService()
    memory_id = uuid.uuid4()
    tenant_id = "test-tenant"
    owner_id = "test-user"

    # Create a source memory node to reference
    from datetime import UTC, datetime

    from memoryhub_core.models.memory import MemoryNode

    now = datetime.now(UTC)
    source = MemoryNode(
        id=memory_id,
        content="Alice from Acme Corp visited New York for the AI Summit.",
        stub="Alice from Acme Corp...",
        scope="user",
        weight=0.9,
        owner_id=owner_id,
        tenant_id=tenant_id,
        is_current=True,
        version=1,
        storage_type="inline",
        created_at=now,
        updated_at=now,
    )
    async_session.add(source)
    await async_session.commit()

    fake = FakeNlp([
        SpacyEntity("Alice", "PERSON", 0, 5),
        SpacyEntity("Acme Corp", "ORG", 11, 20),
        SpacyEntity("New York", "GPE", 29, 37),
        SpacyEntity("AI Summit", "EVENT", 46, 55),
    ])

    with patch("memoryhub_core.services.extraction._get_nlp", return_value=fake):
        result = await extract_entities_from_memory(
            memory_id=memory_id,
            content=source.content,
            session=async_session,
            embedding_service=embedding_service,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )

    assert result["count"] == 4
    assert len(result["entities"]) == 4
    entity_names = {e["name"] for e in result["entities"]}
    assert entity_names == {"Alice", "Acme Corp", "New York", "AI Summit"}

    # Verify entity nodes were created in the database
    from sqlalchemy import select

    stmt = select(MemoryNode).where(MemoryNode.scope == "entity")
    entities = (await async_session.execute(stmt)).scalars().all()
    assert len(entities) == 4

    # Check entity node properties
    alice = next(e for e in entities if e.content == "Alice")
    assert alice.branch_type == "entity:person"
    assert alice.tenant_id == tenant_id
    assert alice.owner_id == owner_id
    assert alice.metadata_["extracted_by"] == "spacy"

    # Verify MENTIONS relationships
    from memoryhub_core.models.memory import MemoryRelationship

    rel_stmt = select(MemoryRelationship).where(
        MemoryRelationship.source_id == memory_id,
        MemoryRelationship.relationship_type == "mentions",
    )
    rels = (await async_session.execute(rel_stmt)).scalars().all()
    assert len(rels) == 4


@pytest.mark.asyncio
async def test_extract_entities_deduplicates_across_memories(async_session):
    """Two memories mentioning 'Alice' should share one entity node."""
    embedding_service = MockEmbeddingService()
    tenant_id = "test-tenant"
    owner_id = "test-user"

    from datetime import UTC, datetime

    from sqlalchemy import select

    from memoryhub_core.models.memory import MemoryNode, MemoryRelationship

    now = datetime.now(UTC)
    mem_ids = [uuid.uuid4(), uuid.uuid4()]
    for mid in mem_ids:
        async_session.add(MemoryNode(
            id=mid,
            content="Alice did something",
            stub="Alice did something...",
            scope="user",
            weight=0.9,
            owner_id=owner_id,
            tenant_id=tenant_id,
            is_current=True,
            version=1,
            storage_type="inline",
            created_at=now,
            updated_at=now,
        ))
    await async_session.commit()

    fake = FakeNlp([SpacyEntity("Alice", "PERSON", 0, 5)])

    with patch("memoryhub_core.services.extraction._get_nlp", return_value=fake):
        for mid in mem_ids:
            await extract_entities_from_memory(
                memory_id=mid,
                content="Alice did something",
                session=async_session,
                embedding_service=embedding_service,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )

    # Only one entity node for "Alice"
    entity_stmt = select(MemoryNode).where(MemoryNode.scope == "entity")
    entities = (await async_session.execute(entity_stmt)).scalars().all()
    assert len(entities) == 1

    # Two MENTIONS edges pointing to the same entity
    rel_stmt = select(MemoryRelationship).where(
        MemoryRelationship.relationship_type == "mentions",
    )
    rels = (await async_session.execute(rel_stmt)).scalars().all()
    assert len(rels) == 2
    assert rels[0].target_id == rels[1].target_id


@pytest.mark.asyncio
async def test_extract_entities_handles_no_entities(async_session):
    """Content with no recognized entities returns count=0."""
    embedding_service = MockEmbeddingService()
    memory_id = uuid.uuid4()

    from datetime import UTC, datetime

    from memoryhub_core.models.memory import MemoryNode

    now = datetime.now(UTC)
    async_session.add(MemoryNode(
        id=memory_id,
        content="The weather is nice today.",
        stub="The weather...",
        scope="user",
        weight=0.9,
        owner_id="test-user",
        tenant_id="test-tenant",
        is_current=True,
        version=1,
        storage_type="inline",
        created_at=now,
        updated_at=now,
    ))
    await async_session.commit()

    fake = FakeNlp([])

    with patch("memoryhub_core.services.extraction._get_nlp", return_value=fake):
        result = await extract_entities_from_memory(
            memory_id=memory_id,
            content="The weather is nice today.",
            session=async_session,
            embedding_service=embedding_service,
            tenant_id="test-tenant",
            owner_id="test-user",
        )

    assert result["count"] == 0
    assert result["entities"] == []


@pytest.mark.asyncio
async def test_extract_entities_survives_individual_failure(async_session):
    """If one entity fails, the others still get extracted."""
    embedding_service = MockEmbeddingService()
    memory_id = uuid.uuid4()

    from datetime import UTC, datetime

    from memoryhub_core.models.memory import MemoryNode

    now = datetime.now(UTC)
    async_session.add(MemoryNode(
        id=memory_id,
        content="Alice went to New York.",
        stub="Alice went...",
        scope="user",
        weight=0.9,
        owner_id="test-user",
        tenant_id="test-tenant",
        is_current=True,
        version=1,
        storage_type="inline",
        created_at=now,
        updated_at=now,
    ))
    await async_session.commit()

    # First entity will succeed, second will have an invalid type triggering ValueError
    fake = FakeNlp([
        SpacyEntity("Alice", "PERSON", 0, 5),
        SpacyEntity("New York", "GPE", 14, 22),
    ])

    from memoryhub_core.services.entity import find_or_create_entity as orig_find

    call_count = 0

    async def flaky_find(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated failure")
        return await orig_find(*args, **kwargs)

    with (
        patch("memoryhub_core.services.extraction._get_nlp", return_value=fake),
        patch("memoryhub_core.services.extraction.find_or_create_entity", side_effect=flaky_find),
    ):
        result = await extract_entities_from_memory(
            memory_id=memory_id,
            content="Alice went to New York.",
            session=async_session,
            embedding_service=embedding_service,
            tenant_id="test-tenant",
            owner_id="test-user",
        )

    # One succeeded, one failed
    assert result["count"] == 1
    assert result["entities"][0]["name"] == "Alice"


# ── GLiNER Stage 2 tests ──────────────────────────────────────────────────


class FakeGLiNERModel:
    """Deterministic GLiNER model replacement for unit tests."""

    def __init__(self, entities=None):
        self._entities = entities or []

    def predict_entities(self, text, labels, threshold=0.5):
        return self._entities


def test_run_gliner_ner_extracts_entities():
    fake_model = FakeGLiNERModel([
        {"text": "PostgreSQL", "label": "database", "score": 0.92, "start": 10, "end": 20},
        {"text": "FastAPI", "label": "framework", "score": 0.88, "start": 30, "end": 37},
    ])
    with patch("memoryhub_core.services.extraction._get_gliner", return_value=fake_model):
        result = run_gliner_ner("Deployed PostgreSQL with FastAPI on OpenShift.")

    assert len(result) == 2
    assert result[0]["name"] == "PostgreSQL"
    assert result[0]["type"] == "object"
    assert result[0]["label"] == "database"
    assert result[0]["confidence"] == 0.92
    assert result[1]["name"] == "FastAPI"
    assert result[1]["type"] == "object"


def test_run_gliner_ner_empty_text():
    assert run_gliner_ner("") == []
    assert run_gliner_ner("   ") == []


def test_run_gliner_ner_whitespace_entity_filtered():
    fake_model = FakeGLiNERModel([
        {"text": "  ", "label": "technology", "score": 0.85, "start": 0, "end": 2},
        {"text": "Python", "label": "programming language", "score": 0.95, "start": 5, "end": 11},
    ])
    with patch("memoryhub_core.services.extraction._get_gliner", return_value=fake_model):
        result = run_gliner_ner("  Python is great.")

    assert len(result) == 1
    assert result[0]["name"] == "Python"


def test_run_gliner_ner_deduplicates():
    fake_model = FakeGLiNERModel([
        {"text": "Python", "label": "programming language", "score": 0.95, "start": 0, "end": 6},
        {"text": "Python", "label": "programming language", "score": 0.90, "start": 20, "end": 26},
    ])
    with patch("memoryhub_core.services.extraction._get_gliner", return_value=fake_model):
        result = run_gliner_ner("Python is great. Python is fast.")

    assert len(result) == 1
    assert result[0]["confidence"] == 0.95


def test_run_gliner_ner_maps_labels_to_pole():
    fake_model = FakeGLiNERModel([
        {"text": "Alice", "label": "person", "score": 0.99, "start": 0, "end": 5},
        {"text": "Kubernetes", "label": "technology", "score": 0.91, "start": 10, "end": 20},
        {"text": "TCP", "label": "protocol", "score": 0.85, "start": 25, "end": 28},
        {"text": "AI Summit", "label": "event", "score": 0.87, "start": 33, "end": 42},
    ])
    with patch("memoryhub_core.services.extraction._get_gliner", return_value=fake_model):
        result = run_gliner_ner("Alice uses Kubernetes over TCP at AI Summit")

    types = {e["name"]: e["type"] for e in result}
    assert types["Alice"] == "person"
    assert types["Kubernetes"] == "object"
    assert types["TCP"] == "object"
    assert types["AI Summit"] == "event"


# ── Cascade logic tests ───────────────────────────────────────────────────


def test_should_run_stage2_with_no_entities():
    assert _should_run_stage2([]) is True


def test_should_run_stage2_with_low_coverage():
    entities = [
        {"name": "Alice", "type": "person", "confidence": 1.0},
    ]
    assert _should_run_stage2(entities) is True


def test_should_run_stage2_skipped_with_sufficient_coverage():
    entities = [
        {"name": "Alice", "type": "person", "confidence": 1.0},
        {"name": "Acme", "type": "organization", "confidence": 1.0},
    ]
    assert _should_run_stage2(entities) is False


def test_should_run_stage2_low_confidence_doesnt_count():
    entities = [
        {"name": "Alice", "type": "person", "confidence": 1.0},
        {"name": "maybe-entity", "type": "person", "confidence": 0.5},
    ]
    assert _should_run_stage2(entities) is True


def test_tag_extractor():
    entities = [{"name": "Alice"}, {"name": "Bob"}]
    result = _tag_extractor(entities, "spacy")
    assert all(e["extractor"] == "spacy" for e in result)


def test_merge_entities_drops_duplicates():
    stage1 = [
        {"name": "Alice", "type": "person", "label": "PERSON", "confidence": 1.0, "start": 0, "end": 5},
    ]
    stage2 = [
        {"name": "alice", "type": "person", "label": "person", "confidence": 0.95, "start": 0, "end": 5},
        {"name": "PostgreSQL", "type": "object", "label": "database", "confidence": 0.92, "start": 10, "end": 20},
    ]
    merged = _merge_entities(stage1, stage2)
    assert len(merged) == 2
    names = [e["name"] for e in merged]
    assert "Alice" in names
    assert "PostgreSQL" in names


# ── Full cascade integration tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_runs_stage2_when_spacy_coverage_low(async_session):
    """When spaCy finds < 2 high-confidence entities, GLiNER Stage 2 runs."""
    embedding_service = MockEmbeddingService()
    memory_id = uuid.uuid4()

    from datetime import UTC, datetime

    from memoryhub_core.models.memory import MemoryNode

    now = datetime.now(UTC)
    async_session.add(MemoryNode(
        id=memory_id,
        content="Deployed PostgreSQL 16 with pgvector on OpenShift.",
        stub="Deployed PostgreSQL...",
        scope="user",
        weight=0.9,
        owner_id="test-user",
        tenant_id="test-tenant",
        is_current=True,
        version=1,
        storage_type="inline",
        created_at=now,
        updated_at=now,
    ))
    await async_session.commit()

    # Stage 1: spaCy finds nothing (no person/org/location/event)
    fake_nlp = FakeNlp([])
    # Stage 2: GLiNER finds technologies
    fake_gliner = FakeGLiNERModel([
        {"text": "PostgreSQL", "label": "database", "score": 0.92, "start": 9, "end": 19},
        {"text": "pgvector", "label": "technology", "score": 0.88, "start": 25, "end": 33},
        {"text": "OpenShift", "label": "technology", "score": 0.90, "start": 37, "end": 46},
    ])

    with (
        patch("memoryhub_core.services.extraction._get_nlp", return_value=fake_nlp),
        patch("memoryhub_core.services.extraction._get_gliner", return_value=fake_gliner),
    ):
        result = await extract_entities_from_memory(
            memory_id=memory_id,
            content="Deployed PostgreSQL 16 with pgvector on OpenShift.",
            session=async_session,
            embedding_service=embedding_service,
            tenant_id="test-tenant",
            owner_id="test-user",
        )

    assert result["count"] == 3
    extractors = {e["extractor"] for e in result["entities"]}
    assert "gliner" in extractors


@pytest.mark.asyncio
async def test_cascade_skips_stage2_when_spacy_coverage_sufficient(async_session):
    """When spaCy finds >= 2 high-confidence entities, GLiNER is skipped."""
    embedding_service = MockEmbeddingService()
    memory_id = uuid.uuid4()

    from datetime import UTC, datetime

    from memoryhub_core.models.memory import MemoryNode

    now = datetime.now(UTC)
    async_session.add(MemoryNode(
        id=memory_id,
        content="Alice from Acme Corp visited New York.",
        stub="Alice from Acme...",
        scope="user",
        weight=0.9,
        owner_id="test-user",
        tenant_id="test-tenant",
        is_current=True,
        version=1,
        storage_type="inline",
        created_at=now,
        updated_at=now,
    ))
    await async_session.commit()

    fake_nlp = FakeNlp([
        SpacyEntity("Alice", "PERSON", 0, 5),
        SpacyEntity("Acme Corp", "ORG", 11, 20),
        SpacyEntity("New York", "GPE", 29, 37),
    ])

    gliner_called = False

    def mock_get_gliner():
        nonlocal gliner_called
        gliner_called = True
        return FakeGLiNERModel([])

    with (
        patch("memoryhub_core.services.extraction._get_nlp", return_value=fake_nlp),
        patch("memoryhub_core.services.extraction._get_gliner", side_effect=mock_get_gliner),
    ):
        result = await extract_entities_from_memory(
            memory_id=memory_id,
            content="Alice from Acme Corp visited New York.",
            session=async_session,
            embedding_service=embedding_service,
            tenant_id="test-tenant",
            owner_id="test-user",
        )

    assert result["count"] == 3
    assert not gliner_called, "GLiNER should not have been called when spaCy coverage is sufficient"
    extractors = {e["extractor"] for e in result["entities"]}
    assert extractors == {"spacy"}


@pytest.mark.asyncio
async def test_cascade_stage2_failure_falls_back_to_stage1(async_session):
    """If GLiNER fails, Stage 1 results are still used."""
    embedding_service = MockEmbeddingService()
    memory_id = uuid.uuid4()

    from datetime import UTC, datetime

    from memoryhub_core.models.memory import MemoryNode

    now = datetime.now(UTC)
    async_session.add(MemoryNode(
        id=memory_id,
        content="Alice went somewhere.",
        stub="Alice went...",
        scope="user",
        weight=0.9,
        owner_id="test-user",
        tenant_id="test-tenant",
        is_current=True,
        version=1,
        storage_type="inline",
        created_at=now,
        updated_at=now,
    ))
    await async_session.commit()

    fake_nlp = FakeNlp([SpacyEntity("Alice", "PERSON", 0, 5)])

    def broken_gliner():
        raise RuntimeError("model load failed")

    with (
        patch("memoryhub_core.services.extraction._get_nlp", return_value=fake_nlp),
        patch("memoryhub_core.services.extraction.run_gliner_ner", side_effect=RuntimeError("model load failed")),
    ):
        result = await extract_entities_from_memory(
            memory_id=memory_id,
            content="Alice went somewhere.",
            session=async_session,
            embedding_service=embedding_service,
            tenant_id="test-tenant",
            owner_id="test-user",
        )

    assert result["count"] == 1
    assert result["entities"][0]["name"] == "Alice"
    assert result["entities"][0]["extractor"] == "spacy"


@pytest.mark.asyncio
async def test_cascade_dedup_between_stages(async_session):
    """When both stages find the same entity, only one entity node is created."""
    embedding_service = MockEmbeddingService()
    memory_id = uuid.uuid4()

    from datetime import UTC, datetime

    from memoryhub_core.models.memory import MemoryNode

    now = datetime.now(UTC)
    async_session.add(MemoryNode(
        id=memory_id,
        content="Alice went to New York to deploy PostgreSQL.",
        stub="Alice went to New York...",
        scope="user",
        weight=0.9,
        owner_id="test-user",
        tenant_id="test-tenant",
        is_current=True,
        version=1,
        storage_type="inline",
        created_at=now,
        updated_at=now,
    ))
    await async_session.commit()

    # Stage 1: finds Alice (1 entity, triggers Stage 2)
    fake_nlp = FakeNlp([SpacyEntity("Alice", "PERSON", 0, 5)])
    # Stage 2: also finds Alice (person) + PostgreSQL (database)
    fake_gliner = FakeGLiNERModel([
        {"text": "Alice", "label": "person", "score": 0.95, "start": 0, "end": 5},
        {"text": "PostgreSQL", "label": "database", "score": 0.92, "start": 34, "end": 44},
    ])

    with (
        patch("memoryhub_core.services.extraction._get_nlp", return_value=fake_nlp),
        patch("memoryhub_core.services.extraction._get_gliner", return_value=fake_gliner),
    ):
        result = await extract_entities_from_memory(
            memory_id=memory_id,
            content="Alice went to New York to deploy PostgreSQL.",
            session=async_session,
            embedding_service=embedding_service,
            tenant_id="test-tenant",
            owner_id="test-user",
        )

    # Alice from Stage 1 + PostgreSQL from Stage 2 (Alice duplicate from Stage 2 dropped)
    assert result["count"] == 2
    names = {e["name"] for e in result["entities"]}
    assert names == {"Alice", "PostgreSQL"}

    # Verify extractors are correctly tagged
    extractor_map = {e["name"]: e["extractor"] for e in result["entities"]}
    assert extractor_map["Alice"] == "spacy"
    assert extractor_map["PostgreSQL"] == "gliner"
