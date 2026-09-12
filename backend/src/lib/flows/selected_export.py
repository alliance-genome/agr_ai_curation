"""Enforce the curator's saved field selection independently of model instructions."""
from typing import Any


def selection_errors(plan: Any, sources: dict[str, dict], output_format: str) -> list[str]:
    errors = []
    if plan.format != output_format or output_format not in {"csv", "tsv", "json"}:
        errors.append("The selected field layout must match this file output format.")
    if plan.row_source != "object" or plan.row_strategy != "wide_union":
        errors.append("Selected fields use one row per item; separate source records are never joined.")
    if not plan.columns or not plan.selected_sources:
        errors.append("Choose at least one source and one output field.")
    if plan.filters or plan.sort or plan.group_by or plan.source_keys or plan.source_extraction_result_ids or plan.max_rows is not None or plan.json_shape != "rows":
        errors.append("Selected fields preserve all source items in order. Remove extra filters, limits or grouping.")
    if plan.missing_value != (None if output_format == "json" else ""):
        errors.append("Missing answers must be null for JSON and blank for CSV/TSV.")
    if len({c.key for c in plan.columns}) != len(plan.columns) or any(not c.key.strip() for c in plan.columns):
        errors.append("Each output field needs a different, nonempty key.")
    selected = {}
    for source in plan.selected_sources:
        catalog = sources.get(source.node_id)
        if source.node_id in selected:
            errors.append("A source was selected more than once.")
        selected[source.node_id] = catalog
        if not catalog or not catalog.get("fields"):
            errors.append(f"Source '{source.node_id}' has no available saved structure. Reconnect or save its extractor first.")
        elif source.schema_fingerprint != catalog.get("schema_fingerprint"):
            errors.append(f"Source '{source.node_id}' changed. Open this file output step, click 'Choose output fields', review and confirm its fields, then save the flow again.")
    headers = set()
    for column in plan.columns:
        catalog = selected.get(column.source_node_id)
        fields = {f["ref"] for f in (catalog or {}).get("fields", [])}
        if column.transform is not None or column.field_ref not in fields:
            errors.append(f"Output field '{column.header or column.key}' must copy a declared field from its selected source.")
        label = (column.header or column.key).strip()
        if not label or label in headers:
            errors.append("Each output column needs a different, nonempty name.")
        headers.add(label)
    return errors


def selected_export_errors(bundle: Any, plan: Any) -> list[str]:
    sources = {artifact.node_id: {
        "schema_fingerprint": artifact.export_schema_fingerprint,
        "fields": [field.model_dump(mode="json") for field in artifact.declared_fields],
    } for artifact in bundle.artifacts}
    errors = selection_errors(plan, sources, plan.format)
    for artifact in bundle.artifacts:
        if artifact.node_id in {source.node_id for source in plan.selected_sources} and not artifact.is_canonical_curation_data:
            errors.append("Selected fields require saved extraction data, not agent-written text.")
    return errors
