import json
from typing import Any, Dict, List, Optional


def coerce_tool_event_dict(value: Any) -> Optional[Dict[str, Any]]:
    """Parse tool event payloads that may be dictionaries or JSON strings."""

    if isinstance(value, dict):
        return value

    if not isinstance(value, str):
        return None

    payload = value.strip()
    if not payload:
        return None

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def build_record_evidence_summary_record(
    *,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> Optional[Dict[str, Any]]:
    """Build a normalized evidence summary record from a record_evidence tool event."""

    if tool_name != "record_evidence":
        return None

    output_payload = coerce_tool_event_dict(tool_output)
    input_payload = coerce_tool_event_dict(tool_input)

    if output_payload is None or input_payload is None:
        return None

    if str(output_payload.get("status") or "").strip().lower() != "verified":
        return None

    entity = str(input_payload.get("entity") or "").strip()
    chunk_id = str(input_payload.get("chunk_id") or "").strip()
    verified_quote = str(output_payload.get("verified_quote") or "").strip()
    section = str(output_payload.get("section") or "").strip()
    page = output_payload.get("page")

    if (
        not entity
        or not chunk_id
        or not verified_quote
        or not section
        or not isinstance(page, int)
        or isinstance(page, bool)
        or page <= 0
    ):
        return None

    evidence_record: Dict[str, Any] = {
        "entity": entity,
        "verified_quote": verified_quote,
        "page": page,
        "section": section,
        "chunk_id": chunk_id,
    }

    subsection = str(output_payload.get("subsection") or "").strip()
    if subsection:
        evidence_record["subsection"] = subsection

    figure_reference = str(output_payload.get("figure_reference") or "").strip()
    if figure_reference:
        evidence_record["figure_reference"] = figure_reference

    return evidence_record


def normalize_evidence_records(value: Any) -> List[Dict[str, Any]]:
    """Parse and normalize a value containing evidence records."""

    if not isinstance(value, list):
        return []

    evidence_records: List[Dict[str, Any]] = []

    for record in value:
        if not isinstance(record, dict):
            continue

        entity = str(record.get("entity") or "").strip()
        verified_quote = str(record.get("verified_quote") or "").strip()
        section = str(record.get("section") or "").strip()
        chunk_id = str(record.get("chunk_id") or "").strip()
        page = record.get("page")

        if (
            not entity
            or not verified_quote
            or not section
            or not chunk_id
            or not isinstance(page, int)
            or isinstance(page, bool)
            or page <= 0
        ):
            continue

        evidence_record = {
            "entity": entity,
            "verified_quote": verified_quote,
            "page": page,
            "section": section,
            "chunk_id": chunk_id,
        }

        subsection = str(record.get("subsection") or "").strip()
        if subsection:
            evidence_record["subsection"] = subsection

        figure_reference = str(record.get("figure_reference") or "").strip()
        if figure_reference:
            evidence_record["figure_reference"] = figure_reference

        evidence_records.append(evidence_record)

    return evidence_records
