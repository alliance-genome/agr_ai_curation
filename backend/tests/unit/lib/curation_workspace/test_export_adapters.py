"""Unit tests for deterministic curation-workspace export adapters."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

import src.lib.curation_workspace.adapter_registry as adapter_registry_module
from src.lib.curation_adapters.reference import REFERENCE_ADAPTER_KEY
from src.lib.curation_workspace.export_adapters import (
    DEFAULT_JSON_BUNDLE_TARGET_KEY,
    ExportAdapterRegistry,
    JsonBundleExportAdapter,
    build_default_export_adapter_registry,
)
from src.schemas.curation_workspace import SubmissionMode, SubmissionPayloadContract


def _timestamp() -> datetime:
    return datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc)


def _export_payload_context() -> dict[str, object]:
    timestamp = _timestamp()
    candidate_id = "candidate-1"

    return {
        "session_id": "session-1",
        "document": {
            "document_id": "document-1",
            "title": "Export bundle paper",
            "pmid": "12345678",
            "citation_label": "PMID:12345678",
            "pdf_url": "/api/documents/document-1/pdf",
            "viewer_url": "/documents/document-1/viewer",
        },
        "session_validation": {
            "snapshot_id": "snapshot-1",
            "scope": "session",
            "session_id": "session-1",
            "candidate_id": None,
            "adapter_key": REFERENCE_ADAPTER_KEY,
            "state": "completed",
            "field_results": {},
            "summary": {
                "state": "completed",
                "counts": {
                    "validated": 2,
                    "ambiguous": 0,
                    "not_found": 0,
                    "invalid_format": 0,
                    "conflict": 0,
                    "skipped": 0,
                    "overridden": 0,
                },
                "stale_field_keys": [],
                "warnings": [],
            },
            "requested_at": timestamp,
            "completed_at": timestamp,
            "warnings": [],
        },
        "candidate_ids": [candidate_id],
        "candidate_count": 1,
        "candidates": [
            {
                "candidate_id": candidate_id,
                "session_id": "session-1",
                "source": "extracted",
                "status": "accepted",
                "order": 0,
                "adapter_key": REFERENCE_ADAPTER_KEY,
                "display_label": "APOE association",
                "secondary_label": "Late onset phenotype",
                "conversation_summary": "Curator approved the extracted association.",
                "extraction_result_id": "extract-1",
                "normalized_payload": {
                    "gene_symbol": "APOE",
                    "condition_label": "Late onset phenotype",
                },
                "draft": {
                    "draft_id": "draft-1",
                    "candidate_id": candidate_id,
                    "adapter_key": REFERENCE_ADAPTER_KEY,
                    "version": 2,
                    "title": "APOE annotation",
                    "summary": "Approved curator draft.",
                    "fields": [
                        {
                            "field_key": "gene_symbol",
                            "label": "Gene symbol",
                            "value": "APOE",
                            "seed_value": "APOE",
                            "field_type": "string",
                            "group_key": "core",
                            "group_label": "Core",
                            "order": 0,
                            "required": True,
                            "read_only": False,
                            "dirty": False,
                            "stale_validation": False,
                            "evidence_anchor_ids": ["anchor-1"],
                            "validation_result": {
                                "status": "validated",
                                "resolver": "agr_db",
                                "candidate_matches": [],
                                "warnings": [],
                            },
                            "metadata": {"source_field_path": "gene.symbol"},
                        }
                    ],
                    "notes": "Curator confirmed symbol casing.",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "last_saved_at": timestamp,
                    "metadata": {"draft_origin": "curation_workspace"},
                },
                "evidence_anchors": [
                    {
                        "anchor_id": "anchor-1",
                        "candidate_id": candidate_id,
                        "source": "extracted",
                        "field_keys": ["gene_symbol"],
                        "field_group_keys": ["core"],
                        "is_primary": True,
                        "anchor": {
                            "anchor_kind": "snippet",
                            "locator_quality": "exact_quote",
                            "supports_decision": "supports",
                            "snippet_text": "APOE was linked to the reported phenotype.",
                            "sentence_text": "APOE was linked to the reported phenotype.",
                            "normalized_text": "apoe was linked to the reported phenotype",
                            "viewer_search_text": "APOE was linked to the reported phenotype.",
                            "viewer_highlightable": True,
                            "page_number": 3,
                            "page_label": "3",
                            "section_title": "Results",
                            "subsection_title": "Association",
                            "figure_reference": None,
                            "table_reference": None,
                            "chunk_ids": ["chunk-1"],
                        },
                        "created_at": timestamp,
                        "updated_at": timestamp,
                        "warnings": [],
                    }
                ],
                "validation": {
                    "state": "completed",
                    "counts": {
                        "validated": 1,
                        "ambiguous": 0,
                        "not_found": 0,
                        "invalid_format": 0,
                        "conflict": 0,
                        "skipped": 0,
                        "overridden": 0,
                    },
                    "stale_field_keys": [],
                    "warnings": [],
                },
                "evidence_summary": {
                    "total_anchor_count": 1,
                    "resolved_anchor_count": 1,
                    "viewer_highlightable_anchor_count": 1,
                    "quality_counts": {
                        "exact_quote": 1,
                        "normalized_quote": 0,
                        "section_only": 0,
                        "page_only": 0,
                        "document_only": 0,
                        "unresolved": 0,
                    },
                    "degraded": False,
                    "warnings": [],
                },
                "created_at": timestamp,
                "updated_at": timestamp,
                "last_reviewed_at": timestamp,
                "metadata": {"reviewer": "curator-1"},
            }
        ],
        "warnings": ["Bundle prepared from curator-approved candidates only."],
    }


def _write_package_with_curation_adapter_export(packages_dir: Path) -> None:
    package_dir = packages_dir / "demo-core"
    module_dir = package_dir / "python" / "src" / "demo_core"
    module_dir.mkdir(parents=True)
    (package_dir / "package.yaml").write_text(
        """package_id: demo.core
