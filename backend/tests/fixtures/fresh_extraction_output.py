"""Send real fresh builder output through both checks that it records every value's state.

A builder's finalized extraction must pass the chat-runtime normalization of
fresh output (``domain_envelope_from_extraction_result(stored=False)``) and the
new-row check (``require_recorded_resolution_states``). Tests call this with a
real pack's materialized output so a builder that stages a stateless declared
value fails here, not in a curator's turn.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.lib.curation_workspace.adapter_registry import load_curation_adapter_registry
from src.lib.curation_workspace.domain_envelope_normalization import (
    domain_envelope_from_extraction_result,
    require_recorded_resolution_states,
)
from src.lib.domain_packs.resolvable_values import declared_resolvable_fields
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


def assert_fresh_output_records_every_state(
    payload: Mapping[str, Any], *, adapter_key: str, agent_key: str,
) -> set[str]:
    """Both checks accept ``payload``; returns the object types whose values they checked."""

    domain_pack = load_curation_adapter_registry().get_domain_pack(adapter_key)
    assert domain_pack is not None, adapter_key
    checked = {
        str(item["object_type"])
        for item in payload["curatable_objects"]
        if declared_resolvable_fields(domain_pack.metadata, str(item["object_type"]))
    }
    # Otherwise the checks below would pass without looking at anything.
    assert checked, f"{adapter_key} output carries no declared resolvable values"
    record = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "fresh-builder-output",
            "document_id": "fresh-builder-document",
            "adapter_key": adapter_key,
            "agent_key": agent_key,
            "source_kind": CurationExtractionSourceKind.CHAT,
            "candidate_count": len(payload["curatable_objects"]),
            "payload_json": dict(payload),
            "created_at": datetime.now(timezone.utc),
            "metadata": {},
        }
    )
    domain_envelope_from_extraction_result(record, stored=False)
    require_recorded_resolution_states(payload, adapter_key=adapter_key)
    return checked
