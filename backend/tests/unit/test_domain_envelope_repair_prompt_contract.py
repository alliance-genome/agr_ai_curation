"""Prompt contract checks for repair-free target extractor schemas."""

import re
from pathlib import Path
from typing import Any

import yaml

from src.lib.prompts import assembly
from src.lib.openai_agents import models as agent_models
from src.schemas.models import DomainEnvelopeExtractionResult


REPO_ROOT = Path(__file__).resolve().parents[3]

EXTRACTOR_PROMPTS = [
    "packages/alliance/agents/allele_extractor/prompt.yaml",
    "packages/alliance/agents/disease_extractor/prompt.yaml",
    "packages/alliance/agents/gene_expression/prompt.yaml",
    "packages/alliance/agents/gene_extractor/prompt.yaml",
    "packages/alliance/agents/phenotype_extractor/prompt.yaml",
]

# Builder-pattern extractors (gene/allele/disease/phenotype/gene_expression) carry
# no envelope output schema; the backend builds their envelope from staged builder
# state. ``None`` here means "builder agent", and the test below resolves the bound
# schema only when one is declared.
EXTRACTOR_OUTPUT_SCHEMAS = {
    "packages/alliance/agents/allele_extractor/agent.yaml": None,
    "packages/alliance/agents/disease_extractor/agent.yaml": None,
    "packages/alliance/agents/gene_expression/agent.yaml": None,
    "packages/alliance/agents/gene_extractor/agent.yaml": None,
    "packages/alliance/agents/phenotype_extractor/agent.yaml": None,
}

# Non-builder, extraction-only tools every extractor may declare regardless of
# pattern. Builder verbs (stage_/patch_/discard_/list_staged_/finalize_*) are
# validated separately by shape so a newly migrated domain stays in scope
# without editing this set.
EXTRACTION_SAFE_TOOLS = {
    "search_document",
    "read_chunk",
    "read_section",
    "read_subsection",
    "record_evidence",
    "list_recorded_evidence",
    "get_recorded_evidence",
    "attach_evidence_to_object",
    "detach_evidence_from_object",
    "discard_recorded_evidence",
    "update_recorded_evidence_metadata",
    "get_agent_contract",
    "agr_species_context_lookup",
    "search_domain_field_terms",
    "inspect_ontology_term",
    "resolve_domain_field_term",
}

# Builder tool-loop verb prefixes. Any tool matching one of these prefixes is an
# in-scope builder verb for an extraction agent; matching on prefix (rather than
# a hardcoded per-domain noun) keeps the contract builder-generic.
BUILDER_TOOL_VERB_PREFIXES = (
    "stage_",
    "patch_",
    "discard_",
    "list_staged_",
    "finalize_",
)


def _is_extraction_scoped_tool(tool_id: str) -> bool:
    if tool_id in EXTRACTION_SAFE_TOOLS:
        return True
    return any(tool_id.startswith(prefix) for prefix in BUILDER_TOOL_VERB_PREFIXES)

FORBIDDEN_EXTRACTOR_METADATA_PHRASES = (
    "database-assisted normalization",
    "database assisted normalization",
    "database normalization",
    "curation database normalization",
)

FORBIDDEN_EXTRACTOR_TOOLS = {
    "agr_curation_query",
    "curation_db_sql",
    "chebi_api_call",
    "agr_literature_reference_lookup",
    "quickgo_api_call",
    "go_api_call",
    "alliance_api_call",
}

FORBIDDEN_TARGET_REPAIR_FRAGMENTS = [
    "repair_action",
    "repair_hints",
    "repair_notes",
    "repair_mode",
    "repair-only",
    "repair_patch",
    "repair_result",
    "repair_request",
    "repair_history",
    "repair_requested",
    "repair_patch_accepted",
    "repair_patch_rejected",
    "repair_final_classified",
    "ExtractorRepairResponse",
    "extractor_patch",
]

# Authoring metadata can still carry policy-only repair markers such as
# metadata.repair or repairable; runtime result event keys are checked separately.
FORBIDDEN_TARGET_REPAIR_METADATA_KEYS = frozenset(
    {
        "repair",
        "repair_action",
        "repair_hints",
        "repair_notes",
        "repair_mode",
        "repair_patch",
        "repair_result",
        "repair_request",
        "repair_history",
        "repair_requested",
        "repairable",
        "extractor_patch",
    }
)

FORBIDDEN_TARGET_REPAIR_METADATA_TEXT = (
    "mark_under_development",
    "repair_action",
    "extractor_patch",
    "repair mode",
    "repair-only",
)


def _content(relative_path: str) -> str:
    path = REPO_ROOT / relative_path
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    content = data.get("content")
    assert isinstance(content, str)
    return content


def _runtime_content(relative_path: str) -> str:
    prompt_path = REPO_ROOT / relative_path
    agent_yaml = prompt_path.with_name("agent.yaml")
    agent_data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
    assert isinstance(agent_data, dict)
    agent_id = str(agent_data["agent_id"])
    return "\n\n".join(
        [
            assembly.build_agent_core_prompt(agent_id).render(),
            _content(relative_path),
        ]
    )


