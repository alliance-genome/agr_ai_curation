"""Package and prompt policy for the restricted RGD GO paper specialist."""

from pathlib import Path

import yaml

from src.lib.config.agent_loader import load_agent_definitions
from src.lib.config.groups_loader import load_groups
from src.lib.agent_access import is_resource_access_allowed
from src.lib.packages.manifest_loader import load_package_manifest
from src.lib.packages.models import ExportKind


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
    description = _agent().supervisor_routing.description

    assert "authenticated RGD curators only" in description
    assert "IPI/IEP" in description
    assert "call the disease extractor separately" in description
    assert "term definitions" in description
    assert "prior-annotation-only" in description
    assert "persisted extraction-result reference" in description
    assert "Reject blank or unsupported CURIEs locally" in description


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
