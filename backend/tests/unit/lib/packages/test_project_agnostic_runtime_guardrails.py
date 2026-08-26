"""Project-agnostic guardrails for non-Alliance runtime package testing."""

from __future__ import annotations

import ast
import builtins
import re
import shutil
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest  # type: ignore[reportMissingImports]
import yaml
import src.lib.curation_workspace.adapter_registry as adapter_registry_module
import src.lib.domain_packs.registry as domain_pack_registry_module
from src.lib.agent_studio import flow_tools, runtime_validation
from src.lib.agent_studio.registry_builder import build_agent_registry
from src.lib.config import (
    agent_loader,
    agent_sources,
    models_loader,
    package_default_sources,
    prompt_loader,
    providers_loader,
    schema_discovery,
)
from src.lib.config.tool_policy_defaults_loader import load_tool_policy_defaults
from src.lib.curation_workspace.adapter_registry import build_curation_adapter_registry
from src.lib.curation_workspace.export_adapters.registry import ExportAdapterRegistry
from src.lib.document_sources.registry import (
    get_configured_document_source_dev_mode_static_curator_token,
    get_configured_document_source_provider,
    get_document_source_provider_metadata,
)
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.flows.output_projection import build_flow_output_artifact_bundle
from src.lib.openai_agents.config import get_model_for_agent, resolve_model_provider
from src.lib.packages.registry import load_package_registry
from src.lib.packages.flow_recipes import load_flow_recipe_catalog
from src.lib.packages.document_source_provider_loader import (
    load_document_source_provider_catalog,
)
from src.lib.packages.tool_registry import load_tool_registry
from src.schemas.curation_workspace import SubmissionMode

from . import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
FIXTURES_DIR = Path(__file__).parent / "fixtures"
ORG_CUSTOM_FIXTURE = FIXTURES_DIR / "org_custom_runtime"
GUARDRAIL_TEST_PATH = Path("backend/tests/unit/lib/packages/test_project_agnostic_runtime_guardrails.py")
AGENT_STUDIO_OPUS_TOOLS_PATH = Path("backend/src/api/agent_studio_opus_tools.py")

ALLIANCE_LITERAL_PATTERNS = (
    re.compile(r"agr\.alliance"),
    re.compile(r"agr_curation_query"),
    re.compile(r"alliance_api_call"),
    re.compile(r"alliancegenome"),
    re.compile(r"alliance-api"),
    re.compile(r"\b(?:FB|WB|MGI|RGD|SGD|ZFIN|HGNC)\b"),
)

GENERIC_RUNTIME_GUARD_PATHS = {
    Path("backend/tests/unit/lib/agent_studio/test_runtime_validation.py"),
    Path("backend/tests/unit/lib/config/test_package_aware_loaders.py"),
}
GENERIC_RUNTIME_SOURCE_GUARD_PATHS = {
    Path("backend/src/lib/agent_studio/diagnostic_tools/tool_definitions.py"),
    Path("backend/src/lib/agent_studio/catalog_service.py"),
    Path("backend/src/lib/agent_studio/flow_tools.py"),
    Path("backend/src/lib/config/agent_loader.py"),
    Path("backend/src/lib/curation_workspace/adapter_registry.py"),
    Path("backend/src/lib/openai_agents/config.py"),
    Path("backend/src/lib/document_sources/registry.py"),
    Path("backend/src/lib/flows/output_projection.py"),
    Path("backend/src/lib/openai_agents/runner.py"),
    Path("backend/src/lib/openai_agents/streaming_tools.py"),
    Path("backend/src/lib/packages/flow_recipes.py"),
    Path("backend/src/lib/packages/document_source_provider_loader.py"),
    Path("backend/src/lib/packages/identifier_prefix_provider_loader.py"),
    Path("backend/src/lib/pdf_jobs/upload_intake_service.py"),
    Path("backend/src/lib/runtime_entrypoint.py"),
    Path("packages/core/config/agent_studio_system_prompt.md"),
}
GENERIC_RUNTIME_SOURCE_PATTERNS = (
    re.compile(r"agr\.alliance"),
    re.compile(r"agr_curation_query"),
    re.compile(r"alliance_api_call"),
    re.compile(r"alliancegenome"),
    re.compile(r"abc_literature"),
    re.compile(r"ABC Literature"),
    re.compile(r"agr_ai_curation_alliance"),
    re.compile(
        r"\b(?:crossreference|ontologyterm|biologicalentity|"
        r"referencedcurie|primaryexternalid)\b"
    ),
    re.compile(
        r"\b(?:allele_candidates|gene_candidates|agm_candidates|"
        r"ontology_term_candidates|gene_id|gene_symbol|go_id)\b"
    ),
    re.compile(r"\b(?:FB|WB|MGI|RGD|SGD|ZFIN|HGNC)\b"),
)
GENERIC_FLOW_RECIPE_SOURCE_GUARD_PATHS = {
    Path("backend/src/lib/agent_studio/flow_tools.py"),
    Path("backend/src/lib/packages/flow_recipes.py"),
}
GENERIC_FLOW_RECIPE_SOURCE_PATTERNS = (
    re.compile(r"\balliance\b", re.IGNORECASE),
    re.compile(
        r"\b(?:gene|gene_validation|gene_expression|gene_ontology|allele|"
        r"allele_validation|disease|disease_validation|chemical|"
        r"chemical_validation|phenotype_extractor)\b",
        re.IGNORECASE,
    ),
)
GENERIC_RUNTIME_PLACEHOLDER_PATTERNS = (
    re.compile(r"agr\.alliance"),
    re.compile(r"agr_curation_query"),
    re.compile(r"alliance_api_call"),
    re.compile(r"alliancegenome"),
    re.compile(r"(?<![A-Za-z0-9_])gene(?![A-Za-z0-9_])"),
    re.compile(r"\b(?:FB|WB|MGI|RGD|SGD|ZFIN|HGNC)\b"),
)

