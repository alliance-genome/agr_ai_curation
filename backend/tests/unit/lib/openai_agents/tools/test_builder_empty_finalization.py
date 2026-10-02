"""Explicit empty builder finalization, not success inferred from absent output.

Disease and allele carry their own copies of these checks
(test_disease_empty_finalization.py, test_allele_builder_domain_pack.py).
"""

from importlib import import_module

import pytest
from pydantic import ValidationError

from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents.extraction_builder_workspace import (
    ExtractionBuilderWorkspace,
)


# (domain, tools module, finalize input model, agent_id, empty summary fragment)
DOMAINS = [
    ("gene_expression", "agr_curation", "GeneExpressionFinalizeInput",
     "gene_expression_extraction", "no retained observations"),
    ("gene", "gene_builder_tools", "GeneFinalizeInput",
     "gene_extractor", "no retained gene mentions"),
    ("phenotype", "phenotype_builder_tools", "PhenotypeFinalizeInput",
     "phenotype_extractor", "no retained phenotype annotations"),
    ("go", "go_builder_tools", "GOFinalizeInput",
     "rgd_go_paper_curator", "no retained recommendations"),
]


@pytest.fixture(params=DOMAINS, ids=[row[0] for row in DOMAINS])
def domain(request, monkeypatch):
    name, module, input_model, agent_id, summary = request.param
    tools = import_module(f"agr_ai_curation_alliance.tools.{module}")
    state = ExtractionBuilderWorkspace(run_id=f"{name}-empty", agent_id=agent_id)
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: state)
    monkeypatch.setattr(tools, "get_active_evidence_records_snapshot", lambda: [])
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **event: event)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: event)
    return {
        "name": name,
        "tools": tools,
        "workspace": state,
        "finalize": getattr(tools, f"_finalize_{name}_extraction_impl"),
        "tool": getattr(tools, f"finalize_{name}_extraction"),
        "input_model": getattr(tools, input_model),
        "summary": summary,
    }


def test_explicit_empty_finalization_is_durable_and_idempotent(domain):
    workspace, finalize = domain["workspace"], domain["finalize"]
    result = finalize(candidate_ids=[])
    assert result.status == "ok", result
    final = workspace.finalization
    assert final is not None
    assert final.source_candidate_ids == ()
    assert final.evidence_record_ids == ()
    assert final.payload["curatable_objects"] == []
    assert final.payload["run_summary"]["kept_count"] == 0
    assert domain["summary"] in final.payload["summary"]
    assert finalize(candidate_ids=[]).status == "ok"
    assert workspace.finalization is final
    assert finalize(candidate_ids=["different"]).status == "error"
    assert workspace.finalization is final


@pytest.mark.asyncio
async def test_public_finalizer_tool_accepts_explicit_empty_list(domain):
    from agents.tool_context import ToolContext

    tool = domain["tool"]
    assert "minItems" not in tool.params_json_schema["properties"]["candidate_ids"]
    arguments = '{"candidate_ids": []}'
    await tool.on_invoke_tool(
        ToolContext(
            context=None,
            tool_name=tool.name,
            tool_call_id=f"empty-{domain['name']}",
            tool_arguments=arguments,
        ),
        arguments,
    )
    assert domain["workspace"].finalization is not None
    assert domain["workspace"].finalization.payload["curatable_objects"] == []


@pytest.mark.parametrize("bad", [None, [None], [""], [" "], ["missing"], ["same", "same"]])
def test_invalid_or_missing_candidates_do_not_become_empty_success(domain, bad):
    result = domain["finalize"](candidate_ids=bad)
    assert result.lookup_status != "success"
    assert domain["workspace"].finalization is None


def test_candidate_list_is_still_required(domain):
    with pytest.raises(ValidationError):
        domain["input_model"]()


def test_nonempty_candidate_still_requires_evidence(domain):
    workspace = domain["workspace"]
    workspace.upsert_candidate(
        candidate_id="unsupported", staged_fields={}, status="valid"
    )
    result = domain["finalize"](candidate_ids=["unsupported"])
    assert result.lookup_status != "success"
    assert workspace.finalization is None


@pytest.mark.parametrize("name", [row[0] for row in DOMAINS])
def test_only_an_explicitly_empty_selection_materializes_as_no_findings(name):
    conversion = import_module(f"agr_ai_curation_alliance.domain_packs.{name}.conversion")
    materialize = getattr(conversion, f"materialize_{name}_builder_state")
    empty = materialize(
        workspace=ExtractionBuilderWorkspace(run_id=f"empty-{name}"),
        candidate_ids=[], evidence_records=[],
    )
    assert empty.ok, empty.summary()
    assert empty.payload["curatable_objects"] == []
    assert empty.payload["metadata"]["evidence_records"] == []
    assert empty.payload["metadata"]["provenance"]["source_candidate_ids"] == []
    assert empty.payload["run_summary"]["candidate_count"] == 0

    # A nonempty malformed selection must not normalize into an empty success.
    for candidate_ids in ([" "], ["unknown-candidate"]):
        rejected = materialize(
            workspace=ExtractionBuilderWorkspace(run_id=f"invalid-{name}"),
            candidate_ids=candidate_ids, evidence_records=[],
        )
        assert not rejected.ok
        assert rejected.payload is None