display_name: Demo Core
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: python/src/demo_core
requirements_file: requirements/runtime.txt
exports:
  - kind: curation_adapter
    name: default
    path: python/src/demo_core/curation_adapters.py
    description: Demo curation adapters
agent_bundles:
  - name: gene
""",
        encoding="utf-8",
    )
    (module_dir / "curation_adapters.py").write_text(
        """from src.lib.curation_workspace.export_adapters import JsonBundleExportAdapter


class DemoNormalizer:
    pass


def register_curation_adapters(registry) -> None:
    registry.register_adapter(
        adapter_key="gene",
        candidate_normalizer=DemoNormalizer(),
        export_adapter=JsonBundleExportAdapter(adapter_key="gene"),
    )
""",
        encoding="utf-8",
    )

    for agent_name in ("gene", "undeclared"):
        agent_dir = package_dir / "agents" / agent_name
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(
            f"agent_id: {agent_name}\n",
            encoding="utf-8",
        )


def test_export_adapter_registry_registers_and_looks_up_adapters():
    registry = ExportAdapterRegistry()
    adapter = JsonBundleExportAdapter(adapter_key=REFERENCE_ADAPTER_KEY)

    registry.register(adapter)

    assert registry.get(REFERENCE_ADAPTER_KEY) is adapter
    assert registry.require(REFERENCE_ADAPTER_KEY) is adapter
    assert registry.adapter_keys() == (REFERENCE_ADAPTER_KEY,)


def test_build_default_export_adapter_registry_exposes_reference_adapter():
    registry = build_default_export_adapter_registry()

    adapter = registry.require(REFERENCE_ADAPTER_KEY)

    assert adapter.__class__.__name__ == "JsonBundleExportAdapter"
    assert adapter.__class__.__module__ == (
        "src.lib.curation_workspace.export_adapters.json_bundle"
    )
    assert adapter.supported_target_keys == (DEFAULT_JSON_BUNDLE_TARGET_KEY,)


def test_build_default_export_adapter_registry_keeps_package_export_when_agent_bundle_is_undeclared(
    tmp_path,
    monkeypatch,
):
    packages_dir = tmp_path / "packages"
    _write_package_with_curation_adapter_export(packages_dir)

    adapter_registry_module.load_curation_adapter_registry.cache_clear()
    monkeypatch.setattr(adapter_registry_module, "_default_packages_dir", lambda: packages_dir)

    try:
        registry = build_default_export_adapter_registry()
    finally:
        adapter_registry_module.load_curation_adapter_registry.cache_clear()

    adapter = registry.require("gene")

    assert registry.adapter_keys() == ("gene",)
    assert adapter.__class__.__name__ == "JsonBundleExportAdapter"
    assert adapter.__class__.__module__ == (
        "src.lib.curation_workspace.export_adapters.json_bundle"
    )
    assert adapter.supported_target_keys == (DEFAULT_JSON_BUNDLE_TARGET_KEY,)


def test_build_curation_adapter_registry_keeps_package_export_when_agent_bundle_is_undeclared(
    tmp_path,
    monkeypatch,
):
    packages_dir = tmp_path / "packages"
    _write_package_with_curation_adapter_export(packages_dir)

    adapter_registry_module.load_curation_adapter_registry.cache_clear()
    monkeypatch.setattr(adapter_registry_module, "_default_packages_dir", lambda: packages_dir)

    try:
        registry = adapter_registry_module.load_curation_adapter_registry()
    finally:
        adapter_registry_module.load_curation_adapter_registry.cache_clear()

    normalizer = registry.require_candidate_normalizer("gene")

    assert registry.adapter_keys() == ("gene",)
    assert normalizer.__class__.__name__ == "DemoNormalizer"


def test_json_bundle_export_adapter_builds_payload_from_candidates_and_evidence():
    adapter = JsonBundleExportAdapter(adapter_key=REFERENCE_ADAPTER_KEY)

    payload = adapter.build_submission_payload(
        mode=SubmissionMode.EXPORT,
        target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
        payload_context=_export_payload_context(),
    )

    assert payload.mode == SubmissionMode.EXPORT
    assert payload.target_key == DEFAULT_JSON_BUNDLE_TARGET_KEY
    assert payload.adapter_key == REFERENCE_ADAPTER_KEY
    assert payload.candidate_ids == ["candidate-1"]
    assert payload.content_type == "application/json"
    assert payload.filename == "reference_adapter-session-1-export-bundle.json"
    assert payload.warnings == ["Bundle prepared from curator-approved candidates only."]
    assert payload.payload_json is not None
    assert payload.payload_text is not None
    assert payload.payload_json["candidate_count"] == 1
    assert payload.payload_json["candidates"][0]["draft"]["fields"][0]["value"] == "APOE"
    assert (
        payload.payload_json["candidates"][0]["evidence_anchors"][0]["anchor"]["snippet_text"]
        == "APOE was linked to the reported phenotype."
    )
    assert json.loads(payload.payload_text) == payload.payload_json


def test_submission_payload_contract_requires_at_least_one_payload_variant():
    with pytest.raises(ValidationError):
        SubmissionPayloadContract(
            mode=SubmissionMode.EXPORT,
            target_key=DEFAULT_JSON_BUNDLE_TARGET_KEY,
            adapter_key=REFERENCE_ADAPTER_KEY,
            candidate_ids=["candidate-1"],
        )