ALLOWED_ALLIANCE_TEST_PATHS = {
    # Package-aware forward reconciliation for the Alliance-owned tool policy.
    Path("backend/tests/unit/test_alliance_tool_policy_reconciliation_migration.py"),
    # Bundled Alliance package contracts and prompt/tool policy coverage.
    Path("backend/tests/integration/persistence/test_validator_agent_identity_migration.py"),
    Path("backend/tests/unit/test_config_loaders.py"),
    Path("backend/tests/unit/test_gene_allele_validator_result_contract.py"),
    Path("backend/tests/unit/test_subject_entity_validator_result_contract.py"),
    Path("backend/tests/unit/test_disease_extractor_domain_envelope_contract.py"),
    Path("backend/tests/unit/test_domain_envelope_repair_prompt_contract.py"),
    Path("backend/tests/unit/test_gene_extractor_domain_envelope_contract.py"),
    Path("backend/tests/unit/test_gene_expression_prompt_policy.py"),
    Path("backend/tests/unit/test_phenotype_extractor_domain_envelope_contract.py"),
    Path("backend/tests/unit/test_allele_extractor_mgi_prompt_policy.py"),
    Path("backend/tests/unit/lib/config/test_agent_access.py"),
    Path("backend/tests/unit/lib/config/test_bundled_alliance_package_aware_loaders.py"),
    Path("backend/tests/unit/lib/config/test_controlled_vocabulary_validation_agent.py"),
    Path("backend/tests/unit/lib/config/test_data_provider_validation_agent.py"),
    Path("backend/tests/unit/lib/config/test_disease_chemical_validator_result_contract.py"),
    Path("backend/tests/unit/lib/config/test_experimental_condition_validation_agent.py"),
    Path("backend/tests/unit/lib/config/test_groups_loader_identity_provider.py"),
    Path("backend/tests/unit/lib/config/test_ontology_term_validator_contract.py"),
    Path("backend/tests/unit/lib/config/test_prompt_loader_runtime.py"),
    Path("backend/tests/unit/lib/packages/test_identifier_prefix_provider_loader.py"),
    Path("backend/tests/unit/lib/config/test_reference_validator_result_contract.py"),
    Path("backend/tests/unit/lib/config/test_runtime_config_defaults.py"),
    Path("backend/tests/unit/lib/packages/__init__.py"),
    Path("backend/tests/unit/lib/packages/test_agent_studio_prompt_loader.py"),
    Path("backend/tests/unit/lib/packages/alliance/test_agent_studio_diagnostics.py"),
    Path("backend/tests/unit/lib/packages/alliance/test_go_annotations_adapter.py"),
    Path("backend/tests/unit/lib/packages/test_alliance_agent_package.py"),
    Path("backend/tests/unit/lib/packages/test_alliance_literature_reference_tool.py"),
    Path("backend/tests/unit/lib/packages/test_core_package_contract.py"),
    Path("backend/tests/unit/lib/packages/test_flow_recipes.py"),
    Path("backend/tests/unit/lib/packages/test_manifest_loader.py"),
    Path("backend/tests/unit/lib/packages/test_package_runner.py"),
    Path("backend/tests/unit/lib/packages/test_registry.py"),
    Path("backend/tests/unit/lib/packages/test_tool_registry.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_agr_curation_helpers.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_agr_curation_provider_config.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_agr_curation_query_paths.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_alliance_agr_curation_data_provider_helpers.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_alliance_agr_curation_vocabulary_helpers.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_alliance_agr_lookup_helpers.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_backend_tool_surface_project_agnostic.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_gene_expression_builder_tools.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_search_helpers.py"),
    Path("backend/tests/unit/lib/openai_agents/tools/test_span_evidence_gene_expression_regression.py"),
    Path("backend/tests/unit/lib/openai_agents/agents/test_supervisor_agent_runtime.py"),
    Path("backend/tests/unit/lib/openai_agents/test_evidence_summary.py"),
    Path("backend/tests/unit/lib/openai_agents/test_extraction_trace_event_writer.py"),
    Path("backend/tests/unit/lib/openai_agents/test_runner_streamed_paths.py"),
    Path("backend/tests/unit/lib/openai_agents/test_streaming_tools_groq_compat.py"),
    Path("backend/tests/unit/lib/openai_agents/test_streaming_tools_helpers.py"),
    Path("backend/tests/unit/lib/openai_agents/test_tool_call_policy.py"),
    Path("backend/tests/unit/lib/openai_agents/test_tool_event_friendly_name_contract.py"),
    Path("backend/tests/unit/lib/test_identifier_validation.py"),
    Path("backend/tests/unit/lib/test_runtime_payload_budget.py"),
    Path("backend/tests/unit/lib/test_runtime_entrypoint.py"),
    Path("backend/tests/unit/lib/test_weaviate_documents_runtime.py"),
    Path("backend/tests/unit/lib/prompts/test_cache_core.py"),
    Path("backend/tests/unit/lib/prompts/test_cache_overrides.py"),
    Path("backend/tests/unit/lib/prompts/test_assembly_callsite_parity.py"),
    Path("backend/tests/unit/lib/prompts/test_assembly.py"),
    Path("backend/tests/unit/lib/prompts/test_context_tracking.py"),
    # Phase C includes explicit Alliance group-render retention contracts.
    Path("backend/tests/unit/lib/prompts/test_phase_c_rewrite_guards.py"),
    Path("backend/tests/unit/lib/prompts/test_service_core.py"),
    Path("backend/tests/unit/lib/agent_studio/test_agent_service.py"),
    Path("backend/tests/unit/lib/agent_studio/test_catalog_service_branches.py"),
    Path("backend/tests/unit/lib/agent_studio/test_catalog_service_prompt_keys.py"),
    Path("backend/tests/unit/lib/agent_studio/test_catalog_service_tool_bindings.py"),
    Path("backend/tests/unit/lib/agent_studio/test_custom_agent_service.py"),
    Path("backend/tests/unit/lib/agent_studio/test_custom_agent_service_branches.py"),
    Path("backend/tests/unit/lib/agent_studio/test_domain_envelope_tools.py"),
    Path("backend/tests/unit/lib/agent_studio/test_flow_tools.py"),
    Path("backend/tests/unit/lib/agent_studio/test_hybrid_tool_registry.py"),
    Path("backend/tests/unit/lib/agent_studio/test_registry_builder.py"),
    Path("backend/tests/unit/lib/agent_studio/test_suggestion_service.py"),
    Path("backend/tests/unit/lib/agent_studio/test_system_agent_sync.py"),
    Path("backend/tests/unit/lib/agent_studio/test_trace_context_service.py"),
    # API, schema, auth, curation workspace, and flow tests using shipped data.
    Path("backend/tests/unit/api/test_admin_prompts_api.py"),
    Path("backend/tests/unit/api/test_agent_studio_agent_test.py"),
    Path("backend/tests/unit/api/test_agent_studio_catalog_endpoints.py"),
    Path("backend/tests/unit/api/test_agent_studio_chat_debug_metadata.py"),
    Path("backend/tests/unit/api/test_agent_studio_custom.py"),
    Path("backend/tests/unit/api/test_agent_studio_metadata.py"),
    Path("backend/tests/unit/api/test_agent_studio_phase2_endpoints.py"),
    Path("backend/tests/unit/api/test_agent_studio_phase3_endpoints.py"),
    Path("backend/tests/unit/api/test_agent_studio_tools_endpoints.py"),
    Path("backend/tests/unit/api/test_agent_studio_trace_tools.py"),
    Path("backend/tests/unit/api/test_auth_api_endpoints.py"),
    Path("backend/tests/unit/api/test_chat_execute_flow_endpoint.py"),
    Path("backend/tests/unit/api/test_documents_download_endpoint.py"),
    Path("backend/tests/unit/api/test_documents_runtime_endpoints.py"),
    Path("backend/tests/unit/api/test_flows_api.py"),
    Path("backend/tests/unit/lib/alerts/test_tool_failure_notifier.py"),
    Path("backend/tests/unit/lib/curation_workspace/test_extraction_results.py"),
    Path("backend/tests/unit/lib/curation_workspace/test_gene_expression_export_submission.py"),
    Path("backend/tests/unit/lib/curation_workspace/test_session_service.py"),
    Path("backend/tests/unit/lib/document_sources/test_abc_literature_provider.py"),
    Path("backend/tests/unit/lib/document_sources/test_access_health.py"),
    Path("backend/tests/unit/lib/document_sources/test_identifier_import.py"),
    Path("backend/tests/unit/lib/document_sources/test_import_selection.py"),
    Path("backend/tests/unit/lib/document_sources/test_ingestion.py"),
    Path("backend/tests/unit/lib/document_sources/test_provenance.py"),
    Path("backend/tests/unit/lib/domain_packs/test_allele_domain_pack_fixtures.py"),
    Path("backend/tests/unit/lib/domain_packs/test_materialization.py"),
    Path("backend/tests/unit/lib/domain_packs/test_pack_workspace_display.py"),
    Path("backend/tests/unit/lib/domain_packs/test_validator_dispatch.py"),
    Path("backend/tests/unit/lib/domain_packs/test_validation_registry_metadata.py"),
    Path("backend/tests/unit/lib/feedback/test_service.py"),
    Path("backend/tests/unit/lib/flows/test_executor.py"),
    Path("backend/tests/unit/lib/flows/test_output_projection.py"),
    Path("backend/tests/unit/lib/packages/alliance/test_abc_literature_client.py"),
    Path("backend/tests/unit/lib/openai_agents/test_streaming_tools_retry_paths.py"),
    Path("backend/tests/unit/lib/pdf_jobs/test_upload_intake_service.py"),
    Path("backend/tests/unit/models/sql/test_pdf_document.py"),
    Path("backend/tests/unit/scripts/test_abc_literature_ready_upload_smoke.py"),
    Path("backend/tests/unit/test_weaviate_client.py"),
    Path("backend/tests/unit/models/sql/test_agent_prompt_override_columns.py"),
    Path("backend/tests/unit/schemas/models/test_allele_extraction_envelope.py"),
    Path("backend/tests/unit/schemas/test_curation_workspace.py"),
    Path("backend/tests/unit/schemas/test_domain_validator.py"),
    # Contract, integration, and live suites intentionally exercise shipped deployment data.
    Path("backend/tests/contract/alliance/domain_packs/test_allele_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_alliance_gene_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_disease_builder_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_disease_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_export_submission_adapters.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_gene_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_gene_expression_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_generic_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_go_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_live_db_lookup_contract.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_phenotype_builder_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_phenotype_domain_pack.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_reference_validation_bindings.py"),
    Path("backend/tests/contract/alliance/domain_packs/test_validation_metadata.py"),
    Path("backend/tests/contract/test_auth_logout.py"),
    Path("backend/tests/contract/test_auth_users_me.py"),
    Path("backend/tests/contract/test_documents_delete.py"),
    Path("backend/tests/contract/test_documents_download_pdf.py"),
    Path("backend/tests/contract/test_documents_download_pdfx.py"),
    Path("backend/tests/contract/test_documents_download_processed.py"),
    Path("backend/tests/contract/test_documents_get.py"),
    Path("backend/tests/contract/test_documents_status.py"),
    Path("backend/tests/contract/test_documents_upload.py"),
    Path("backend/tests/contract/test_list_documents.py"),
    Path("backend/tests/integration/conftest.py"),
    Path("backend/tests/integration/alliance/test_allele_fuzzy_db_lookup.py"),
    Path("backend/tests/integration/evidence_test_support.py"),
    Path("backend/tests/integration/test_cross_user_access.py"),
    Path("backend/tests/integration/test_curation_submission_e2e.py"),
    Path("backend/tests/integration/test_curation_workspace_sessions_api.py"),
    Path("backend/tests/integration/test_feedback_submission.py"),
    Path("backend/tests/integration/test_login_provisioning.py"),
    Path("backend/tests/integration/test_logout.py"),
    Path("backend/tests/integration/test_performance.py"),
    Path("backend/tests/integration/test_protected_endpoints.py"),
    Path("backend/tests/integration/test_session_timeout.py"),
    Path("backend/tests/integration/test_chat_stream_inline_persistence.py"),
    Path("backend/tests/integration/test_inspect_results_resolution.py"),
    Path("backend/tests/integration/persistence/test_inline_extraction_persistence.py"),
    # Alliance domain-pack contract tests (inherently Alliance-specific by location).
    Path("backend/tests/contract/alliance/domain_packs/test_disease_relation_subset_enforcement.py"),
    Path("backend/tests/live_integration/test_backend_batch_live_processing.py"),
    Path("backend/tests/live_integration/test_backend_chat_live_pdf_qa.py"),
    Path("backend/tests/live_integration/test_backend_flow_live_llm.py"),
    Path("backend/tests/live_integration/test_abc_literature_live_smoke.py"),
    Path("backend/tests/live_integration/test_backend_pdfx_live_cancellation.py"),
    Path("backend/tests/live_integration/test_backend_pdfx_live_pipeline.py"),
    # Frontend tests that assert current shipped Alliance defaults or auth fixtures.
    Path("frontend/src/components/AgentStudio/OpusChat.test.tsx"),
    Path("frontend/src/components/AgentStudio/DomainEnvelopeMetadataPanel.test.tsx"),
    Path("frontend/src/components/AgentStudio/FlowBuilder/FlowBuilder.test.tsx"),
    Path("frontend/src/components/AgentStudio/FlowBuilder/NodeEditor.test.tsx"),
    Path("frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx"),
    Path("frontend/src/features/curation/entityTable/workspaceEntityTags.test.ts"),
    Path("frontend/src/features/curation/entityTags/workspaceEntityTags.test.ts"),
    Path("frontend/src/features/curation/types.test.ts"),
    Path("frontend/src/components/weaviate/DocumentDetailsDialog.test.tsx"),
    Path("frontend/src/components/weaviate/DocumentList.test.tsx"),
    Path("frontend/src/pages/CurationWorkspacePage.test.tsx"),
    Path("frontend/src/services/weaviate.test.tsx"),
    Path("frontend/src/test/components/Chat.test.tsx"),
    Path("frontend/src/test/utils/auditHelpers.test.ts"),
}


