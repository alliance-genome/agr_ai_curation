"""Builder finalize takes every retained record in one call, however many (KANBAN-2100 / ALL-1583).

One finalize call materializes the whole selection into one envelope and finalizes
the workspace; a second call with other IDs is refused. So a per-call cap on
``candidate_ids`` is a cap on how many records one extraction can save. On 19 Nov
2026 a fan-out run staged 55 valid gene-expression records and finalize refused
them ("List should have at most 50 items"). These tests finalize 55 and 120
valid records through each builder domain's public finalize tool (the call the
fan-out makes), and guard every registered builder finalize tool against a
maximum on ``candidate_ids`` coming back.
"""

from __future__ import annotations

import json
from copy import deepcopy
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from agents.tool_context import ToolContext
from agr_ai_curation_alliance.domain_packs.allele.constants import (
    ALLELE_ASSOCIATION_OBJECT_TYPE,
    ALLELE_DOMAIN_PACK_ID,
)
from agr_ai_curation_alliance.domain_packs.disease.constants import DISEASE_DOMAIN_PACK_ID, DISEASE_OBJECT_TYPE
from agr_ai_curation_alliance.domain_packs.gene.constants import (
    GENE_DOMAIN_PACK_ID,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
)
from agr_ai_curation_alliance.domain_packs.gene_expression.constants import GENE_EXPRESSION_DOMAIN_PACK_ID
from agr_ai_curation_alliance.domain_packs.phenotype.constants import PHENOTYPE_DOMAIN_PACK_ID, PHENOTYPE_OBJECT_TYPE

from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents.tools import evidence_workspace


REPO_ROOT = Path(__file__).resolve().parents[6]
BINDINGS_PATH = REPO_ROOT / "packages/alliance/tools/bindings.yaml"
RECORD_COUNTS = (55, 120)


def _allele(index: int) -> dict[str, Any]:
    return {
        "domain_pack_id": ALLELE_DOMAIN_PACK_ID,
        "object_type": ALLELE_ASSOCIATION_OBJECT_TYPE,
        "pending_ref_id": f"allele-mention-{index}",
        "mention": "unc-54(e190)",
        "source_mentions": ["unc-54(e190)"],
        "rationale": "The paper's own paralysis assay characterizes e190, so this allele is curatable here.",
        "associated_gene": "unc-54",
        "taxon": "NCBITaxon:6239",
        "reference_title": "Myosin assembly in C. elegans body-wall muscle",
        "reference_filename": "unc54_paper.pdf",
    }


def _gene(index: int) -> dict[str, Any]:
    return {
        "domain_pack_id": GENE_DOMAIN_PACK_ID,
        "object_type": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
        "pending_ref_id": f"gene-mention-evidence-{index}",
        "mention": "daf-16",
        "confidence": "high",
        "identity_resolution_notes": ["The paper reports a daf-16 nuclear translocation phenotype in C. elegans."],
        "rationale": "This paper's heat-shock assay shows DAF-16 nuclear translocation as a new result.",
        "species": "Caenorhabditis elegans",
        "taxon_hint": "NCBITaxon:6239",
        "data_provider_hint": "WB",
        "proposed_gene_symbol": "daf-16",
        "proposed_taxon": "NCBITaxon:6239",
    }


def _disease(index: int) -> dict[str, Any]:
    return {
        "domain_pack_id": DISEASE_DOMAIN_PACK_ID,
        "object_type": DISEASE_OBJECT_TYPE,
        "pending_ref_id": f"disease-annotation-{index}",
        "mention": "Alzheimer's disease",
        "disease_curie": "DOID:10652",
        "role": "model_context",
        "confidence": "high",
        "data_provider": "FB",
        "subject_type": "gene",
        "subject_identifier": "FB:FBgn0000108",
        "subject_label": "Appl",
        "disease_relation_name": "is_implicated_in",
        "evidence_code_curies": [{"mention": "IMP"}],
        "source_mentions": ["a transgenic Drosophila model of Alzheimer's disease"],
        "rationale": "APP and BACE over-expression reproduced core Alzheimer's features in this line.",
        "negated": False,
    }


def _phenotype(index: int) -> dict[str, Any]:
    return {
        "domain_pack_id": PHENOTYPE_DOMAIN_PACK_ID,
        "object_type": PHENOTYPE_OBJECT_TYPE,
        "pending_ref_id": f"phenotype-annotation-{index}",
        "phenotype_annotation_object": "abnormal sensory cilia morphology",
        "source_mentions": ["sensory cilia were truncated in mutant animals"],
        "subject_identifier": "WB:WBGene00000111",
        "subject_label": "che-2",
        "subject_type": "gene",
        "subject_taxon": "NCBITaxon:6239",
        "term_mention": "truncated sensory cilia",
        "data_provider": "WB",
        "term_taxon_id": "NCBITaxon:6239",
        "rationale": "Amphid cilia were truncated in che-2 mutants, a morphology defect rather than a behaviour.",
        "negated": False,
    }


