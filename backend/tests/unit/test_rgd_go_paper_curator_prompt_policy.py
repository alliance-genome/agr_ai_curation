"""Package and prompt policy for the restricted RGD GO paper specialist."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import yaml
import pytest

from src.lib.config.agent_loader import load_agent_definitions
from src.lib.config.groups_loader import load_groups
from src.lib.agent_access import is_resource_access_allowed
from src.lib.packages.manifest_loader import load_package_manifest
from src.lib.packages.models import ExportKind
from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents.agents import supervisor_agent


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "packages" / "alliance" / "agents" / "rgd_go_paper_curator"
FIXTURE_PATH = (
    REPO_ROOT
    / "packages"
    / "alliance"
    / "domain_packs"
    / "go"
    / "fixtures"
    / "rgd_paper_curator_cases.yaml"
)


def _agent():
    load_groups(REPO_ROOT / "config" / "groups.yaml", force_reload=True)
    return load_agent_definitions(
        REPO_ROOT / "packages" / "alliance" / "agents", force_reload=True
    )["rgd_go_paper_curator"]


def test_rgd_go_paper_curator_is_registered_and_rgd_restricted():
    agent = _agent()

    assert agent.name == "RGD GO Paper Curator"
    assert agent.category == "Extraction"
    assert agent.requires_document is True
    assert agent.access.allowed_group_ids == ["RGD"]
    assert agent.output_schema is None
    assert agent.curation.adapter_key == "go"
    assert agent.curation.domain_pack_id == "agr.alliance.go"
    assert "agr_literature_reference_lookup" in agent.tools

    manifest = load_package_manifest(
        REPO_ROOT / "packages" / "alliance" / "package.yaml"
    )
    exports = {(item.kind, item.name) for item in manifest.exports}
    assert (ExportKind.AGENT, "rgd_go_paper_curator") in exports
    assert (ExportKind.PROMPT, "rgd_go_paper_curator.system") in exports


def test_rgd_identity_and_annotation_tools_are_group_scoped():
    agent = _agent()

    assert "resolve_gene_product" not in agent.tools
    assert "go_api_call" not in agent.tools
    assert [rule.to_dict() for rule in agent.group_tool_policy.rules] == [
        {
            "tool_id": "resolve_gene_product",
            "allowed_group_ids": ["RGD"],
            "field_paths": ["gene_product"],
        },
        {
            "tool_id": "go_api_call",
            "allowed_group_ids": ["RGD"],
            "field_paths": ["provider_context.existing_annotation_context"],
        },
    ]


def test_rgd_go_paper_curator_denies_non_rgd_authenticated_contexts():
    agent = _agent()

    for active_groups in ([], ["MGI"], ["WB", "ZFIN"]):
        assert not is_resource_access_allowed(
            visibility_allowed=True,
            allowed_group_ids=agent.access.allowed_group_ids,
            active_group_ids=active_groups,
        )
    assert is_resource_access_allowed(
        visibility_allowed=True,
        allowed_group_ids=agent.access.allowed_group_ids,
        active_group_ids=["RGD"],
    )


def test_prompt_requires_grounding_sections_and_canonical_lifecycle():
    prompt = yaml.safe_load((AGENT_DIR / "prompt.yaml").read_text(encoding="utf-8"))[
        "content"
    ]
    normalized_prompt = " ".join(prompt.split())

    for required in (
        "Results, Methods, figure legends, tables",
        "Introduction and Discussion text",
        "never sufficient evidence",
        "resolve_gene_product",
        "agr_literature_reference_lookup",
        "quickgo_api_call",
        "go_api_call",
        "record_evidence",
        "stage_go_recommendation",
        "finalize_go_extraction",
        "Never call `rgd_go_evidence_policy_validation`",
        "general-PDF fallback",
        "Never synthesize an RGD CURIE",
        "completed candidate manifest",
        "GO:<digits>",
        "RGD:<digits>",
        "Reject blank, malformed, or unsupported-prefix",
        "persisted `extraction-result:<uuid>`",
        "active document and requested target scope are unchanged",
        "disease extraction as a separate typed result and policy path",
    ):
        assert required in normalized_prompt

    assert "stage_disease_observation" not in prompt
    assert "finalize_disease_extraction" not in prompt


def test_supervisor_route_is_narrow_and_keeps_combined_domains_separate():
    routing = _agent().supervisor_routing
    description = routing.description

    assert "authenticated RGD curators only" in description
    assert "IPI/IEP" in description
    assert "call the disease extractor separately" in description
    assert "term definitions" in description
    assert "prior-annotation-only" in description
    assert "persisted extraction-result reference" in description
    assert "Reject blank or unsupported CURIEs locally" in description
    assert routing.input_validation == {
        "curie_patterns": {"GO": r"^GO:\d{7}$", "RGD": r"^RGD:\d+$"},
        "unsupported_curie_prefixes": ["DOID", "FB", "MGI", "SGD", "WB", "XB", "ZFIN"],
    }


@pytest.mark.asyncio
async def test_trace_routes_execute_distinct_tools_reuse_results_and_reject_curies(
    monkeypatch,
):
    fixture = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in fixture["cases"]}
    calls = []

    async def _run_specialist_with_events(**kwargs):
        tool_name = kwargs["tool_name"]
        calls.append((tool_name, kwargs["input_text"]))
        adapter_key = "go" if "rgd_go" in tool_name else "disease"
        callback = kwargs.get("validated_handoff_callback")
        if callback is not None:
            sequence = len(calls)
            extraction_result_id = f"00000000-0000-4000-8000-{sequence:012d}"
            callback(
                streaming_tools.SupervisorExtractionHandoff(
                    tool_name=tool_name,
                    specialist_name=kwargs["specialist_name"],
                    result_ref=f"extraction-result:{extraction_result_id}",
                    extraction_result_id=extraction_result_id,
                    result_status="non_empty_extraction_ready",
                    object_count=1,
                    adapter_key=adapter_key,
                    agent_key=(
                        "rgd_go_paper_curator"
                        if adapter_key == "go"
                        else "disease_extractor"
                    ),
                    created_new=True,
                )
            )
        return json.dumps({"adapter_key": adapter_key, "status": "validated"})

    monkeypatch.setattr(
        supervisor_agent, "run_specialist_with_events", _run_specialist_with_events
    )
    ledger = supervisor_agent.SupervisorCallLedger(
        max_total_calls=10,
        max_calls_per_tool=6,
    )
    rgd_tool = cast(Any, supervisor_agent._create_streaming_tool(
        agent=SimpleNamespace(name="RGD GO Paper Curator"),
        tool_name="ask_rgd_go_paper_curator_specialist",
        tool_description="Route RGD paper GO recommendations",
        specialist_name="RGD GO Paper Curator",
        ledger=ledger,
        input_validation=_agent().supervisor_routing.input_validation,
        propagate_errors=False,
    ))
    disease_tool = cast(Any, supervisor_agent._create_streaming_tool(
        agent=SimpleNamespace(name="Disease Extractor"),
        tool_name="ask_disease_extractor_specialist",
        tool_description="Extract disease annotations",
        specialist_name="Disease Extractor",
        ledger=ledger,
        propagate_errors=False,
    ))
    ctx = SimpleNamespace(
        tool_name="automatic_supervisor_trace_replay",
        run_config=None,
    )

    initial_case = cases["trace_initial_ipi"]
    initial_output = await rgd_tool.on_invoke_tool(
        ctx,
        json.dumps({"query": "Assess Cttn for an IPI GO recommendation"}),
    )
    assert initial_case["expect"]["rgd_go_specialist_dispatched"] is True
    assert json.loads(initial_output)["adapter_key"] == "go"

    combined_case = cases["trace_initial_iep_combined_disease"]
    go_output = await rgd_tool.on_invoke_tool(
        ctx,
        json.dumps({"query": "Assess Cttn for an IEP GO recommendation"}),
    )
    disease_output = await disease_tool.on_invoke_tool(
        ctx,
        json.dumps({"query": "Extract the separately requested disease assertions"}),
    )
    assert combined_case["expect"]["disease_specialist_dispatched_separately"] is True
    assert {json.loads(go_output)["adapter_key"], json.loads(disease_output)["adapter_key"]} == {
        "go",
        "disease",
    }

    same_scope_case = cases["trace_follow_up_same_scope"]
    same_scope_query = "Assess Cttn with GO:0005515 in the unchanged paper scope"
    await rgd_tool.on_invoke_tool(ctx, json.dumps({"query": same_scope_query}))
    calls_before_replay = len(calls)
    replay = await rgd_tool.on_invoke_tool(ctx, json.dumps({"query": same_scope_query}))
    assert same_scope_case["expect"]["specialist_redispatch"] is False
    assert len(calls) == calls_before_replay
    assert "inspect_results" in replay
    assert "extraction-result:" in replay

    changed_scope_case = cases["trace_follow_up_changed_scope"]
    await rgd_tool.on_invoke_tool(
        ctx,
        json.dumps({"query": "Assess Ago2 with GO:0005515 in the changed target scope"}),
    )
    assert changed_scope_case["expect"]["specialist_redispatch_allowed"] is True
    assert len(calls) == calls_before_replay + 1

    malformed_case = cases["malformed_curie_local_rejection"]
    calls_before_rejection = len(calls)
    rejected = await rgd_tool.on_invoke_tool(
        ctx,
        json.dumps({"query": "Assess Cttn using DOID:0001 as the GO term"}),
    )
    rejected_payload = json.loads(rejected)
    assert malformed_case["expect"]["specialist_document_tools_called"] is False
    assert rejected_payload["reason"] == "unsupported_curie_prefix"
    assert len(calls) == calls_before_rejection


@pytest.mark.asyncio
@pytest.mark.parametrize("curie", ["GO:", "GO:123", "RGD:", "RGD:abc", "MGI:12345"])
async def test_rgd_tool_rejects_blank_malformed_or_unsupported_curie_before_dispatch(
    monkeypatch,
    curie,
):
    monkeypatch.setattr(
        supervisor_agent,
        "run_specialist_with_events",
        lambda **_kwargs: pytest.fail("invalid CURIE must not start the specialist"),
    )
    tool = cast(Any, supervisor_agent._create_streaming_tool(
        agent=SimpleNamespace(name="RGD GO Paper Curator"),
        tool_name="ask_rgd_go_paper_curator_specialist",
        tool_description="Route RGD paper GO recommendations",
        specialist_name="RGD GO Paper Curator",
        ledger=supervisor_agent.SupervisorCallLedger(
            max_total_calls=2, max_calls_per_tool=2
        ),
        input_validation=_agent().supervisor_routing.input_validation,
        propagate_errors=False,
    ))

    output = await tool.on_invoke_tool(
        SimpleNamespace(tool_name=tool.name, run_config=None),
        json.dumps({"query": f"Assess this identifier: {curie}"}),
    )

    payload = json.loads(output)
    assert payload["status"] == "unresolved"
    assert "general PDF extractor" in payload["message"]


def test_rgd_tool_accepts_supported_curies_with_sentence_punctuation():
    validation = _agent().supervisor_routing.input_validation

    assert (
        supervisor_agent._specialist_query_input_rejection(
            "What about GO:0005515 for RGD:619839?",
            validation,
        )
        is None
    )


def test_fixture_set_covers_named_ambiguity_discovery_and_abstention_cases():
    fixture = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in fixture["cases"]}

    assert set(cases) == {
        "named_cttn",
        "mature_mir_124_3p_ambiguity",
        "distinct_ago_products",
        "bounded_additional_entity",
        "evidence_code_abstention",
        "excluded_section_only",
        "trace_initial_ipi",
        "trace_initial_iep_combined_disease",
        "trace_follow_up_same_scope",
        "trace_follow_up_changed_scope",
        "malformed_curie_local_rejection",
    }
    assert fixture["grounding"]["checked_symbols"] == {
        "Cttn": {"gene_curie": "RGD:619839", "gene_name": "cortactin"},
        "Ago1": {
            "gene_curie": "RGD:1304619",
            "gene_name": "argonaute RISC component 1",
        },
        "Ago2": {
            "gene_curie": "RGD:621255",
            "gene_name": "argonaute RISC catalytic component 2",
        },
    }
    assert cases["mature_mir_124_3p_ambiguity"]["expect"]["gene_product_curie_absent"]
    assert cases["evidence_code_abstention"]["expect"][
        "invented_evidence_code_forbidden"
    ]
    assert cases["excluded_section_only"]["expect"]["recommendation_finalized"] is False
    assert cases["trace_initial_ipi"]["expect"]["go_result_adapter_key"] == "go"
    assert cases["trace_initial_iep_combined_disease"]["expect"][
        "disease_specialist_dispatched_separately"
    ]
    assert cases["trace_follow_up_same_scope"]["expect"] == {
        "inspect_prior_result": True,
        "specialist_redispatch": False,
        "broad_pdf_fallback_forbidden": True,
    }
    assert cases["trace_follow_up_changed_scope"]["expect"][
        "specialist_redispatch_allowed"
    ]
    assert cases["malformed_curie_local_rejection"]["expect"][
        "specialist_document_tools_called"
    ] is False