@pytest.fixture(autouse=True)
def _reset_runtime_caches():
    from src.lib.openai_agents import streaming_tools

    agent_loader.reset_cache()
    models_loader.reset_cache()
    prompt_loader.reset_cache()
    providers_loader.reset_cache()
    schema_discovery.reset_cache()
    _reset_streaming_tool_caches(streaming_tools)
    runtime_validation.reset_startup_agent_validation_report()
    yield
    agent_loader.reset_cache()
    models_loader.reset_cache()
    prompt_loader.reset_cache()
    providers_loader.reset_cache()
    schema_discovery.reset_cache()
    _reset_streaming_tool_caches(streaming_tools)
    runtime_validation.reset_startup_agent_validation_report()


def _reset_streaming_tool_caches(streaming_tools: ModuleType) -> None:
    streaming_tools._tool_metadata_by_name.cache_clear()
    streaming_tools._tool_provider_adapter_factories.cache_clear()
    streaming_tools.builder_finalization_tool_names.cache_clear()
    streaming_tools._run_state_tool_impls.cache_clear()


def _copy_runtime_package(source: Path, packages_dir: Path, directory_name: str) -> Path:
    destination = packages_dir / directory_name
    shutil.copytree(source, destination)
    return destination


def _assert_no_alliance_runtime_values(values: list[str]) -> None:
    joined = "\n".join(values)
    forbidden = [
        pattern.pattern
        for pattern in ALLIANCE_LITERAL_PATTERNS
        if pattern.search(joined)
    ]
    assert forbidden == []


