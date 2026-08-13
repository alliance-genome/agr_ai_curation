"""Guardrails for supervisor specialist routing configuration."""

from pathlib import Path
import re

from src.lib.config import agent_loader
from src.lib.domain_packs.registry import load_domain_pack_registry
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry

from ..packages import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
SUPERVISOR_PROMPT_PATH = REPO_ROOT / "config" / "agents" / "supervisor" / "prompt.yaml"
ALLIANCE_DOMAIN_PACKS_DIR = REPO_ROOT / "packages" / "alliance" / "domain_packs"
SPECIALIST_TOOL_PATTERN = re.compile(r"ask_[a-z0-9_]+_specialist")

# These config-layer pipeline agents are invoked by flow/curation orchestration,
# not by the supervisor.
CONFIG_PIPELINE_AGENT_IDS = frozenset({"curation_prep", "curation_handoff"})

# Some domain-pack validator bindings intentionally point at public resolver
# specialists so curators can ask the supervisor to resolve the same kind of
# value directly. Keep this exception list tiny and explicit so all other
# bound validators remain non-routable pipeline infrastructure.
SUPERVISOR_ROUTABLE_DOMAIN_VALIDATOR_AGENT_IDS = frozenset({
    "ontology_term_validation",
})


def _load_shipped_agents(monkeypatch, request):
    agent_loader.reset_cache()
    request.addfinalizer(agent_loader.reset_cache)
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    return agent_loader.load_agent_definitions(force_reload=True)


def _extract_supervisor_prompt_tool_names() -> set[str]:
    prompt_text = SUPERVISOR_PROMPT_PATH.read_text(encoding="utf-8")
    return set(SPECIALIST_TOOL_PATTERN.findall(prompt_text))


def _iter_domain_pack_validator_refs():
    registry = load_domain_pack_registry(ALLIANCE_DOMAIN_PACKS_DIR)

    for loaded_pack in registry.loaded_packs:
        validation_registry = DomainPackValidationRegistry.from_domain_pack(loaded_pack)
        for binding in validation_registry.bindings:
            if binding.validator_agent is None:
                continue
            yield {
                "pack_id": loaded_pack.pack_id,
                "state": binding.state.value,
                "source_scope": binding.source_scope,
                "source_object_type": binding.source_object_type,
                "source_field_path": binding.source_field_path,
                "binding_id": binding.binding_id,
                "package_id": binding.validator_agent.package_id,
                "agent_id": binding.validator_agent.agent_id,
            }


def test_supervisor_prompt_references_only_registered_specialist_tools(
    monkeypatch,
    request,
):
    _load_shipped_agents(monkeypatch, request)
    prompt_tool_names = _extract_supervisor_prompt_tool_names()
    registered_tool_names = {
        tool["tool_name"] for tool in agent_loader.get_supervisor_tools()
    }

    assert prompt_tool_names, "Expected supervisor prompt to name specialist tools"
    assert prompt_tool_names <= registered_tool_names, (
        "Supervisor prompt references unregistered specialist tools: "
        f"{sorted(prompt_tool_names - registered_tool_names)}"
    )


def test_pipeline_validators_are_not_supervisor_routable(monkeypatch, request):
    loaded_agents = _load_shipped_agents(monkeypatch, request)
    domain_validator_refs = tuple(_iter_domain_pack_validator_refs())
    assert domain_validator_refs, "Expected Alliance domain packs to declare validators"

    missing_domain_validator_agents = []
    domain_validator_agents = {}
    for validator_ref in domain_validator_refs:
        key = (validator_ref["package_id"], validator_ref["agent_id"])
        agent = agent_loader.get_agent_definition_for_package(*key)
        if agent is None:
            missing_domain_validator_agents.append(validator_ref)
            continue
        domain_validator_agents[key] = agent

    assert not missing_domain_validator_agents, (
        "Domain-pack validator bindings reference unknown agents: "
        f"{missing_domain_validator_agents}"
    )

    pipeline_agents = [
        agent
        for agent in domain_validator_agents.values()
        if agent.agent_id not in SUPERVISOR_ROUTABLE_DOMAIN_VALIDATOR_AGENT_IDS
    ]
    for agent_id in CONFIG_PIPELINE_AGENT_IDS:
        pipeline_agents.append(loaded_agents[agent_id])

    routable_pipeline_agents = {
        agent.agent_id: agent.tool_name
        for agent in pipeline_agents
        if agent.supervisor_routing.enabled
    }

    assert not routable_pipeline_agents, (
        "Pipeline validators must not be supervisor-routable: "
        f"{routable_pipeline_agents}"
    )
