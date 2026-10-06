"""Deterministic ids for imported rows: the id itself says which copy belongs to which source.

Each importer gets private copies, so two curators importing one shared flow never
collide, and re-importing finds the same flow, agents and output structures without a
mapping table. Main AI Curation's UUIDs are never reused in the benchmark resolver.
"""

from typing import Literal
from uuid import UUID, uuid5

from src.lib.agent_studio.custom_agent_service import make_custom_agent_id

FLOW_IMPORT_NAMESPACE = UUID("6f1c3a52-9d0e-4b7a-8e21-5c4f0b9a7d13")

DerivedKind = Literal["flow", "agent", "agent_revision", "profile", "profile_revision"]


def derived_id(kind: DerivedKind, source_id: UUID, *, export_issuer: str, importer_sub: str) -> UUID:
    return uuid5(FLOW_IMPORT_NAMESPACE, f"{export_issuer}|{importer_sub}|{kind}|{source_id}")


def derived_agent_key(source_agent_id: UUID, *, export_issuer: str, importer_sub: str) -> str:
    return make_custom_agent_id(
        derived_id("agent", source_agent_id, export_issuer=export_issuer, importer_sub=importer_sub)
    )