def _evidence(domain: str, index: int, pending_ref_id: str, field_paths: list[str]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "evidence_record_id": f"evidence-{domain}-{index}",
        "entity": f"{domain} record {index}",
        "verified_quote": f"Observation {index} of this {domain} study was recorded in the results.",
        "chunk_id": f"chunk-{domain}-{index}",
        "document_id": "doc-1",
        "page": 3,
        "section": "Results",
    }
    if field_paths:
        targets = [{"pending_ref_id": pending_ref_id, "field_path": path} for path in field_paths]
        record.update(pending_ref_id=pending_ref_id, field_path=field_paths[0], field_paths=field_paths,
                      envelope_target=targets[0], envelope_targets=targets)
    return record


def _stage_upserted(staged: Callable[[int], dict[str, Any]], domain: str):
    def stage(workspace, count):
        evidence, ids = [], []
        for index in range(1, count + 1):
            fields = staged(index)
            record = _evidence(domain, index, fields["pending_ref_id"], [])
            workspace.upsert_candidate(
                candidate_id=f"{domain}-candidate-{index}",
                staged_fields=fields,
                pending_ref_ids=[fields["pending_ref_id"]],
                evidence_record_ids=[record["evidence_record_id"]],
                status=builder.CANDIDATE_STATUS_VALID,
            )
            evidence.append(record)
            ids.append(f"{domain}-candidate-{index}")
        return evidence, ids

    return stage


_GEX_FIELDS = ["where_expressed_statement", "expression_pattern.where_expressed.anatomical_structure"]


def _stage_gene_expression(workspace, count):
    """Stage through the real stage tool, one evidence record per observation."""

    tools = import_module("agr_ai_curation_alliance.tools.agr_curation")
    stage = tools._unwrap_function_tool_callable(
        tools.stage_gene_expression_observation, "stage_gene_expression_observation")
    evidence = [_evidence("gene_expression", index, f"gene-expression-annotation-{index}", _GEX_FIELDS)
                for index in range(1, count + 1)]
    token = evidence_workspace.set_active_evidence_records(evidence)
    try:
        for index, record in enumerate(evidence, start=1):
            result = stage(
                pending_ref_id=f"gene-expression-annotation-{index}",
                evidence_record_ids=[record["evidence_record_id"]],
                where_expressed_statement=f"PEF-1::GFP expression in the cilium (observation {index})",
                rationale="Anti-GFP staining localizes the reporter to the cilium, not the cell body.",
                data_provider="WB",
                negated=False,
                subject={"mention": "pef-1", "proposed_primary_external_id": None},
                reference={"mention": "PMID:39550471"},
                controlled_fields=[
                    {"field_path": "relation.name", "mention": "is_expressed_in", "proposed_curie": None},
                    {"field_path": "expression_experiment.expression_assay_used", "mention": "GFP reporter",
                     "proposed_curie": None},
                    {"field_path": "expression_pattern.when_expressed.developmental_stage_start",
                     "mention": "L2 larvae", "proposed_curie": None},
                    {"field_path": "expression_pattern.where_expressed.anatomical_structure",
                     "mention": "cilia", "proposed_curie": None},
                ],
            )
            assert result.status == "ok", result
    finally:
        evidence_workspace.reset_active_evidence_records(token)
    return evidence, list(workspace.candidates)


# domain -> (tools module, public finalize tool, workspace domain pack, agent, stager)
DOMAINS = {
    "gene_expression": ("agr_curation", "finalize_gene_expression_extraction", GENE_EXPRESSION_DOMAIN_PACK_ID,
                        "gene_expression_extraction", _stage_gene_expression),
    "allele": ("allele_builder_tools", "finalize_allele_extraction", ALLELE_DOMAIN_PACK_ID,
               "allele_extractor", _stage_upserted(_allele, "allele")),
    "disease": ("disease_builder_tools", "finalize_disease_extraction", DISEASE_DOMAIN_PACK_ID,
                "disease_extractor", _stage_upserted(_disease, "disease")),
    "gene": ("gene_builder_tools", "finalize_gene_extraction", GENE_DOMAIN_PACK_ID,
             "gene_extractor", _stage_upserted(_gene, "gene")),
    "phenotype": ("phenotype_builder_tools", "finalize_phenotype_extraction", PHENOTYPE_DOMAIN_PACK_ID,
                  "phenotype_extractor", _stage_upserted(_phenotype, "phenotype")),
}


