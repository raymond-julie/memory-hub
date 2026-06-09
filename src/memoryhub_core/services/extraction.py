"""Entity extraction pipeline -- spaCy NER + GLiNER2 zero-shot cascade.

Two-stage extraction cascade (#170 Phase 2, #248). Stage 1 (spaCy) runs
always for fast person/org/location/event extraction. Stage 2 (GLiNER2)
fires only when Stage 1 finds fewer than 2 high-confidence entities,
adding zero-shot extraction for objects, technologies, and domain terms.

Entities are created via find_or_create_entity and linked to the source
memory via MENTIONS relationships. Designed for async background execution
after write commits.
"""

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from memoryhub_core.config import AppSettings
from memoryhub_core.services.embeddings import EmbeddingService
from memoryhub_core.services.entity import (
    create_mentions_relationship,
    find_or_create_entity,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage 1: spaCy NER
# ---------------------------------------------------------------------------

# spaCy label -> POLE+O type mapping.  Unmapped labels (DATE, TIME, MONEY,
# PERCENT, QUANTITY, ORDINAL, CARDINAL, NORP, PRODUCT, WORK_OF_ART, LAW,
# LANGUAGE) are skipped -- they don't map to the POLE+O entity model.
_SPACY_LABEL_MAP: dict[str, str] = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "location",
    "LOC": "location",
    "FAC": "location",
    "EVENT": "event",
}

_nlp = None


def _get_nlp():
    """Lazy-load the spaCy model. Cached at module level."""
    global _nlp
    if _nlp is None:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
    return _nlp


def run_spacy_ner(text: str) -> list[dict[str, Any]]:
    """Run spaCy NER and return extracted entities.

    Returns a list of dicts with keys: name, type, label, start, end, confidence.
    Deduplicates by (lowered name, type) within a single text, keeping
    the first occurrence.
    """
    if not text or not text.strip():
        return []

    nlp = _get_nlp()
    doc = nlp(text)

    seen: set[tuple[str, str]] = set()
    entities: list[dict[str, Any]] = []

    for ent in doc.ents:
        pole_type = _SPACY_LABEL_MAP.get(ent.label_)
        if pole_type is None:
            continue

        key = (ent.text.strip().lower(), pole_type)
        if key in seen:
            continue
        seen.add(key)

        entities.append({
            "name": ent.text.strip(),
            "type": pole_type,
            "label": ent.label_,
            "start": ent.start_char,
            "end": ent.end_char,
            "confidence": 1.0,
        })

    return entities


# ---------------------------------------------------------------------------
# Stage 2: GLiNER2 zero-shot NER
# ---------------------------------------------------------------------------

_GLINER_LABELS = [
    "person", "organization", "location", "event",
    "technology", "programming language", "framework",
    "protocol", "database", "tool", "concept",
]

# Map GLiNER's fine-grained labels back to POLE+O types
_GLINER_LABEL_TO_POLE: dict[str, str] = {
    "person": "person",
    "organization": "organization",
    "location": "location",
    "event": "event",
    "technology": "object",
    "programming language": "object",
    "framework": "object",
    "protocol": "object",
    "database": "object",
    "tool": "object",
    "concept": "object",
}

_gliner_model = None


def _get_gliner():
    """Lazy-load the GLiNER model. Cached at module level (assumes gliner_model config is static)."""
    global _gliner_model
    if _gliner_model is None:
        from gliner import GLiNER
        settings = AppSettings()
        _gliner_model = GLiNER.from_pretrained(settings.gliner_model)
    return _gliner_model


def run_gliner_ner(text: str) -> list[dict[str, Any]]:
    """Run GLiNER zero-shot NER and return extracted entities.

    Returns a list of dicts with keys: name, type, label, start, end, confidence.
    Deduplicates by (lowered name, type) within a single text.
    """
    if not text or not text.strip():
        return []

    settings = AppSettings()
    model = _get_gliner()
    raw_entities = model.predict_entities(
        text,
        _GLINER_LABELS,
        threshold=settings.gliner_confidence_threshold,
    )

    seen: set[tuple[str, str]] = set()
    entities: list[dict[str, Any]] = []

    for ent in raw_entities:
        pole_type = _GLINER_LABEL_TO_POLE.get(ent["label"])
        if pole_type is None:
            continue

        name = ent["text"].strip()
        if not name:
            continue

        key = (name.lower(), pole_type)
        if key in seen:
            continue
        seen.add(key)

        entities.append({
            "name": name,
            "type": pole_type,
            "label": ent["label"],
            "start": ent["start"],
            "end": ent["end"],
            "confidence": ent["score"],
        })

    return entities