def _iter_backend_and_frontend_test_files() -> tuple[Path, ...]:
    backend_tests = tuple(
        path
        for path in (REPO_ROOT / "backend" / "tests").rglob("*.py")
        if path.is_file()
    )
    frontend_tests = tuple(
        path
        for path in (REPO_ROOT / "frontend" / "src").rglob("*")
        if path.is_file()
        and (
            ".test." in path.name
            or Path("frontend/src/test") in path.relative_to(REPO_ROOT).parents
        )
    )
    return tuple(sorted((*backend_tests, *frontend_tests)))


def test_core_plus_org_custom_runtime_loads_without_alliance_package(monkeypatch, tmp_path):
    packages_dir = tmp_path / "runtime-packages"
    _copy_runtime_package(REPO_ROOT / "packages" / "core", packages_dir, "agr.core")
    _copy_runtime_package(ORG_CUSTOM_FIXTURE, packages_dir, "org.custom")

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages_dir))
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: None)
    monkeypatch.setattr(agent_sources, "get_runtime_config_dir", lambda: tmp_path / "runtime-config")
    monkeypatch.setattr(package_default_sources, "_find_project_root", lambda: None)
    monkeypatch.setattr(
        package_default_sources,
        "get_runtime_config_dir",
        lambda: tmp_path / "runtime-config",
    )

    package_registry = load_package_registry(
        packages_dir,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    assert {package.package_id for package in package_registry.loaded_packages} == {
        "agr.core",
        "org.custom",
    }
    assert package_registry.get_package("agr.alliance") is None

    agents = agent_loader.load_agent_definitions(packages_dir, force_reload=True)
    assert set(agents) == {
        "curation_handoff",
        "curation_prep",
        "demo_agent_validation",
        "supervisor",
    }
    assert agents["demo_agent_validation"].folder_name == "demo_agent"
    assert agents["demo_agent_validation"].tools == ["demo_search_tool"]
    assert agents["demo_agent_validation"].curation.adapter_key == "demo"
    assert agents["demo_agent_validation"].output_projection is not None
    assert (
        agents["demo_agent_validation"].output_projection.row_list_field
        == "projected_records"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key")
    for agent in agents.values():
        if agent.model_config is None:
            continue
        model_id = agent.model_config.model
        provider_id = resolve_model_provider(model_id)
        assert get_model_for_agent(model_id, provider_override=provider_id) == model_id

    schemas = schema_discovery.discover_agent_schemas(packages_dir, force_reload=True)
    assert set(schemas) == {
        "CurationPrepAgentOutput",
        "DemoValidationEnvelope",
        "PdfExtractionFinalizationEnvelope",
        "PdfExtractionResultEnvelope",
    }
    demo_schema = schema_discovery.get_schema_for_agent("demo_agent")
    assert demo_schema is not None
    assert demo_schema.__name__ == "DemoValidationEnvelope"

    tool_registry = load_tool_registry(
        packages_dir,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    assert set(tool_registry.bindings_by_tool_id) == {"demo_search_tool"}
    demo_tool_binding = tool_registry.get("demo_search_tool")
    assert demo_tool_binding is not None
    assert demo_tool_binding.source.package_id == "org.custom"

    tool_policies = load_tool_policy_defaults(packages_dir=packages_dir)
    assert "alliance_api_call" not in tool_policies
    assert all(
        "alliance_api_call" not in str(value)
        for policy in tool_policies.values()
        for value in (
            policy.tool_key,
            policy.display_name,
            policy.description,
            policy.config,
        )
    )
    attachable_catalog = [
        policy
        for policy in tool_policies.values()
        if policy.allow_attach
    ]
    assert {
        policy.tool_key for policy in attachable_catalog
    } <= set(tool_registry.bindings_by_tool_id)

    document_source_catalog = load_document_source_provider_catalog(packages_dir)
    assert set(document_source_catalog.registrations_by_provider_id) == {
        "example_literature"
    }
    loaded_document_source = document_source_catalog.get("example_literature")
    assert loaded_document_source is not None
    assert dict(loaded_document_source.registration.capabilities) == {
        "identifier_import": True,
        "checksum_lookup": False,
        "conversion_requests": False,
    }
    callback_calls = getattr(
        loaded_document_source.registration.factory,
        "__globals__",
    )["CALLBACK_CALLS"]
    assert callback_calls == {"factory": 0, "development_token_resolver": 0}
    assert loaded_document_source.source.package_id == "org.custom"
    assert get_document_source_provider_metadata("example_literature") == {
        "display_label": "Example Literature",
        "reference_label_priority": ["reference_curie", "reference_id"],
    }
    assert callback_calls == {"factory": 0, "development_token_resolver": 0}
    assert (
        get_configured_document_source_dev_mode_static_curator_token(
            "example_literature"
        )
        == "fixture-development-token"
    )
    assert callback_calls == {"factory": 0, "development_token_resolver": 1}
    assert (
        get_configured_document_source_provider("example_literature").provider_id
        == "example_literature"
    )
    assert callback_calls == {"factory": 1, "development_token_resolver": 1}

    registry = build_agent_registry()
    assert "demo_agent_validation" in registry
    assert "demo_agent" in registry
    assert "gene_validation" not in registry
    assert "gene_extractor" not in registry
    flow_recipe_catalog = load_flow_recipe_catalog()
    assert [recipe.name for recipe in flow_recipe_catalog.recipes] == [
        "Demo Record Review"
    ]
    assert [
        group.agent_ids for group in flow_recipe_catalog.equivalence_groups
    ] == [["demo_agent", "demo_agent_validation"]]
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["demo_agent", "demo_agent_validation"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "demo_agent": {"category": "Demo"},
            "demo_agent_validation": {"category": "Demo"},
        },
    )
    assert flow_tools._filter_flow_templates(
        {"demo_agent", "demo_agent_validation"},
        flow_recipe_catalog,
    ) == [
        {
            "name": "Demo Record Review",
            "description": "Review a demo record with the installed custom specialist",
            "allowed_group_ids": [],
            "steps": [
                {
                    "agent_id": "demo_agent",
                    "step_goal": "Validate the demo record",
                }
            ],
        }
    ]
    _assert_no_alliance_runtime_values(
        [
            *agents.keys(),
            *schemas.keys(),
            *tool_registry.bindings_by_tool_id.keys(),
            *registry.keys(),
        ]
    )


@pytest.mark.parametrize(
    ("projection", "message"),
    [
        (None, "must declare output_projection"),
        (
            {"row_list_field": "missing_rows", "identity_fields": ["record_key"]},
            "must name a list field",
        ),
        (
            {
                "row_list_field": "projected_records",
                "identity_fields": ["missing_key"],
            },
            "identity_fields are not declared",
        ),
        (
            {
                "row_list_field": "projected_records",
                "identity_fields": ["record_key"],
                "label_fields": ["missing_label"],
            },
            "label_fields are not declared",
        ),
        (
            {
                "row_list_field": "projected_records",
                "identity_fields": ["record_key"],
                "inherited_parent_fields": ["missing_source"],
            },
            "inherited_parent_fields are not declared",
        ),
    ],
)
def test_typed_validator_projection_metadata_is_validated_at_package_load(
    monkeypatch,
    tmp_path,
    projection,
    message,
):
    packages_dir = tmp_path / "runtime-packages"
    _copy_runtime_package(REPO_ROOT / "packages" / "core", packages_dir, "agr.core")
    package_dir = _copy_runtime_package(
        ORG_CUSTOM_FIXTURE,
        packages_dir,
        "org.custom",
    )
    agent_yaml = package_dir / "agents" / "demo_agent" / "agent.yaml"
    agent_data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
    if projection is None:
        agent_data.pop("output_projection")
    else:
        agent_data["output_projection"] = projection
    agent_yaml.write_text(
        yaml.safe_dump(agent_data, sort_keys=False),
        encoding="utf-8",
    )

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages_dir))
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: None)
    monkeypatch.setattr(
        agent_sources,
        "get_runtime_config_dir",
        lambda: tmp_path / "runtime-config",
    )

    with pytest.raises(ValueError, match=message):
        agent_loader.load_agent_definitions(packages_dir, force_reload=True)