@pytest.fixture
def quiet_traces(monkeypatch):
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: event)

    def silence(tools):
        if hasattr(tools, "write_extraction_trace_event"):
            monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **event: event)
        return tools

    return silence


async def _invoke(tool, candidate_ids):
    arguments = json.dumps({"candidate_ids": candidate_ids})
    return await tool.on_invoke_tool(
        ToolContext(context=None, tool_name=tool.name, tool_call_id=f"finalize-{tool.name}",
                    tool_arguments=arguments),
        arguments,
    )


def _issue_reasons(output: Any) -> list[str]:
    data = getattr(output, "data", None)
    if data is None:
        parsed = json.loads(output) if isinstance(output, str) else dict(output)
        data = parsed.get("data") or {}
    return [str(issue.get("reason")) for issue in (data or {}).get("validation_issues", [])]


def test_every_bound_builder_domain_with_staged_records_is_covered():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    domain_finalizers = {
        tool["tool_id"] for tool in bindings["tools"]
        if (tool.get("metadata") or {}).get("builder_output_mode") == "domain"
    }
    assert {row[1] for row in DOMAINS.values()} <= domain_finalizers


@pytest.mark.asyncio
@pytest.mark.parametrize("count", RECORD_COUNTS)
@pytest.mark.parametrize("domain", list(DOMAINS))
async def test_finalize_keeps_every_valid_record_in_one_payload(domain, count, quiet_traces):
    module, tool_name, pack_id, agent_id, stage = DOMAINS[domain]
    tools = quiet_traces(import_module(f"agr_ai_curation_alliance.tools.{module}"))
    workspace = builder.ExtractionBuilderWorkspace(
        run_id=f"{domain}-{count}", document_id="doc-1", domain_pack_id=pack_id, agent_id=agent_id,
    )
    builder_token = builder.set_active_extraction_builder_workspace(workspace)
    try:
        evidence, candidate_ids = stage(workspace, count)
        assert len(candidate_ids) == count
        evidence_token = evidence_workspace.set_active_evidence_records(deepcopy(evidence))
        try:
            output = await _invoke(getattr(tools, tool_name), candidate_ids)
        finally:
            evidence_workspace.reset_active_evidence_records(evidence_token)
    finally:
        builder.reset_active_extraction_builder_workspace(builder_token)

    finalization = workspace.finalization
    assert finalization is not None, _issue_reasons(output)
    assert finalization.source_candidate_ids == tuple(candidate_ids), "all records, in the order sent"
    payload = finalization.payload
    assert payload["metadata"]["provenance"]["source_candidate_ids"] == candidate_ids
    assert (payload["run_summary"]["candidate_count"], payload["run_summary"]["kept_count"]) == (count, count)
    assert set(finalization.evidence_record_ids) == {record["evidence_record_id"] for record in evidence}


def _bound_builder_finalizers() -> list[tuple[str, str]]:
    """Every finalize tool bindings.yaml marks as a builder finalization, with its callable."""

    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    rows = [
        (tool["tool_id"], tool["callable"]) for tool in bindings["tools"]
        if (tool.get("metadata") or {}).get("builder_finalization")
    ]
    assert rows, "bindings.yaml marks no builder finalize tool"
    return rows


def _load_tool(path: str):
    module, attribute = path.split(":")
    return import_module(module), getattr(import_module(module), attribute)


@pytest.mark.parametrize(("tool_id", "callable_path"), _bound_builder_finalizers())
def test_no_builder_finalize_tool_schema_caps_candidate_ids(tool_id, callable_path):
    _module, tool = _load_tool(callable_path)
    candidate_ids = tool.params_json_schema["properties"]["candidate_ids"]
    assert "maxItems" not in candidate_ids, f"{tool_id} caps candidate_ids at {candidate_ids['maxItems']}"


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool_id", "callable_path"), _bound_builder_finalizers())
async def test_no_builder_finalize_input_check_caps_candidate_ids(tool_id, callable_path, quiet_traces):
    """500 unknown IDs are refused only as unknown records, never for the list's size.

    This reaches the tool's own input model as well as its published schema.
    """

    module, tool = _load_tool(callable_path)
    quiet_traces(module)
    workspace = builder.ExtractionBuilderWorkspace(run_id=f"guard-{tool_id}", document_id="doc-1")
    builder_token = builder.set_active_extraction_builder_workspace(workspace)
    evidence_token = evidence_workspace.set_active_evidence_records([])
    try:
        output = await _invoke(tool, [f"unknown-{index}" for index in range(500)])
    finally:
        evidence_workspace.reset_active_evidence_records(evidence_token)
        builder.reset_active_extraction_builder_workspace(builder_token)

    assert workspace.finalization is None
    reasons = _issue_reasons(output)
    assert reasons and set(reasons) == {"unknown_candidate_id"}, (tool_id, output)
