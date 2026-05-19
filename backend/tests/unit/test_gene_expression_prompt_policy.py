from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.lib.config import schema_discovery
from src.lib.config.agent_sources import resolve_agent_config_sources


def _repo_root() -> Path:
    # backend/tests/unit/<this_file>.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def _load_gene_expression_source():
    return next(
        source
        for source in resolve_agent_config_sources(_repo_root() / "packages")
        if source.folder_name == "gene_expression"
    )


def _load_tmem67_output() -> dict:
    fixture_path = (
        _repo_root()
        / "backend"
        / "tests"
        / "fixtures"
        / "domain_packs"
        / "gene_expression"
        / "tmem67_gene_expression_output.yaml"
    )
    return yaml.safe_load(fixture_path.read_text(encoding="utf-8"))["output"]


def _load_gene_expression_schema():
    schema_discovery.reset_cache()
    schema_discovery.discover_agent_schemas(
        _repo_root() / "packages",
        force_reload=True,
    )
    schema = schema_discovery.get_schema_for_agent("gene_expression")
    assert schema is not None
    return schema


def test_gene_expression_prompt_includes_daniela_policy_gates():
    source = _load_gene_expression_source()
    prompt_path = source.prompt_yaml
    assert prompt_path is not None
    data = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")
    agent_data = yaml.safe_load(source.agent_yaml.read_text(encoding="utf-8"))

    assert agent_data["tools"] == [
        "search_document",
        "read_section",
        "read_subsection",
        "record_evidence",
        "get_agent_contract",
        "agr_species_context_lookup",
    ]

    assert "Return JSON only, matching GeneExpressionEnvelope." in content
    assert "previously_reported" in content
    assert "non_experimental_claim" in content
    assert "marker_only_visualization" in content
    assert "promoter_driven_marker_localization" in content
    assert "mutant_background_only" in content
    assert "structural_label_or_fusion_only" in content
    assert "Capture reagent genotype strings exactly as written" in content
    assert "midbrain-hindbrain boundary at 18 hpf" in content
    assert "Tg(kdrl:EGFP)" in content
    assert "object_type` - always `GeneExpressionAnnotation" in content
    assert "metadata.evidence_records[]" in content
    assert "Do not place `evidence_text`" in content
    assert "payload.evidence_text" not in content
    assert "anatomy_label" not in content
    assert "life_stage_label" not in content
    assert "go_cc_label" not in content
    assert "is_negative" not in content
    assert "negated: true" in content
    assert "repair_mode" not in content
    assert "metadata.repair_notes" not in content
    assert "Do not emit top-level `items[]`" in content
    assert "agr_species_context_lookup" in content
    assert "Do not perform extraction-time relation vocabulary lookup" in content
    assert "`relation.name` to `is_expressed_in`" in content
    assert "`data_provider.abbreviation`" in content
    assert '"data_provider": {"abbreviation": "ZFIN"}' in content
    assert "zebrafish / Danio rerio => `ZFIN`" in content
    assert "expression ontology lookup" in content
    assert "agr_curation_query" not in content


def test_gene_expression_wb_overlay_includes_wormbase_examples():
    wb_path = next(
        path
        for path in _load_gene_expression_source().group_rule_files
        if path.stem == "wb"
    )
    data = yaml.safe_load(wb_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")

    assert "dendrite` over `dendritic tree" in content
    assert "F49H12.4p::GFP" in content
    assert "SAX-7/MNR-1" in content
    assert "TIAM-1::GFP" in content
    assert "tagRFP::TBA-1" in content
    assert "UtrCH" in content
    assert "evidence_text" not in content
    assert "anatomy_label" not in content
    assert "life_stage_label" not in content
    assert "is_negative" not in content
    assert "negated: true" in content
    assert "agr_curation_query" not in content


def test_gene_expression_zfin_overlay_includes_zebrafish_curation_rules():
    zfin_path = next(
        path
        for path in _load_gene_expression_source().group_rule_files
        if path.stem == "zfin"
    )
    data = yaml.safe_load(zfin_path.read_text(encoding="utf-8"))
    content = str(data.get("content") or "")

    assert "ZFA-compatible anatomy label" in content
    assert "ZFS-compatible stage labels" in content
    assert "fgf8a" in content
    assert "Tg(kdrl:EGFP)" in content
    assert "morpholino" in content
    assert "rescue_experiment_not_expression" in content
    assert "is_negative" not in content
    assert "negated: true" in content


def test_gene_expression_schema_accepts_tmem67_domain_envelope_output():
    schema = _load_gene_expression_schema()

    envelope = schema.model_validate(_load_tmem67_output())

    assert envelope.curatable_objects[0].object_type == "GeneExpressionAnnotation"
    assert envelope.curatable_objects[0].model_ref == "GeneExpressionAnnotationPayload"
    assert envelope.metadata.evidence_records[0].evidence_record_id == (
        "evidence-tmem67-metanephros-1"
    )


def test_gene_expression_schema_canonicalizes_missing_pending_ref_id():
    schema = _load_gene_expression_schema()
    payload = deepcopy(_load_tmem67_output())
    payload["curatable_objects"][0].pop("pending_ref_id", None)

    envelope = schema.model_validate(payload)

    assert envelope.curatable_objects[0].pending_ref_id.startswith(
        "gene-expression-annotation-"
    )
    assert envelope.curatable_objects[0].schema_ref.version == (
        "1b11d0888f19eba4ca72022200bb7d96b30d4a52"
    )


def test_gene_expression_schema_rejects_legacy_payload_evidence_fields():
    schema = _load_gene_expression_schema()
    payload = deepcopy(_load_tmem67_output())
    payload["curatable_objects"][0]["payload"]["evidence_text"] = "legacy payload quote"

    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate(payload)

    assert "metadata.evidence_records[]" in str(exc_info.value)


def test_gene_expression_schema_rejects_null_relation_name():
    schema = _load_gene_expression_schema()
    payload = deepcopy(_load_tmem67_output())
    payload["curatable_objects"][0]["payload"]["relation"]["name"] = None

    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate(payload)

    assert "relation.name must be is_expressed_in" in str(exc_info.value)


def test_gene_expression_schema_rejects_null_data_provider_abbreviation():
    schema = _load_gene_expression_schema()
    payload = deepcopy(_load_tmem67_output())
    payload["curatable_objects"][0]["payload"]["data_provider"]["abbreviation"] = None

    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate(payload)

    assert "data_provider.abbreviation must be a non-empty" in str(exc_info.value)


def test_gene_expression_schema_rejects_non_annotation_curatable_objects():
    schema = _load_gene_expression_schema()
    payload = deepcopy(_load_tmem67_output())
    payload["curatable_objects"][0]["object_type"] = "Gene"

    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate(payload)

    assert "GeneExpressionAnnotation" in str(exc_info.value)


@pytest.mark.parametrize(
    ("location", "field_name", "value"),
    (
        ("object", "repair_hints", ["legacy repair hint"]),
        ("metadata", "repair_notes", ["legacy repair note"]),
        ("top_level", "repair_mode", True),
    ),
)
def test_gene_expression_schema_rejects_repair_surfaces(
    location: str,
    field_name: str,
    value: object,
):
    schema = _load_gene_expression_schema()
    payload = deepcopy(_load_tmem67_output())
    if location == "object":
        payload["curatable_objects"][0][field_name] = value
    elif location == "metadata":
        payload["metadata"][field_name] = value
    else:
        payload[field_name] = value

    with pytest.raises(ValidationError):
        schema.model_validate(payload)