def test_org_custom_validator_projection_uses_package_descriptor(
    monkeypatch,
    tmp_path,
):
    packages_dir = tmp_path / "runtime-packages"
    _copy_runtime_package(REPO_ROOT / "packages" / "core", packages_dir, "agr.core")
    _copy_runtime_package(ORG_CUSTOM_FIXTURE, packages_dir, "org.custom")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages_dir))
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: None)
    monkeypatch.setattr(
        agent_sources,
        "get_runtime_config_dir",
        lambda: tmp_path / "runtime-config",
    )
    agent_loader.load_agent_definitions(packages_dir, force_reload=True)

    schemas = schema_discovery.discover_agent_schemas(
        packages_dir,
        force_reload=True,
    )
    payload = schemas["DemoValidationEnvelope"].model_validate(
        {
            "status": "resolved",
            "request_id": "request-demo-1",
            "validator_binding_id": "binding-demo-1",
            "validator_agent": {
                "package_id": "org.custom",
                "agent_id": "demo_agent_validation",
            },
            "target": {
                "domain_pack_id": "org.custom.demo_record",
                "object_type": "DemoRecord",
            },
            "resolved_values": {},
            "resolved_objects": [],
            "missing_expected_fields": [],
            "candidates": [],
            "lookup_attempts": [],
            "curator_message": "Fixture projection succeeded.",
            "explanation": "Fixture projection.",
            "source_name": "fixture provider",
            "projected_records": [
                {"record_key": "DEMO-1", "label": "First record"}
            ],
        }
    ).model_dump(mode="json")

    bundle = build_flow_output_artifact_bundle(
        completed_steps=[
            {"step": 1, "agent_id": "demo_agent_validation", "output": payload}
        ],
        flow_name="Demo validation",
    )

    assert bundle.artifacts[0].artifact_shape == "structured_result"
    rows = bundle.rows_for_source("object")
    assert len(rows) == 1
    assert rows[0]["object.object_type"] == "DemoRecord"
    assert rows[0]["object.object_id"] == "DEMO-1"
    assert rows[0]["object.label"] == "First record"
    assert rows[0]["object.payload.source_name"] == "fixture provider"