def _yaml(relative_path: str) -> dict:
    data = yaml.safe_load((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _yaml_paths(*patterns: str) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(REPO_ROOT.glob(pattern))
    return sorted(paths)


def _collect_forbidden_metadata_surfaces(
    value: Any,
    *,
    path: tuple[str, ...],
) -> list[str]:
    violations: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = (*path, key_text)
            if key_text in FORBIDDEN_TARGET_REPAIR_METADATA_KEYS:
                violations.append(".".join(child_path))
            violations.extend(
                _collect_forbidden_metadata_surfaces(child, path=child_path)
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(
                _collect_forbidden_metadata_surfaces(
                    child,
                    path=(*path, f"[{index}]"),
                )
            )
    elif isinstance(value, str):
        normalized_value = value.lower()
        for fragment in FORBIDDEN_TARGET_REPAIR_METADATA_TEXT:
            if fragment in normalized_value:
                violations.append(".".join(path))
    return violations


def test_extractor_prompts_do_not_expose_repair_surfaces():
    for relative_path in EXTRACTOR_PROMPTS:
        content = _content(relative_path)
        normalized_content = re.sub(r"\s+", " ", content)

        for fragment in FORBIDDEN_TARGET_REPAIR_FRAGMENTS:
            assert fragment not in normalized_content, f"{relative_path} contains {fragment}"


def test_extractor_prompts_delegate_unresolved_state_to_validators():
    required_fragments = [
        "Active validator binding",
        "validator-bound unresolved candidates",
        "Active validator bindings own",
        "validator result fields",
        "envelope validation findings",
    ]

    for relative_path in EXTRACTOR_PROMPTS:
        content = _runtime_content(relative_path)
        normalized_content = re.sub(r"\s+", " ", content).lower()
        for fragment in required_fragments:
            assert fragment.lower() in normalized_content, f"{relative_path} missing {fragment}"


def test_extractor_agents_use_plain_extraction_result_schemas():
    for relative_path, schema_name in EXTRACTOR_OUTPUT_SCHEMAS.items():
        agent_payload = _yaml(relative_path)
        # Builder agents bind no output schema (None); envelope agents bind a
        # plain extraction-result schema that must never be a repair response.
        assert agent_payload["output_schema"] == schema_name
        if schema_name is None:
            continue

        schema_cls = getattr(agent_models, schema_name)
        assert any(_b.__qualname__ == DomainEnvelopeExtractionResult.__qualname__ for _b in type.mro(schema_cls))
        assert not getattr(
            schema_cls,
            "__domain_envelope_extractor_repair_response__",
            False,
        )


def test_extractor_agent_metadata_and_tools_stay_extraction_scoped():
    for relative_path in EXTRACTOR_OUTPUT_SCHEMAS:
        agent_payload = _yaml(relative_path)
        tools = set(agent_payload.get("tools") or [])
        # Every declared tool is either an extraction-only tool or a builder
        # tool-loop verb (stage_/patch_/discard_/list_staged_/finalize_*). This
        # stays builder-generic: a newly migrated domain's verbs are accepted by
        # shape rather than needing to be enumerated here.
        out_of_scope = {tool for tool in tools if not _is_extraction_scoped_tool(tool)}
        assert out_of_scope == set(), f"{relative_path} declares non-extraction tools: {out_of_scope}"
        assert tools.isdisjoint(FORBIDDEN_EXTRACTOR_TOOLS)

        supervisor_description = (
            agent_payload.get("supervisor_routing", {}).get("description") or ""
        )
        metadata_text = " ".join(
            [
                str(agent_payload.get("description") or ""),
                str(supervisor_description),
            ]
        )
        normalized_metadata = re.sub(r"\s+", " ", metadata_text).lower()
        for phrase in FORBIDDEN_EXTRACTOR_METADATA_PHRASES:
            assert phrase not in normalized_metadata, (
                f"{relative_path} exposes validator-owned normalization as "
                "extractor metadata"
            )


def test_extractor_group_rules_do_not_expose_repair_surfaces():
    group_rule_paths = _yaml_paths(
        "packages/alliance/agents/*_extractor/group_rules/*.yaml",
        "packages/alliance/agents/gene_expression/group_rules/*.yaml",
    )
    assert group_rule_paths

    for path in group_rule_paths:
        content = str(yaml.safe_load(path.read_text(encoding="utf-8"))["content"])
        normalized_content = re.sub(r"\s+", " ", content)
        for fragment in FORBIDDEN_TARGET_REPAIR_FRAGMENTS:
            assert fragment not in normalized_content, f"{path} contains {fragment}"


def test_domain_pack_metadata_omits_repair_only_policy():
    metadata_paths = _yaml_paths("packages/alliance/domain_packs/*/domain_pack.yaml")
    assert metadata_paths

    for path in metadata_paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        violations = _collect_forbidden_metadata_surfaces(payload, path=(str(path),))
        assert violations == []


def test_validator_dispatch_fixtures_omit_repair_only_policy():
    fixture_paths = _yaml_paths(
        "backend/tests/fixtures/domain_packs/**/*.yaml",
        "packages/alliance/domain_packs/*/fixtures/*.yaml",
    )
    assert fixture_paths

    for path in fixture_paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        violations = _collect_forbidden_metadata_surfaces(payload, path=(str(path),))
        assert violations == []