# ---------------------------------------------------------------------------
# Cascade: Stage 1 -> Stage 2 (conditional)
# ---------------------------------------------------------------------------

def _should_run_stage2(stage1_entities: list[dict[str, Any]]) -> bool:
    """Return True if Stage 1 coverage is too low and Stage 2 should run."""
    settings = AppSettings()
    high_confidence = sum(
        1 for e in stage1_entities
        if e.get("confidence", 0) >= settings.gliner_stage2_trigger_confidence
    )
    return high_confidence < settings.gliner_stage2_trigger_count


def _tag_extractor(entities: list[dict[str, Any]], extractor: str) -> list[dict[str, Any]]:
    """Return a copy of each entity dict tagged with its source extractor name."""
    return [{**e, "extractor": extractor} for e in entities]


def _merge_entities(
    stage1: list[dict[str, Any]],
    stage2: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge Stage 2 entities into Stage 1, dropping duplicates by (name, type)."""
    seen = {(e["name"].lower(), e["type"]) for e in stage1}
    merged = list(stage1)
    for ent in stage2:
        key = (ent["name"].lower(), ent["type"])
        if key not in seen:
            seen.add(key)
            merged.append(ent)
    return merged


async def extract_entities_from_memory(
    memory_id: uuid.UUID,
    content: str,
    session: AsyncSession,
    embedding_service: EmbeddingService,
    *,
    tenant_id: str,
    owner_id: str,
) -> dict[str, Any]:
    """Extract entities from memory content and create entity nodes + MENTIONS edges.

    Runs the two-stage cascade: Stage 1 (spaCy) always runs; Stage 2
    (GLiNER2) fires when Stage 1 yields fewer than 2 high-confidence entities.
    Returns a summary dict with extracted entity info for logging.
    """
    stage1_entities = _tag_extractor(run_spacy_ner(content), "spacy")

    if _should_run_stage2(stage1_entities):
        try:
            stage2_entities = _tag_extractor(run_gliner_ner(content), "gliner")
            all_entities = _merge_entities(stage1_entities, stage2_entities)
            logger.debug(
                "Stage 2 (GLiNER) added %d entities for memory %s",
                len(all_entities) - len(stage1_entities), memory_id,
            )
        except Exception:
            logger.warning(
                "GLiNER Stage 2 failed for memory %s; using Stage 1 results only",
                memory_id,
                exc_info=True,
            )
            all_entities = stage1_entities
    else:
        logger.debug(
            "Stage 2 (GLiNER) skipped for memory %s: %d high-confidence entities from Stage 1",
            memory_id, len(stage1_entities),
        )
        all_entities = stage1_entities

    if not all_entities:
        return {"memory_id": str(memory_id), "entities": [], "count": 0}

    created_entities: list[dict[str, Any]] = []

    for raw in all_entities:
        extractor = raw.get("extractor", "spacy")

        try:
            entity_node, was_created = await find_or_create_entity(
                name=raw["name"],
                entity_type=raw["type"],
                session=session,
                embedding_service=embedding_service,
                tenant_id=tenant_id,
                owner_id=owner_id,
                confidence=raw.get("confidence", 1.0),
                extractor=extractor,
            )

            await create_mentions_relationship(
                memory_id=memory_id,
                entity_id=entity_node.id,
                session=session,
                tenant_id=tenant_id,
                metadata={"extractor": extractor, "label": raw["label"]},
            )

            created_entities.append({
                "name": raw["name"],
                "type": raw["type"],
                "entity_id": str(entity_node.id),
                "was_created": was_created,
                "extractor": extractor,
            })
        except Exception:
            logger.warning(
                "Failed to process entity '%s' (type=%s) for memory %s",
                raw["name"], raw["type"], memory_id,
                exc_info=True,
            )

    return {
        "memory_id": str(memory_id),
        "entities": created_entities,
        "count": len(created_entities),
    }
