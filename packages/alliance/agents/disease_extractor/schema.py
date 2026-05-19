"""Disease extractor schema for Alliance disease domain-envelope output."""

import sys
from pathlib import Path
from typing import Any

from pydantic import model_validator
from src.lib.openai_agents.models import (
    DiseaseExtractionResultEnvelope as RuntimeDiseaseExtractionResultEnvelope,
)

_ALLIANCE_PYTHON_SRC = Path(__file__).resolve().parents[2] / "python" / "src"
if str(_ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(_ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.disease import (  # noqa: E402
    validate_disease_extraction_objects,
)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _metadata_ref_paths_for_evidence_ids(
    metadata: dict[str, Any],
    evidence_record_ids: list[str],
) -> list[dict[str, str]]:
    evidence_ids = set(evidence_record_ids)
    refs: list[dict[str, str]] = []
    evidence_records = metadata.get("evidence_records")
    if isinstance(evidence_records, list):
        for index, record in enumerate(evidence_records):
            if not isinstance(record, dict):
                continue
            evidence_record_id = str(record.get("evidence_record_id") or "").strip()
            if evidence_record_id in evidence_ids:
                refs.append(
                    {
                        "metadata_path": f"evidence_records[{index}]",
                        "role": "supporting_evidence",
                    }
                )
    raw_mentions = metadata.get("raw_mentions")
    if isinstance(raw_mentions, list):
        for index, raw_mention in enumerate(raw_mentions):
            if not isinstance(raw_mention, dict):
                continue
            raw_evidence_ids = set(_string_list(raw_mention.get("evidence_record_ids")))
            if evidence_ids.intersection(raw_evidence_ids):
                refs.insert(
                    0,
                    {
                        "metadata_path": f"raw_mentions[{index}]",
                        "role": "source_mention",
                    },
                )
                break
    return refs


class DiseaseExtractionResultEnvelope(RuntimeDiseaseExtractionResultEnvelope):
    """Config-discovered Alliance disease extraction envelope."""

    __envelope_class__ = True

    @model_validator(mode="before")
    @classmethod
    def _canonicalize_pending_disease_scaffold(cls, value: Any) -> Any:
        """Fill deterministic envelope-scaffold fields before strict validation."""

        if not isinstance(value, dict):
            return value

        normalized = dict(value)
        metadata = normalized.get("metadata")
        metadata_payload = metadata if isinstance(metadata, dict) else {}
        objects = normalized.get("curatable_objects")
        if not isinstance(objects, list):
            return normalized

        canonical_objects: list[Any] = []
        for raw_obj in objects:
            if not isinstance(raw_obj, dict):
                canonical_objects.append(raw_obj)
                continue
            obj = dict(raw_obj)
            if obj.get("object_type") != "DiseaseAnnotation":
                canonical_objects.append(obj)
                continue

            payload = obj.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            payload_evidence_record_ids = _string_list(payload.get("evidence_record_ids"))
            if not _string_list(obj.get("evidence_record_ids")) and payload_evidence_record_ids:
                obj["evidence_record_ids"] = payload_evidence_record_ids

            schema_ref = obj.get("schema_ref")
            if (
                not obj.get("definition_state")
                and isinstance(schema_ref, dict)
                and schema_ref.get("definition_state")
            ):
                obj["definition_state"] = schema_ref.get("definition_state")

            obj_metadata = obj.get("metadata")
            obj_metadata = dict(obj_metadata) if isinstance(obj_metadata, dict) else {}
            obj_metadata.setdefault("assertion_kind", "pending_disease_assertion")
            obj_metadata.setdefault(
                "write_behavior",
                {
                    "status": "blocked",
                    "reason": (
                        "Subject identifier, durable reference, evidence-code, and "
                        "concrete disease annotation write targets are not fully "
                        "materialized from extractor output."
                    ),
                },
            )
            obj["metadata"] = obj_metadata

            if not obj.get("metadata_refs"):
                obj_evidence_record_ids = _string_list(obj.get("evidence_record_ids"))
                metadata_refs = _metadata_ref_paths_for_evidence_ids(
                    metadata_payload,
                    obj_evidence_record_ids,
                )
                if metadata_refs:
                    obj["metadata_refs"] = metadata_refs

            canonical_objects.append(obj)

        normalized["curatable_objects"] = canonical_objects
        return normalized

    @model_validator(mode="after")
    def _validate_disease_domain_contract(self) -> "DiseaseExtractionResultEnvelope":
        errors = validate_disease_extraction_objects(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self
