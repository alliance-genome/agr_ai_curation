"""Project-agnostic guardrails for non-Alliance runtime package testing."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from . import find_repo_root
from src.lib.agent_studio import runtime_validation
from src.lib.agent_studio.registry_builder import build_agent_registry
from src.lib.config import agent_loader, agent_sources, prompt_loader, schema_discovery
from src.lib.curation_workspace.adapter_registry import build_curation_adapter_registry
from src.lib.curation_workspace.export_adapters.registry import ExportAdapterRegistry
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.packages.registry import load_package_registry
from src.lib.packages.tool_registry import load_tool_registry
from src.schemas.curation_workspace import SubmissionMode

REPO_ROOT = find_repo_root(Path(__file__))
FIXTURES_DIR = Path(__file__).parent / "fixtures"
ORG_CUSTOM_FIXTURE = FIXTURES_DIR / "org_custom_runtime"
GUARDRAIL_TEST_PATH = Path("backend/tests/unit/lib/packages/test_project_agnostic_runtime_guardrails.py")

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
GENERIC_RUNTIME_PLACEHOLDER_PATTERNS = (
    re.compile(r"agr\.alliance"),
    re.compile(r"agr_curation_query"),
    re.compile(r"alliance_api_call"),
    re.compile(r"alliancegenome"),
    re.compile(r"(?<![A-Za-z0-9_])gene(?![A-Za-z0-9_])"),
    re.compile(r"\b(?:FB|WB|MGI|RGD|SGD|ZFIN|HGNC)\b"),
)

ALLOWED_ALLIANCE_TEST_PATHS = {
    # Bundled Alliance package contracts and prompt/tool policy coverage.
    Path("backend/tests/unit/test_config_loaders.py"),
    Path("backend/tests/unit/test_gene_allele_validator_result_contract.py"),
    Path("backend/tests/unit/test_subject_entity_validator_result_contract.py"),
    Path("backend/tests/unit/test_disease_extractor_domain_envelope_contract.py"),
    Path("backend/tests/unit/test_domain_envelope_repair_prompt_contract.py"),
    Path("backend/tests/unit/test_gene_extractor_domain_envelope_contract.py"),
    Path("backend/tests/unit/test_gene_expression_prompt_policy.py"),
    Path("backend/tests/unit/test_phenotype_extractor_domain_envelope_contract.py"),
    Path("backend/tests/unit/test_allele_extractor_mgi_prompt_policy.py"),
    Path("backend/tests/unit/lib/config/test_bundled_alliance_package_aware_loaders.py"),
    Path("backend/tests/unit/lib/config/test_controlled_vocabulary_validation_agent.py"),
    Path("backend/tests/unit/lib/config/test_data_provider_validation_agent.py"),
    Path("backend/tests/unit/lib/config/test_disease_chemical_validator_result_contract.py"),
    Path("backend/tests/unit/lib/config/test_experimental_condition_validation_agent.py"),
    Path("backend/tests/unit/lib/config/test_groups_loader_identity_provider.py"),
    Path("backend/tests/unit/lib/config/test_ontology_term_validator_contract.py"),
    Path("backend/tests/unit/lib/config/test_prompt_loader_runtime.py"),
    Path("backend/tests/unit/lib/config/test_reference_validator_result_contract.py"),
    Path("backend/tests/unit/lib/config/test_runtime_config_defaults.py"),
    Path("backend/tests/unit/lib/packages/__init__.py"),
    Path("backend/tests/unit/lib/packages/test_alliance_agent_package.py"),
    Path("backend/tests/unit/lib/packages/test_alliance_literature_reference_tool.py"),
    Path("backend/tests/unit/lib/packages/test_core_package_contract.py"),
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
    Path("backend/tests/unit/lib/test_runtime_entrypoint.py"),
    Path("backend/tests/unit/lib/test_weaviate_documents_runtime.py"),
    Path("backend/tests/unit/lib/prompts/test_cache_core.py"),
    Path("backend/tests/unit/lib/prompts/test_cache_overrides.py"),
    Path("backend/tests/unit/lib/prompts/test_assembly_callsite_parity.py"),
    Path("backend/tests/unit/lib/prompts/test_assembly.py"),
    Path("backend/tests/unit/lib/prompts/test_context_tracking.py"),
    Path("backend/tests/unit/lib/prompts/test_service_core.py"),
    Path("backend/tests/unit/lib/agent_studio/test_agent_service.py"),
    Path("backend/tests/unit/lib/agent_studio/test_catalog_service_branches.py"),
    Path("backend/tests/unit/lib/agent_studio/test_catalog_service_prompt_keys.py"),
    Path("backend/tests/unit/lib/agent_studio/test_catalog_service_tool_bindings.py"),
    Path("backend/tests/unit/lib/agent_studio/test_custom_agent_service.py"),
    Path("backend/tests/unit/lib/agent_studio/test_custom_agent_service_branches.py"),
    Path("backend/tests/unit/lib/agent_studio/test_domain_envelope_tools.py"),
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
    Path("backend/tests/unit/api/test_flows_api.py"),
    Path("backend/tests/unit/lib/alerts/test_tool_failure_notifier.py"),
    Path("backend/tests/unit/lib/curation_workspace/test_extraction_results.py"),
    Path("backend/tests/unit/lib/curation_workspace/test_gene_expression_export_submission.py"),
    Path("backend/tests/unit/lib/curation_workspace/test_session_service.py"),
    Path("backend/tests/unit/lib/domain_packs/test_allele_domain_pack_fixtures.py"),
    Path("backend/tests/unit/lib/domain_packs/test_materialization.py"),
    Path("backend/tests/unit/lib/domain_packs/test_validator_dispatch.py"),
    Path("backend/tests/unit/lib/domain_packs/test_validation_registry_metadata.py"),
    Path("backend/tests/unit/lib/feedback/test_service.py"),
    Path("backend/tests/unit/lib/flows/test_executor.py"),
    Path("backend/tests/unit/lib/openai_agents/test_streaming_tools_retry_paths.py"),
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
    # Alliance domain-pack contract tests (inherently Alliance-specific by location).
    Path("backend/tests/contract/alliance/domain_packs/test_disease_relation_subset_enforcement.py"),
    Path("backend/tests/live_integration/test_backend_batch_live_processing.py"),
    Path("backend/tests/live_integration/test_backend_chat_live_pdf_qa.py"),
    Path("backend/tests/live_integration/test_backend_flow_live_llm.py"),
    Path("backend/tests/live_integration/test_backend_pdfx_live_cancellation.py"),
    Path("backend/tests/live_integration/test_backend_pdfx_live_pipeline.py"),
    # Frontend tests that assert current shipped Alliance defaults or auth fixtures.
    Path("frontend/src/components/AgentStudio/OpusChat.test.tsx"),
    Path("frontend/src/components/AgentStudio/DomainEnvelopeMetadataPanel.test.tsx"),
    Path("frontend/src/components/AgentStudio/FlowBuilder/FlowBuilder.test.tsx"),
    Path("frontend/src/components/AgentStudio/FlowBuilder/NodeEditor.test.tsx"),
    Path("frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx"),
    Path("frontend/src/features/curation/entityTable/workspaceEntityTags.test.ts"),
    Path("frontend/src/features/curation/types.test.ts"),
    Path("frontend/src/pages/CurationWorkspacePage.test.tsx"),
    Path("frontend/src/test/components/Chat.test.tsx"),
    Path("frontend/src/test/utils/auditHelpers.test.ts"),
}


@pytest.fixture(autouse=True)
def _reset_runtime_caches():
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    runtime_validation.reset_startup_agent_validation_report()
    yield
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    runtime_validation.reset_startup_agent_validation_report()


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
    assert set(agents) == {"supervisor", "demo_agent_validation"}
    assert agents["demo_agent_validation"].folder_name == "demo_agent"
    assert agents["demo_agent_validation"].tools == ["demo_search_tool"]
    assert agents["demo_agent_validation"].curation.adapter_key == "demo"

    schemas = schema_discovery.discover_agent_schemas(packages_dir, force_reload=True)
    assert set(schemas) == {
        "CurationPrepAgentOutput",
        "DemoValidationEnvelope",
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

    registry = build_agent_registry()
    assert "demo_agent_validation" in registry
    assert "demo_agent" in registry
    assert "gene_validation" not in registry
    assert "gene_extractor" not in registry
    _assert_no_alliance_runtime_values(
        [
            *agents.keys(),
            *schemas.keys(),
            *tool_registry.bindings_by_tool_id.keys(),
            *registry.keys(),
        ]
    )


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
        "payload": envelope.objects[0].payload,
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
        lambda: [SimpleNamespace(model_id="gpt-5-mini")],
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
                model_id="gpt-5-mini",
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
