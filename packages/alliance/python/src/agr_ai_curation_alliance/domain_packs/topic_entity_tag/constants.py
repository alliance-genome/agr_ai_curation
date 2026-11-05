"""Constants for the Alliance topic-entity-tag proposal domain pack.

LinkML has no topic/entity-tag model. The contract is ABC's topic-entity-tag API plus
ATP terms, pinned here. WB with ``ATP:0000041`` is the first target.
"""

from __future__ import annotations

TOPIC_ENTITY_TAG_DOMAIN_PACK_ID = "agr.alliance.topic_entity_tag"
TOPIC_ENTITY_TAG_DOMAIN_PACK_VERSION = "0.1.0"
TOPIC_ENTITY_TAG_ADAPTER_KEY = "topic_entity_tag"
TOPIC_ENTITY_TAG_OBJECT_TYPE = "topic_entity_tag"
TOPIC_ENTITY_TAG_MODEL_ID = "TopicEntityTagPayload"
TOPIC_ENTITY_TAG_OBJECT_ROLE = "curatable_unit"
TOPIC_ENTITY_TAG_MATERIALIZER_ID = "topic_entity_tag.builder_materializer.v1"
TOPIC_ENTITY_TAG_PENDING_REF_ID = "topic-entity-tag-1"

TOPIC_ENTITY_TAG_TOPIC = "ATP:0000041"
TOPIC_ENTITY_TAG_TOPIC_NAME = "gene expression in wild type"
TOPIC_ENTITY_TAG_MOD_ABBREVIATION = "WB"

ABC_LITERATURE_SERVICE_COMMIT = "c4b6436c849aff86c6b7b3914f364b75c606dee5"
ATP_ONTOLOGY_COMMIT = "74b358e69d4660d0c6bfce80eac6d486774cedaa"
TOPIC_ENTITY_TAG_SCHEMA_ID = "abc.literature.TopicEntityTagSchemaPost"
TOPIC_ENTITY_TAG_SCHEMA_PROVIDER = "abc_literature"
TOPIC_ENTITY_TAG_SCHEMA_NAME = "TopicEntityTagSchemaPost"
TOPIC_ENTITY_TAG_SCHEMA_SOURCE_FILE = (
    "agr_literature_service/api/schemas/topic_entity_tag_schemas.py"
)
TOPIC_ENTITY_TAG_SCHEMA_URI = (
    "https://github.com/alliance-genome/agr_literature_service/blob/"
    f"{ABC_LITERATURE_SERVICE_COMMIT}/{TOPIC_ENTITY_TAG_SCHEMA_SOURCE_FILE}"
)

TOPIC_ENTITY_TAG_DEFINITION_NOTES = (
    "Review-only proposal for a curator to sign off. Nothing here writes to ABC.",
)

__all__ = [
    "ABC_LITERATURE_SERVICE_COMMIT",
    "ATP_ONTOLOGY_COMMIT",
    "TOPIC_ENTITY_TAG_ADAPTER_KEY",
    "TOPIC_ENTITY_TAG_DEFINITION_NOTES",
    "TOPIC_ENTITY_TAG_DOMAIN_PACK_ID",
    "TOPIC_ENTITY_TAG_DOMAIN_PACK_VERSION",
    "TOPIC_ENTITY_TAG_MATERIALIZER_ID",
    "TOPIC_ENTITY_TAG_MODEL_ID",
    "TOPIC_ENTITY_TAG_MOD_ABBREVIATION",
    "TOPIC_ENTITY_TAG_OBJECT_ROLE",
    "TOPIC_ENTITY_TAG_OBJECT_TYPE",
    "TOPIC_ENTITY_TAG_PENDING_REF_ID",
    "TOPIC_ENTITY_TAG_SCHEMA_ID",
    "TOPIC_ENTITY_TAG_SCHEMA_NAME",
    "TOPIC_ENTITY_TAG_SCHEMA_PROVIDER",
    "TOPIC_ENTITY_TAG_SCHEMA_SOURCE_FILE",
    "TOPIC_ENTITY_TAG_SCHEMA_URI",
    "TOPIC_ENTITY_TAG_TOPIC",
    "TOPIC_ENTITY_TAG_TOPIC_NAME",
]