def test_org_custom_domain_pack_walkthrough_registers_runtime_surfaces(monkeypatch, tmp_path):
    packages_dir = tmp_path / "runtime-packages"
    _copy_runtime_package(REPO_ROOT / "packages" / "core", packages_dir, "agr.core")
    org_custom_package = _copy_runtime_package(
        ORG_CUSTOM_FIXTURE,
        packages_dir,
        "org.custom",
    )

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages_dir))
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: None)
    monkeypatch.setattr(
        agent_sources,
        "get_runtime_config_dir",
        lambda: tmp_path / "runtime-config",
    )

    agent_loader.load_agent_definitions(packages_dir, force_reload=True)
    tool_registry = load_tool_registry(
        packages_dir,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    adapter_registry = build_curation_adapter_registry()
    domain_pack = adapter_registry.get_domain_pack_by_id("org.custom.demo_record")
    assert domain_pack is not None
    assert domain_pack.package_id == "org.custom"
    assert domain_pack.metadata.metadata["validator_bindings"]["active"][0][
        "validator_agent"
    ] == {
        "package_id": "org.custom",
        "agent_id": "demo_agent_validation",
    }

    fixture_pack = load_domain_fixture_pack(
        org_custom_package
        / "domain_packs"
        / "demo_record"
        / "fixtures"
        / "smoke.yaml"
    )
    envelope = fixture_pack.fixtures[0].envelope
    validator = adapter_registry.get_domain_envelope_validator_by_id(
        "org.custom.demo_record"
    )
    assert callable(validator)
    assert validator(envelope) == ()

    materializer = adapter_registry.get_review_row_materializer_for_domain_pack(
        "org.custom.demo_record"
    )
    assert materializer is not None
    rows = materializer.materialize(envelope, envelope_revision=1)
    assert [row.object_id for row in rows] == ["demo-record-1"]
    assert [field.field_path for field in rows[0].summary_fields] == [
        "record.record_id",
        "record.title",
        "review.status",
    ]
    assert rows[0].metadata["workspace_fields"][0]["metadata"]["workspace_group"] == {
        "id": "identity",
        "label": "Identity",
        "order": 0,
        "field_order": 0,
    }

    export_registry = ExportAdapterRegistry(adapter_registry.export_adapters())
    export_adapter = export_registry.require("demo")
    candidate = {
        "candidate_id": "candidate-demo-record-1",
        "projection_ref": {
            "envelope_id": envelope.envelope_id,
            "object_id": "demo-record-1",
            "envelope_revision": 1,
        },
        "envelope_id": envelope.envelope_id,
        "envelope_revision": 1,
        "domain_pack_id": envelope.domain_pack_id,
        "domain_pack_version": envelope.domain_pack_version,
        "object_id": "demo-record-1",
        "object_type": "DemoRecord",
        "payload": envelope.extracted_objects[0].payload,
    }
    payload = export_adapter.build_submission_payload(
        mode=SubmissionMode.EXPORT,
        target_key="demo.records.archive",
        payload_context={
            "session_id": "session-demo-record",
            "candidate_ids": [candidate["candidate_id"]],
            "candidate_count": 1,
            "candidates": [],
            "domain_envelope_candidates": [candidate],
            "domain_envelopes": [],
            "readiness_blockers": [],
            "warnings": [],
        },
    )

    assert payload.payload_json == {
        "adapter_key": "demo",
        "mode": "export",
        "target_key": "demo.records.archive",
        "domain_pack_id": "org.custom.demo_record",
        "records": [
            {
                "candidate_id": "candidate-demo-record-1",
                "record_id": "DEMO-0001",
                "review_status": "accepted",
                "title": "Neutral external package record",
            }
        ],
    }
    demo_tool_binding = tool_registry.get("demo_search_tool")
    assert demo_tool_binding is not None
    assert demo_tool_binding.source.package_id == "org.custom"
    _assert_no_alliance_runtime_values(
        [
            *adapter_registry.adapter_keys(),
            domain_pack.pack_id,
            payload.payload_text or "",
        ]
    )


def test_org_custom_prompts_load_with_neutral_sources_and_group_ids(monkeypatch, tmp_path):
    packages_dir = tmp_path / "runtime-packages"
    _copy_runtime_package(ORG_CUSTOM_FIXTURE, packages_dir, "org.custom")
    db = MagicMock()
    captured_calls = []

    def _capture_upsert(**kwargs):
        captured_calls.append(kwargs)
        return (True, 1)

    monkeypatch.setattr(prompt_loader, "_acquire_advisory_lock", lambda _db: (True, True))
    monkeypatch.setattr(prompt_loader, "_upsert_prompt", _capture_upsert)

    result = prompt_loader.load_prompts(packages_dir, db=db, force_reload=True)

    assert result == {"base_prompts": 1, "group_rules": 1}
    assert {
        (call["agent_name"], call["prompt_type"], call["group_id"])
        for call in captured_calls
    } == {
        ("demo_agent", "system", None),
        ("demo_agent", "group_rules", "DEMO"),
    }
    _assert_no_alliance_runtime_values(
        [
            str(call["source_file"])
            for call in captured_calls
        ]
    )


def test_runtime_validation_accepts_synthetic_system_agent_without_alliance(monkeypatch, tmp_path):
    packages_dir = tmp_path / "runtime-packages"
    _copy_runtime_package(ORG_CUSTOM_FIXTURE, packages_dir, "org.custom")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages_dir))
    monkeypatch.setattr(agent_sources, "_find_project_root", lambda: None)
    monkeypatch.setattr(agent_sources, "get_runtime_config_dir", lambda: tmp_path / "runtime-config")
    monkeypatch.setattr(runtime_validation, "load_models", lambda: None)
    monkeypatch.setattr(
        runtime_validation,
        "list_models",
        lambda: [SimpleNamespace(model_id="gpt-5.4-mini")],
    )
    monkeypatch.setattr(
        runtime_validation,
        "_fetch_active_agents",
        lambda: [
            SimpleNamespace(
                agent_key="demo_agent",
                name="Demo Agent",
                visibility="system",
                user_id=None,
                project_id=None,
                template_source=None,
                model_id="gpt-5.4-mini",
                model_reasoning=None,
                tool_ids=["demo_search_tool"],
                output_schema_key=None,
            )
        ],
    )
    monkeypatch.setattr(
        runtime_validation,
        "_load_runtime_policy",
        lambda: {
            "tool_bindings": {"demo_search_tool": {"required_context": []}},
            "canonicalize_tool_id": lambda tool_id: tool_id,
            "document_tool_ids": set(),
            "package_required_tool_ids": {"demo_search_tool"},
        },
    )

    report = runtime_validation.build_agent_runtime_report(strict_mode=True)

    assert report["status"] == "healthy"
    assert report["errors"] == []
    assert report["warnings"] == []
    assert report["summary"]["missing_system_agent_count"] == 0
    _assert_no_alliance_runtime_values(
        [
            str(report["agents"]),
            str(report["summary"]),
        ]
    )


