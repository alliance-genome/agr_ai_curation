"""Alliance generic extraction domain-pack helpers."""

from .catalog import (
    GenericClassCatalog,
    GenericClassCatalogEntry,
    get_generated_generic_domain_pack,
    load_generic_class_catalog,
    proxy_object_type,
)
from .constants import (
    GENERIC_CLAIM_OBJECT_TYPE,
    GENERIC_DOMAIN_PACK_ID,
    GENERIC_DOMAIN_PACK_VERSION,
    GENERIC_MATERIALIZER_ID,
    GENERIC_OBJECT_TYPE,
    GENERIC_PROXY_PREFIX,
    GENERIC_REAGENT_CANDIDATE_OBJECT_TYPE,
)
from .conversion import (
    GenericBuilderExtractionOutput,
    GenericMaterializationResult,
    materialize_generic_builder_state,
)

__all__ = [
    "GENERIC_CLAIM_OBJECT_TYPE",
    "GENERIC_DOMAIN_PACK_ID",
    "GENERIC_DOMAIN_PACK_VERSION",
    "GENERIC_MATERIALIZER_ID",
    "GENERIC_OBJECT_TYPE",
    "GENERIC_PROXY_PREFIX",
    "GENERIC_REAGENT_CANDIDATE_OBJECT_TYPE",
    "GenericBuilderExtractionOutput",
    "GenericClassCatalog",
    "GenericClassCatalogEntry",
    "GenericMaterializationResult",
    "get_generated_generic_domain_pack",
    "load_generic_class_catalog",
    "materialize_generic_builder_state",
    "proxy_object_type",
]
