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


def _coerce_evidence_record_dict(value: Any) -> Optional[Dict[str, Any]]:
    """Coerce evidence-record-like values into plain dictionaries."""

    if isinstance(value, dict):
        return value

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped

    return coerce_tool_event_dict(value)


def _normalize_evidence_record(
    value: Any,
    *,
    entity_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Normalize one evidence record, optionally overriding the display entity."""

    record_dict = _coerce_evidence_record_dict(value)
    if not isinstance(record_dict, dict):
        return None

    entity_source = entity_override if entity_override is not None else record_dict.get("entity")
    entity = str(entity_source or "").strip()
    verified_quote = str(record_dict.get("verified_quote") or "").strip()
    section = str(record_dict.get("section") or "").strip()
    chunk_id = str(record_dict.get("chunk_id") or "").strip()
    page = record_dict.get("page")

    if (
        not entity
        or not verified_quote
        or not section
        or not chunk_id
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

    subsection = str(record_dict.get("subsection") or "").strip()
    if subsection:
        evidence_record["subsection"] = subsection

    figure_reference = str(record_dict.get("figure_reference") or "").strip()
    if figure_reference:
        evidence_record["figure_reference"] = figure_reference

    return evidence_record


def _merge_unique_strings(*values: Any) -> List[str]:
    """Merge string values/lists while preserving first-seen order."""

    merged: List[str] = []
    seen: set[str] = set()

    def _append(candidate: Any) -> None:
        text = str(candidate or "").strip()
        if not text:
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        merged.append(text)

    for value in values:
        if isinstance(value, list):
            for item in value:
                _append(item)
            continue
        _append(value)

    return merged


def _normalized_identity_key(
    record_dict: Dict[str, Any],
    *,
    allow_label_fallback: bool,
) -> Optional[str]:
    """Build a deterministic grouping key for retained normalized records."""

    normalized_id = str(record_dict.get("normalized_id") or "").strip()
    if normalized_id:
        return f"id:{normalized_id.casefold()}"

    if not allow_label_fallback:
        return None

    label = str(record_dict.get("label") or "").strip()
    if not label:
        return None

    entity_type = str(record_dict.get("entity_type") or "").strip().casefold()
    return f"label:{entity_type}:{label.casefold()}"


def _normalized_label_candidates(record_dict: Dict[str, Any]) -> List[str]:
    """Return preferred normalized labels exposed by a retained record."""

    candidates: List[str] = []

    for key, value in record_dict.items():
        if key == "normalized_id" or not key.startswith("normalized_"):
            continue
        text = str(value or "").strip()
        if text:
            candidates.append(text)

    label = str(record_dict.get("label") or "").strip()
    if label:
        candidates.append(label)

    mention = str(record_dict.get("mention") or "").strip()
    if mention:
        candidates.append(mention)

    return _merge_unique_strings(candidates)


def _collect_preferred_labels(payload: Dict[str, Any]) -> Dict[str, str]:
    """Collect stable display labels for normalized identities across the payload."""

    preferred_labels: Dict[str, str] = {}

    for value in payload.values():
        if not isinstance(value, list):
            continue

        for item in value:
            item_dict = _coerce_evidence_record_dict(item)
            if not isinstance(item_dict, dict):
                continue

            identity_key = _normalized_identity_key(
                item_dict,
                allow_label_fallback=False,
            )
            if not identity_key or identity_key in preferred_labels:
                continue

            label_candidates = _normalized_label_candidates(item_dict)
            if label_candidates:
                preferred_labels[identity_key] = label_candidates[0]

    return preferred_labels


def _merge_normalized_evidence_records(
    *,
    existing: Any,
    incoming: Any,
    entity_override: str,
) -> List[Dict[str, Any]]:
    """Merge evidence records under a canonical entity label."""

    merged: List[Dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for source in (existing, incoming):
        if not isinstance(source, list):
            continue
        for record in source:
            evidence_record = _normalize_evidence_record(
                record,
                entity_override=entity_override,
            )
            if evidence_record is None:
                continue

            key = (
                evidence_record.get("entity"),
                evidence_record.get("verified_quote"),
                evidence_record.get("page"),
                evidence_record.get("section"),
                evidence_record.get("chunk_id"),
                evidence_record.get("subsection"),
                evidence_record.get("figure_reference"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(evidence_record)

    return merged


def _consolidate_items(
    items: Any,
    *,
    preferred_labels: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Collapse retained items that resolve to the same normalized identity."""

    if not isinstance(items, list):
        return []

    consolidated: List[Dict[str, Any]] = []
    grouped_indexes: Dict[str, int] = {}

    for item in items:
        item_dict = _coerce_evidence_record_dict(item)
        if not isinstance(item_dict, dict):
            continue

        identity_key = _normalized_identity_key(
            item_dict,
            allow_label_fallback=True,
        )
        if not identity_key:
            consolidated.append(item_dict)
            continue

        display_label = (
            preferred_labels.get(identity_key)
            or str(item_dict.get("label") or "").strip()
        )

        if identity_key not in grouped_indexes:
            merged_item = dict(item_dict)
            merged_item["label"] = display_label
            merged_item["source_mentions"] = _merge_unique_strings(
                item_dict.get("source_mentions"),
                item_dict.get("label"),
            )
            merged_item["evidence"] = _merge_normalized_evidence_records(
                existing=[],
                incoming=item_dict.get("evidence"),
                entity_override=display_label,
            )
            grouped_indexes[identity_key] = len(consolidated)
            consolidated.append(merged_item)
            continue

        existing_index = grouped_indexes[identity_key]
        merged_item = dict(consolidated[existing_index])
        merged_item["label"] = display_label
        merged_item["source_mentions"] = _merge_unique_strings(
            merged_item.get("source_mentions"),
            item_dict.get("source_mentions"),
            item_dict.get("label"),
        )
        merged_item["evidence"] = _merge_normalized_evidence_records(
            existing=merged_item.get("evidence"),
            incoming=item_dict.get("evidence"),
            entity_override=display_label,
        )
        consolidated[existing_index] = merged_item

    return consolidated


def _merge_retained_record(
    existing_record: Dict[str, Any],
    incoming_record: Dict[str, Any],
    *,
    preferred_label: Optional[str],
) -> Dict[str, Any]:
    """Merge duplicate retained normalized records without guessing new identities."""

    merged_record = dict(existing_record)

    for key, incoming_value in incoming_record.items():
        if key == "evidence":
            continue
        if key == "confidence":
            ranking = {"low": 0, "medium": 1, "high": 2}
            current_value = str(merged_record.get(key) or "").strip().lower()
            candidate_value = str(incoming_value or "").strip().lower()
            if ranking.get(candidate_value, -1) > ranking.get(current_value, -1):
                merged_record[key] = incoming_value
            continue

        current_value = merged_record.get(key)
        if current_value in (None, "", []):
            merged_record[key] = incoming_value

    if isinstance(merged_record.get("source_mentions"), list) or isinstance(incoming_record.get("source_mentions"), list):
        merged_record["source_mentions"] = _merge_unique_strings(
            merged_record.get("source_mentions"),
            incoming_record.get("source_mentions"),
            merged_record.get("label"),
            incoming_record.get("label"),
        )

    entity_override = preferred_label or str(
        merged_record.get("label")
        or merged_record.get("mention")
        or incoming_record.get("label")
        or incoming_record.get("mention")
        or ""
    ).strip()
    merged_record["evidence"] = _merge_normalized_evidence_records(
        existing=merged_record.get("evidence"),
        incoming=incoming_record.get("evidence"),
        entity_override=entity_override,
    )

    return merged_record


def _consolidate_retained_normalized_list(
    value: Any,
    *,
    preferred_labels: Dict[str, str],
) -> Optional[List[Dict[str, Any]]]:
    """Collapse duplicate retained records when they share a normalized identity."""

    if not isinstance(value, list):
        return None

    consolidated: List[Dict[str, Any]] = []
    grouped_indexes: Dict[str, int] = {}
    saw_normalized_identity = False

    for item in value:
        item_dict = _coerce_evidence_record_dict(item)
        if not isinstance(item_dict, dict):
            return None

        identity_key = _normalized_identity_key(
            item_dict,
            allow_label_fallback=False,
        )
        if not identity_key:
            consolidated.append(item_dict)
            continue

        saw_normalized_identity = True
        preferred_label = preferred_labels.get(identity_key)
        if identity_key not in grouped_indexes:
            merged_item = dict(item_dict)
            if preferred_label and "label" in merged_item:
                merged_item["label"] = preferred_label
            grouped_indexes[identity_key] = len(consolidated)
            consolidated.append(merged_item)
            continue

        existing_index = grouped_indexes[identity_key]
        consolidated[existing_index] = _merge_retained_record(
            consolidated[existing_index],
            item_dict,
            preferred_label=preferred_label,
        )

    if not saw_normalized_identity:
        return None

    return consolidated
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
        evidence_record = _normalize_evidence_record(record)
        if evidence_record is not None:
            evidence_records.append(evidence_record)

    return evidence_records


def _extract_evidence_records_from_items(value: Any) -> List[Dict[str, Any]]:
    """Extract canonical evidence records from retained structured items."""

    if not isinstance(value, list):
        return []

    evidence_records: List[Dict[str, Any]] = []

    for item in value:
        item_dict = _coerce_evidence_record_dict(item)
        if not isinstance(item_dict, dict):
            continue

        canonical_entity = str(item_dict.get("label") or "").strip()
        if not canonical_entity:
            continue

        for record in item_dict.get("evidence") or []:
            evidence_record = _normalize_evidence_record(
                record,
                entity_override=canonical_entity,
            )
            if evidence_record is not None:
                evidence_records.append(evidence_record)

    return evidence_records


def canonicalize_structured_result_payload(value: Any) -> Any:
    """Collapse duplicate retained normalized items in structured extraction payloads."""

    payload = _coerce_evidence_record_dict(value)
    if not isinstance(payload, dict):
        return value

    preferred_labels = _collect_preferred_labels(payload)
    canonical_payload = dict(payload)

    original_items = payload.get("items")
    canonical_items = _consolidate_items(
        original_items,
        preferred_labels=preferred_labels,
    )
    if canonical_items:
        canonical_payload["items"] = canonical_items
        canonical_payload["evidence_records"] = _extract_evidence_records_from_items(canonical_items)

        run_summary = canonical_payload.get("run_summary")
        if isinstance(run_summary, dict):
            canonical_run_summary = dict(run_summary)
            canonical_run_summary["kept_count"] = len(canonical_items)
            canonical_payload["run_summary"] = canonical_run_summary

        if isinstance(original_items, list) and len(canonical_items) < len(original_items):
            canonical_payload["summary"] = (
                f"Retained {len(canonical_items)} normalized items with verified evidence "
                "after consolidating duplicate identifiers."
            )
            canonical_payload["normalization_notes"] = _merge_unique_strings(
                canonical_payload.get("normalization_notes"),
                (
                    "Duplicate retained mentions were consolidated by normalized identity "
                    f"({len(original_items)} -> {len(canonical_items)} retained items)."
                ),
            )

    for key, field_value in payload.items():
        if key == "items":
            continue
        consolidated_field = _consolidate_retained_normalized_list(
            field_value,
            preferred_labels=preferred_labels,
        )
        if consolidated_field is not None:
            canonical_payload[key] = consolidated_field

    return canonical_payload


def extract_evidence_records_from_structured_result(value: Any) -> List[Dict[str, Any]]:
    """Extract normalized evidence records from a structured extraction result."""

    payload = _coerce_evidence_record_dict(canonicalize_structured_result_payload(value))
    if not isinstance(payload, dict):
        return []

    item_evidence_records = _extract_evidence_records_from_items(payload.get("items"))
    if item_evidence_records:
        return item_evidence_records

    return normalize_evidence_records(payload.get("evidence_records"))


def structured_result_requires_evidence(value: Any) -> bool:
    """Whether a structured result represents retained extraction findings that require evidence."""

    payload = _coerce_evidence_record_dict(value)
    if not isinstance(payload, dict):
        return False

    items = payload.get("items")
    if isinstance(items, list) and items:
        return True

    run_summary = payload.get("run_summary")
    if isinstance(run_summary, dict):
        kept_count = run_summary.get("kept_count")
        if isinstance(kept_count, int) and not isinstance(kept_count, bool) and kept_count > 0:
            return True

    return False
