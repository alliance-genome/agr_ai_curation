"""Pinned Alliance LinkML schema-ref conventions for domain packs."""

from __future__ import annotations

ALLIANCE_BASE_DOMAIN_PACK_ID = "agr.alliance.base"

ALLIANCE_LINKML_PROVIDER_KEY = "alliance_linkml"
ALLIANCE_LINKML_REPOSITORY = (
    "https://github.com/alliance-genome/agr_curation_schema.git"
)
ALLIANCE_LINKML_COMMIT = "1b11d0888f19eba4ca72022200bb7d96b30d4a52"
ALLIANCE_LINKML_SCHEMA_DIR = "model/schema"
ALLIANCE_LINKML_ROOT_SCHEMA_PATH = "model/schema/allianceModel.yaml"

PROVIDER_REFS_METADATA_KEY = "provider_refs"
OBJECT_ROLE_METADATA_KEY = "object_role"
REQUIRED_OBJECT_ROLES = ("curatable_unit", "validated_reference", "metadata_only")

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
]