def test_generic_runtime_tests_keep_neutral_placeholders():
    violations = []
    for relative_path in sorted(GENERIC_RUNTIME_GUARD_PATHS):
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for pattern in GENERIC_RUNTIME_PLACEHOLDER_PATTERNS:
            if pattern.search(text):
                violations.append(f"{relative_path}: {pattern.pattern}")

    assert violations == []


def test_generic_runtime_sources_do_not_hardcode_alliance_identifiers():
    violations = []
    for relative_path in sorted(GENERIC_RUNTIME_SOURCE_GUARD_PATHS):
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for pattern in GENERIC_RUNTIME_SOURCE_PATTERNS:
            if pattern.search(text):
                violations.append(f"{relative_path}: {pattern.pattern}")

    assert violations == []


def test_core_agent_studio_policy_does_not_own_package_diagnostic_ids():
    text = (REPO_ROOT / AGENT_STUDIO_OPUS_TOOLS_PATH).read_text(encoding="utf-8")
    module = ast.parse(text)
    agents_only_assignment = next(
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "AGENTS_ONLY_DIAGNOSTIC_TOOLS"
            for target in node.targets
        )
    )

    assert ast.literal_eval(agents_only_assignment.value) == {
        "search_codebase",
        "read_source_file",
    }


def test_generic_domain_pack_resolution_requires_a_registered_package(monkeypatch):
    original_import = builtins.__import__

    def reject_alliance_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("agr_ai_curation_alliance"):
            raise AssertionError(f"Core resolver imported Alliance module: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_alliance_import)
    monkeypatch.setattr(
        domain_pack_registry_module,
        "load_domain_pack_registry",
        lambda: SimpleNamespace(get_pack=lambda _pack_id: None),
    )
    monkeypatch.setattr(
        adapter_registry_module,
        "load_curation_adapter_registry",
        adapter_registry_module.CurationAdapterRegistry,
    )

    assert adapter_registry_module.resolve_curation_domain_pack_by_id("generic") is None


def test_active_agent_docs_keep_package_and_explicit_override_contract():
    backend_readme = (REPO_ROOT / "backend/README.md").read_text(encoding="utf-8")
    group_rule_injection = (
        REPO_ROOT / "backend/config/group_rules/group_config.py"
    ).read_text(encoding="utf-8")
    agent_template = (
        REPO_ROOT / "config/agents/_examples/basic_agent/agent.yaml"
    ).read_text(encoding="utf-8")
    examples_readme = (
        REPO_ROOT / "config/agents/_examples/README.md"
    ).read_text(encoding="utf-8")

    assert "Shipped agent definitions live in package-owned bundles" in backend_readme
    assert (
        "Agent definitions and LLM provider configuration live outside the backend"
        not in backend_readme
    )
    assert "Agents are defined entirely in YAML configuration files under" not in backend_readme
    assert "Loads agent definitions from `config/agents" not in backend_readme

    assert "manifest-declared package agent bundles" in group_rule_injection
    assert "from config/agents/*/group_rules/*.yaml" not in group_rule_injection

    assert "Shipped agents belong to an owning runtime" in agent_template
    assert "Copy this folder to config/agents/" not in agent_template
    assert "packages/<package>/agents/my_agent" in agent_template
    assert "source Compose explicitly mounts that directory" in agent_template

    assert "packages/<package>/agents/your_agent_name/" in examples_readme
    assert "agent_bundles" in examples_readme
    assert "explicit `/runtime/config/agents` mount" in examples_readme
    assert "../your_agent_name/" not in examples_readme


def test_generic_runtime_does_not_ship_alliance_literature_client():
    assert not (REPO_ROOT / "backend/src/lib/literature/client.py").exists()
    assert not (REPO_ROOT / "backend/src/lib/literature/__init__.py").exists()


def test_generic_flow_recipe_sources_do_not_hardcode_domain_metadata():
    violations = []
    for relative_path in sorted(GENERIC_FLOW_RECIPE_SOURCE_GUARD_PATHS):
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for pattern in GENERIC_FLOW_RECIPE_SOURCE_PATTERNS:
            if pattern.search(text):
                violations.append(f"{relative_path}: {pattern.pattern}")

    assert violations == []


def test_alliance_specific_test_literals_are_allowlisted():
    violations = []

    for path in _iter_backend_and_frontend_test_files():
        relative_path = path.relative_to(REPO_ROOT)
        if relative_path == GUARDRAIL_TEST_PATH:
            continue

        text = path.read_text(encoding="utf-8")
        matches = [
            pattern.pattern
            for pattern in ALLIANCE_LITERAL_PATTERNS
            if pattern.search(text)
        ]
        if matches and relative_path not in ALLOWED_ALLIANCE_TEST_PATHS:
            violations.append(f"{relative_path}: {', '.join(matches)}")

    assert violations == []
