"""Alliance domain-pack loader hooks and schema-ref constants."""

from .loader import (
    get_alliance_domain_pack,
    load_alliance_domain_pack_registry,
    load_alliance_domain_packs,
)
from .paths import get_alliance_domain_packs_dir, get_alliance_domain_pack_metadata_path
from .schema_refs import (
    ALLIANCE_BASE_DOMAIN_PACK_ID,
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    ALLIANCE_LINKML_REPOSITORY,
    ALLIANCE_LINKML_ROOT_SCHEMA_PATH,
    ALLIANCE_LINKML_SCHEMA_DIR,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
    REQUIRED_OBJECT_ROLES,
)

__all__ = [
    "ALLIANCE_BASE_DOMAIN_PACK_ID",
    "ALLIANCE_LINKML_COMMIT",
    "ALLIANCE_LINKML_PROVIDER_KEY",
    "ALLIANCE_LINKML_REPOSITORY",
    "ALLIANCE_LINKML_ROOT_SCHEMA_PATH",
    "ALLIANCE_LINKML_SCHEMA_DIR",
    "OBJECT_ROLE_METADATA_KEY",
    "PROVIDER_REFS_METADATA_KEY",
    "REQUIRED_OBJECT_ROLES",
    "get_alliance_domain_pack",
    "get_alliance_domain_pack_metadata_path",
    "get_alliance_domain_packs_dir",
    "load_alliance_domain_pack_registry",
    "load_alliance_domain_packs",
]
