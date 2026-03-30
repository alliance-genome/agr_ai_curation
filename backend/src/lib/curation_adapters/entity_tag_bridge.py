"""Temporary entity-tag bridge catalog for literature-aligned UI integrations.

Keep shared curation-workspace contracts and services free of hard-coded
project/domain catalog values by isolating the current entity-tag bridge here.

TODO: Replace this local bridge with a live read from the literature UI/source
catalog so the workspace entity table uses the same controlled vocabulary
directly instead of maintaining these values by hand.
"""

from __future__ import annotations

from typing import Final

ENTITY_FIELD_KEYS: Final[tuple[str, ...]] = ("entity_name", "gene_symbol")
ENTITY_TYPE_FIELD_KEYS: Final[tuple[str, ...]] = (
    "entity_type",
    "entity_type_code",
    "entity_type_atp_code",
)
SPECIES_FIELD_KEYS: Final[tuple[str, ...]] = ("species", "taxon", "taxon_id")
TOPIC_FIELD_KEYS: Final[tuple[str, ...]] = ("topic", "topic_name", "topic_term", "topic_curie")
