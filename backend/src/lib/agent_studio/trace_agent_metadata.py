"""Registry-backed trace agent metadata helpers."""

from __future__ import annotations

from typing import Any, Mapping

from src.lib.config.agent_loader import canonical_system_agent_key

TRACE_AGENT_ALIASES = {
    "pdf_specialist": "pdf_extraction",
    "pdf": "pdf_extraction",
    "gene_extraction": "gene_extractor",
    "gene_agent": "gene",
    "ask_gene_extractor_": "gene_extractor",
    "ask_gene_extractor_specialist": "gene_extractor",
    "allele_variant_extraction": "allele_extractor",
    "allele_agent": "allele",
    "ask_allele_extractor_": "allele_extractor",
    "ask_allele_extractor_specialist": "allele_extractor",
    "disease_extraction": "disease_extractor",
    "disease_agent": "disease",
    "ask_disease_extractor_": "disease_extractor",
    "ask_disease_extractor_specialist": "disease_extractor",
    "chemical_extraction": "chemical_extractor",
    "chemical_agent": "chemical",
    "ask_chemical_extractor_": "chemical_extractor",
    "ask_chemical_extractor_specialist": "chemical_extractor",
    "phenotype_extraction": "phenotype_extractor",
    "phenotype_specialist": "phenotype_extractor",
    "ask_phenotype_extractor_": "phenotype_extractor",
    "ask_phenotype_": "phenotype_extractor",
    "ask_phenotype_extractor_specialist": "phenotype_extractor",
    "ask_phenotype_specialist": "phenotype_extractor",
    "ontology_term": "ontology_term_validation",
    "ask_ontology_term_specialist": "ontology_term_validation",
}


def get_trace_agent_patterns(
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    registry = agent_registry or _agent_registry()
    patterns: dict[str, str] = {}

    for agent_id, entry in registry.items():
        if agent_id == "task_input":
            continue

        patterns[agent_id] = agent_id

        prompt_key = _prompt_key_for_registry_agent(agent_id)
        if prompt_key:
            patterns[prompt_key] = agent_id

        supervisor = entry.get("supervisor") or {}
        tool_name = str(supervisor.get("tool_name") or "").strip()
        if tool_name:
            patterns[tool_name] = agent_id

    for alias, agent_id in TRACE_AGENT_ALIASES.items():
        normalized_agent_id = patterns.get(agent_id, agent_id)
        if normalized_agent_id in registry:
            patterns[alias] = normalized_agent_id

    return dict(
        sorted(
            patterns.items(),
            key=lambda item: (-len(item[0]), item[0]),
        )
    )


def normalize_trace_agent_id(
    agent_id: str,
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    normalized_id = str(agent_id or "").strip().lower()
    if not normalized_id:
        return normalized_id

    registry = agent_registry or _agent_registry()
    if normalized_id in registry:
        return normalized_id

    for pattern, registry_agent_id in get_trace_agent_patterns(registry).items():
        if pattern == normalized_id:
            return registry_agent_id

    return normalized_id


def trace_agent_display_name(
    agent_id: str,
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    registry = agent_registry or _agent_registry()
    normalized_id = normalize_trace_agent_id(agent_id, registry)
    registry_entry = registry.get(normalized_id)
    if registry_entry:
        registry_name = str(registry_entry.get("name") or "").strip()
        if registry_name:
            return registry_name
    return normalized_id


def _agent_registry() -> Mapping[str, Mapping[str, Any]]:
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

    return AGENT_REGISTRY


def _prompt_key_for_registry_agent(agent_id: str) -> str | None:
    agent_def = _agent_definition_for_registry_agent(agent_id)
    if agent_def is None:
        return None
    return canonical_system_agent_key(agent_def)


def _agent_definition_for_registry_agent(agent_id: str) -> Any | None:
    from src.lib.config.agent_loader import get_agent_by_folder, get_agent_definition

    return get_agent_definition(agent_id) or get_agent_by_folder(agent_id)
