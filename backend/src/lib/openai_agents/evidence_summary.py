import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, get_args, get_origin

logger = logging.getLogger(__name__)

_NON_RETAINED_COLLECTION_DESCRIPTION_MARKERS = (
    "raw mention",
    "excluded",
    "ambiguous",
    "verified evidence registry",
    "normalization decisions",
)


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
    entity_fallback: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Normalize one evidence record and guarantee a stable evidence_record_id."""

    record_dict = _coerce_evidence_record_dict(value)
    if not isinstance(record_dict, dict):
        logger.debug(
            "Dropping evidence record that could not be coerced into a dictionary: value_type=%s",
            type(value).__name__,
        )
        return None

    entity_source = record_dict.get("entity")
    if entity_source in (None, ""):
        entity_source = entity_fallback
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
        invalid_fields: List[str] = []
        if not entity:
            invalid_fields.append("entity")
        if not verified_quote:
            invalid_fields.append("verified_quote")
        if not section:
            invalid_fields.append("section")
        if not chunk_id:
            invalid_fields.append("chunk_id")
        if not isinstance(page, int) or isinstance(page, bool) or page <= 0:
            invalid_fields.append(f"page={page!r}")

        logger.debug(
            "Dropping malformed evidence record with invalid fields %s; keys=%s entity_fallback=%r",
            ",".join(invalid_fields),
            sorted(record_dict.keys()),
            entity_fallback,
        )
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

    evidence_record["evidence_record_id"] = build_evidence_record_id(
        record_dict.get("evidence_record_id"),
        evidence_record=evidence_record,
    )

    return evidence_record


def build_evidence_record_id(
    existing_id: Any = None,
    *,
    evidence_record: Optional[Dict[str, Any]] = None,
) -> str:
    """Return a stable evidence record ID, preferring an existing valid ID."""

    normalized_existing = str(existing_id or "").strip()
    if normalized_existing:
        return normalized_existing

    if not isinstance(evidence_record, dict):
        raise ValueError("evidence_record is required when existing_id is empty")

    canonical_blob = json.dumps(
        [
            str(evidence_record.get("entity") or "").strip(),
            str(evidence_record.get("verified_quote") or "").strip(),
            evidence_record.get("page"),
            str(evidence_record.get("section") or "").strip(),
            str(evidence_record.get("chunk_id") or "").strip(),
            str(evidence_record.get("subsection") or "").strip(),
            str(evidence_record.get("figure_reference") or "").strip(),
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha1(canonical_blob.encode("utf-8")).hexdigest()[:16]
    return f"evidence-{digest}"


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


def _merge_unique_reference_ids(*values: Any) -> List[str]:
    """Merge evidence record IDs while preserving first-seen order."""

    merged: List[str] = []
    seen: set[str] = set()

    for value in values:
        if isinstance(value, list):
            candidates = value
        else:
            candidates = [value]

        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)

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


def _evidence_record_key(record: Dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("entity"),
        record.get("verified_quote"),
        record.get("page"),
        record.get("section"),
        record.get("chunk_id"),
        record.get("subsection"),
        record.get("figure_reference"),
    )


def _evidence_locator_key(record: Dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("verified_quote"),
        record.get("page"),
        record.get("section"),
        record.get("chunk_id"),
        record.get("subsection"),
        record.get("figure_reference"),
    )


class _EvidenceRegistry:
    """Canonical evidence registry keyed by stable tool-verified record IDs."""

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []
        self._records_by_id: Dict[str, Dict[str, Any]] = {}
        self._id_by_exact_key: Dict[tuple[Any, ...], str] = {}
        self._ids_by_locator_key: Dict[tuple[Any, ...], List[str]] = {}

    def add_many(
        self,
        values: Any,
        *,
        entity_fallback: Optional[str] = None,
        allow_locator_fallback: bool = False,
    ) -> List[str]:
        if not isinstance(values, list):
            return []

        added_ids: List[str] = []
        for value in values:
            evidence_record_id = self.add(
                value,
                entity_fallback=entity_fallback,
                allow_locator_fallback=allow_locator_fallback,
            )
            if evidence_record_id:
                added_ids.append(evidence_record_id)
        return added_ids

    def add(
        self,
        value: Any,
        *,
        entity_fallback: Optional[str] = None,
        allow_locator_fallback: bool = False,
    ) -> Optional[str]:
        normalized_record = _normalize_evidence_record(
            value,
            entity_fallback=entity_fallback,
        )
        if normalized_record is None:
            return None

        exact_key = _evidence_record_key(normalized_record)
        existing_id = self._id_by_exact_key.get(exact_key)
        if existing_id:
            return existing_id

        locator_key = _evidence_locator_key(normalized_record)
        locator_ids = self._ids_by_locator_key.get(locator_key, [])
        if allow_locator_fallback and len(locator_ids) == 1:
            return locator_ids[0]

        evidence_record_id = str(normalized_record["evidence_record_id"])
        self._records.append(normalized_record)
        self._records_by_id[evidence_record_id] = normalized_record
        self._id_by_exact_key[exact_key] = evidence_record_id
        self._ids_by_locator_key.setdefault(locator_key, []).append(evidence_record_id)
        return evidence_record_id

    def records(self) -> List[Dict[str, Any]]:
        return list(self._records)


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
            merged_item["evidence_record_ids"] = _merge_unique_reference_ids(
                item_dict.get("evidence_record_ids"),
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
        merged_item["evidence_record_ids"] = _merge_unique_reference_ids(
            merged_item.get("evidence_record_ids"),
            item_dict.get("evidence_record_ids"),
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

    merged_record["evidence_record_ids"] = _merge_unique_reference_ids(
        merged_record.get("evidence_record_ids"),
        incoming_record.get("evidence_record_ids"),
    )

    if preferred_label and "label" in merged_record:
        merged_record["label"] = preferred_label

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
        logger.debug(
            "Skipping record_evidence summary event with malformed payloads: input_type=%s output_type=%s",
            type(tool_input).__name__,
            type(tool_output).__name__,
        )
        return None

    if str(output_payload.get("status") or "").strip().lower() != "verified":
        return None

    entity = str(input_payload.get("entity") or "").strip()
    chunk_id = str(output_payload.get("chunk_id") or input_payload.get("chunk_id") or "").strip()
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
        logger.debug(
            "Skipping verified record_evidence payload with invalid fields: entity=%r chunk_id=%r section=%r page=%r",
            entity,
            chunk_id,
            section,
            page,
        )
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

    evidence_record["evidence_record_id"] = build_evidence_record_id(
        output_payload.get("evidence_record_id"),
        evidence_record=evidence_record,
    )

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


def _candidate_entity_fallback(record_dict: Dict[str, Any]) -> Optional[str]:
    for key in ("label", "mention"):
        candidate = str(record_dict.get(key) or "").strip()
        if candidate:
            return candidate

    source_mentions = record_dict.get("source_mentions")
    if isinstance(source_mentions, list):
        for candidate in source_mentions:
            text = str(candidate or "").strip()
            if text:
                return text

    return None


def _canonicalize_nested_evidence_references(
    value: Any,
    *,
    registry: _EvidenceRegistry,
    top_level: bool = False,
) -> Any:
    """Replace legacy inline evidence blobs with evidence_record_ids recursively."""

    if isinstance(value, list):
        return [
            _canonicalize_nested_evidence_references(item, registry=registry)
            for item in value
        ]

    record_dict = _coerce_evidence_record_dict(value)
    if not isinstance(record_dict, dict):
        return value

    canonical_record: Dict[str, Any] = {}
    for key, nested_value in record_dict.items():
        if key in {"evidence", "evidence_record_ids"}:
            continue
        if top_level and key == "evidence_records":
            continue
        canonical_record[key] = _canonicalize_nested_evidence_references(
            nested_value,
            registry=registry,
        )

    evidence_record_ids = _merge_unique_reference_ids(record_dict.get("evidence_record_ids"))
    legacy_evidence = record_dict.get("evidence")
    if isinstance(legacy_evidence, list):
        evidence_record_ids = _merge_unique_reference_ids(
            evidence_record_ids,
            registry.add_many(
                legacy_evidence,
                entity_fallback=_candidate_entity_fallback(record_dict),
                allow_locator_fallback=True,
            ),
        )

    if "evidence_record_ids" in record_dict or isinstance(legacy_evidence, list):
        canonical_record["evidence_record_ids"] = evidence_record_ids

    return canonical_record


def canonicalize_structured_result_payload(
    value: Any,
    *,
    preferred_evidence_records: Any = None,
) -> Any:
    """Collapse duplicate retained normalized items in structured extraction payloads."""

    payload = _coerce_evidence_record_dict(value)
    if not isinstance(payload, dict):
        return value

    preferred_labels = _collect_preferred_labels(payload)
    registry = _EvidenceRegistry()
    registry.add_many(
        preferred_evidence_records if preferred_evidence_records is not None else payload.get("evidence_records")
    )
    canonical_payload = _canonicalize_nested_evidence_references(
        payload,
        registry=registry,
        top_level=True,
    )

    original_items = canonical_payload.get("items")
    canonical_items = _consolidate_items(
        original_items,
        preferred_labels=preferred_labels,
    )
    if isinstance(original_items, list):
        canonical_payload["items"] = canonical_items
        canonical_payload["evidence_records"] = registry.records()

    if canonical_items:
        canonical_payload["items"] = canonical_items

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
        if key in {"items", "evidence_records"}:
            continue
        consolidated_field = _consolidate_retained_normalized_list(
            canonical_payload.get(key, field_value),
            preferred_labels=preferred_labels,
        )
        if consolidated_field is not None:
            canonical_payload[key] = consolidated_field

    if "evidence_records" in payload or registry.records():
        canonical_payload["evidence_records"] = registry.records()

    return canonical_payload


def extract_evidence_records_from_structured_result(value: Any) -> List[Dict[str, Any]]:
    """Extract normalized evidence records from a structured extraction result."""

    payload = _coerce_evidence_record_dict(canonicalize_structured_result_payload(value))
    if not isinstance(payload, dict):
        return []

    return normalize_evidence_records(payload.get("evidence_records"))


def _coerce_dict_list(value: Any) -> List[Dict[str, Any]]:
    """Coerce a list of dict-like records into plain dictionaries."""

    if not isinstance(value, list):
        return []

    records: List[Dict[str, Any]] = []
    for item in value:
        item_dict = _coerce_evidence_record_dict(item)
        if not isinstance(item_dict, dict):
            return []
        records.append(item_dict)

    return records


def _annotation_item_model(annotation: Any) -> Any:
    """Return the inner model type for list-like annotations when available."""

    origin = get_origin(annotation)
    if origin not in {list, List}:
        return None

    args = get_args(annotation)
    if len(args) != 1:
        return None

    return args[0]


def _field_is_retained_evidence_collection(field_info: Any) -> bool:
    """Whether a schema field represents retained evidence-backed findings."""

    item_model = _annotation_item_model(getattr(field_info, "annotation", None))
    model_fields = getattr(item_model, "model_fields", None)
    if not isinstance(model_fields, dict) or "evidence_record_ids" not in model_fields:
        return False

    description = str(getattr(field_info, "description", "") or "").casefold()
    return not any(marker in description for marker in _NON_RETAINED_COLLECTION_DESCRIPTION_MARKERS)


def _structured_result_retained_collections(
    payload: Dict[str, Any],
    *,
    expected_output_type: Any = None,
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Return evidence-backed retained collections for a structured result payload."""

    schema_fields = getattr(expected_output_type, "model_fields", None)
    if isinstance(schema_fields, dict):
        retained_collections: List[Tuple[str, List[Dict[str, Any]]]] = []
        for field_name, field_info in schema_fields.items():
            if not _field_is_retained_evidence_collection(field_info):
                continue

            field_records = _coerce_dict_list(payload.get(field_name))
            if field_records:
                retained_collections.append((field_name, field_records))

        if retained_collections:
            return retained_collections

    item_records = _coerce_dict_list(payload.get("items"))
    return [("items", item_records)] if item_records else []


def structured_result_missing_evidence_record_refs(value: Any, *, expected_output_type: Any = None) -> bool:
    """Whether retained structured items are missing canonical evidence_record_ids."""

    payload = _coerce_evidence_record_dict(value)
    if not isinstance(payload, dict):
        return False

    if not structured_result_requires_evidence(payload, expected_output_type=expected_output_type):
        return False

    retained_collections = _structured_result_retained_collections(
        payload,
        expected_output_type=expected_output_type,
    )
    if not retained_collections:
        return True

    for _, records in retained_collections:
        for record in records:
            if not _merge_unique_reference_ids(record.get("evidence_record_ids")):
                return True

    return False


def structured_result_requires_evidence(value: Any, *, expected_output_type: Any = None) -> bool:
    """Whether a structured result represents retained extraction findings that require evidence."""

    payload = _coerce_evidence_record_dict(value)
    if not isinstance(payload, dict):
        return False

    if _structured_result_retained_collections(payload, expected_output_type=expected_output_type):
        return True

    run_summary = payload.get("run_summary")
    if isinstance(run_summary, dict):
        kept_count = run_summary.get("kept_count")
        if isinstance(kept_count, int) and not isinstance(kept_count, bool) and kept_count > 0:
            return True

    return False
