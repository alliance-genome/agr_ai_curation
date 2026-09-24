"""Unit tests for active domain-pack validator dispatch."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel

from src.lib.config.agent_loader import AgentDefinition
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validator_dispatch import (
    _DEFAULT_VALIDATOR_BATCH_MAX_SIZE,
    ValidatorRuntimeContext,
    _apply_validator_evidence_updates_to_envelope,
    _plan_validator_run_groups,
    _validated_results_from_agent_batch_output,
    _validator_batch_summary,
    _validator_finalization_tool_payload,
    _validator_request_dedupe_key,
    _validator_result_finalization_feedback,
    dispatch_active_validator_bindings,
    preflight_unresolved_validator_result,
    run_package_scoped_validator_agent_batch,
    run_package_scoped_validator_agent,
    validator_request_payload_for_agent,
    validator_result_from_agent_output,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.domain_validator import (
    DomainValidationRequest,
    DomainValidatorResultBase,
    ValidationTarget,
    ValidatorAgentRef,
)


@pytest.fixture(autouse=True)
def _runtime_packages(monkeypatch):
    from ..packages import find_repo_root
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(find_repo_root(Path(__file__)) / "packages"))


class _FakeContextManager:
    def __init__(self, value: Any = None):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        return None


def _pack_text(
    *,
    max_tool_calls: int | None = 3,
    batch_enabled: bool = False,
    second_binding: bool = False,
) -> str:
    max_tool_calls_yaml = (
        f"        max_tool_calls: {max_tool_calls}\n"
        if max_tool_calls is not None
        else ""
    )
    batch_yaml = (
        "        batch:\n"
        "          enabled: true\n"
        "          family: fixture_gene_reference\n"
        if batch_enabled
        else ""
    )
    second_binding_yaml = (
        """
      - binding_id: fixture.symbol_lookup
        display_name: Symbol lookup
        validator_agent:
          package_id: fixture.validators
          agent_id: symbol_validator
        applies_to:
          domain_pack_id: fixture.dispatch
          object_types:
            - GeneAssertion
          field_paths:
            - gene.symbol
        required: true
        blocking: false
        batch:
          enabled: true
          family: fixture_symbol_reference
        input_fields:
          symbol:
            source: payload
            path: gene.symbol
        expected_result_fields:
          symbol: gene.symbol
"""
        if second_binding
        else ""
    )
    return f"""
pack_id: fixture.dispatch
display_name: Fixture Dispatch Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: GeneAssertionPayload
    display_name: Gene assertion payload
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    model_ref: GeneAssertionPayload
    fields:
      - field_path: gene.identifier
        field_type: string
      - field_path: gene.symbol
        field_type: string
  - object_type: Gene
    display_name: Gene reference
    metadata:
      object_role: validated_reference
    fields:
      - field_path: identifier
        field_type: string
        required: true
      - field_path: symbol
        field_type: string
        required: true
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.identifier_lookup
        display_name: Identifier lookup
        validator_agent:
          package_id: fixture.validators
          agent_id: identifier_validator
        applies_to:
          domain_pack_id: fixture.dispatch
          object_types:
            - GeneAssertion
          field_paths:
            - gene.identifier
        required: true
        blocking: true
{max_tool_calls_yaml}        input_fields:
          identifier:
            source: payload
            path: gene.identifier
          evidence_quote:
            source: evidence_record
            path: quote
            required: false
        expected_result_fields:
          identifier: gene.identifier
          symbol: gene.symbol
{batch_yaml}{second_binding_yaml}
""".strip()


def _loaded_pack(
    tmp_path: Path,
    *,
    max_tool_calls: int | None = 3,
    batch_enabled: bool = False,
    second_binding: bool = False,
) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.dispatch"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        _pack_text(
            max_tool_calls=max_tool_calls,
            batch_enabled=batch_enabled,
            second_binding=second_binding,
        ),
        encoding="utf-8",
    )
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _group_scoped_pack(
    tmp_path: Path,
    *,
    ambiguous_second_binding: bool = False,
    allow_cross_provider: bool = False,
) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.group-scoped-dispatch"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    text = _pack_text().replace(
        "        blocking: true\n",
        """        blocking: true
        group_scope:
          required_any_active_group:
            - ZFIN
          provider_value_field_paths:
            - data_provider.abbreviation
          allowed_provider_values:
            - ZFIN
          allow_cross_provider: false
""",
        1,
    )
    if allow_cross_provider:
        text = text.replace("          allow_cross_provider: false", "          allow_cross_provider: true", 1)
    if ambiguous_second_binding:
        text += """
      - binding_id: fixture.identifier_lookup_wb
        display_name: WB identifier lookup
        validator_agent:
          package_id: fixture.validators
          agent_id: identifier_validator
        applies_to:
          domain_pack_id: fixture.dispatch
          object_types:
            - GeneAssertion
          field_paths:
            - gene.identifier
        required: true
        blocking: false
        group_scope:
          required_any_active_group:
            - WB
        input_fields:
          identifier:
            source: payload
            path: gene.identifier
        expected_result_fields:
          identifier: gene.identifier
"""
    metadata_path.write_text(text, encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _empty_dispatch_pack(tmp_path: Path) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.empty-dispatch"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        """
pack_id: fixture.empty_dispatch
display_name: Fixture Empty Dispatch Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: ThingPayload
    display_name: Thing payload
object_definitions:
  - object_type: Thing
    display_name: Thing
    model_ref: ThingPayload
    fields:
      - field_path: label
        field_type: string
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.structural_data_check
        display_name: Data check
        validator_agent:
          package_id: fixture.validators
          agent_id: thing_validator
        applies_to:
          domain_pack_id: fixture.empty_dispatch
          object_types:
            - Thing
        input_fields: {}
        expected_result_fields: {}
        required: true
        blocking: false
        allow_opt_out: true
        definition_state: in_development
""".strip(),
        encoding="utf-8",
    )
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _alliance_gene_pack() -> LoadedDomainPack:
    repo_root = Path(__file__).resolve().parents[5]
    pack_path = repo_root / "packages" / "alliance" / "domain_packs" / "gene"
    metadata_path = pack_path / "domain_pack.yaml"
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _alliance_gene_expression_pack() -> LoadedDomainPack:
    repo_root = Path(__file__).resolve().parents[5]
    pack_path = (
        repo_root / "packages" / "alliance" / "domain_packs" / "gene_expression"
    )
    metadata_path = pack_path / "domain_pack.yaml"
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _envelope(
    *,
    identifier: str = "BAD:0001",
    evidence_records: list[dict[str, Any]] | None = None,
) -> DomainEnvelope:
    payload: dict[str, Any] = {
        "gene": {
            "identifier": identifier,
            "symbol": "ABC-1",
        }
    }
    if evidence_records is not None:
        payload["evidence_records"] = evidence_records
    return DomainEnvelope(
        envelope_id="dispatch-env",
        domain_pack_id="fixture.dispatch",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id="object-1",
                payload=payload,
                evidence_record_ids=[
                    record["evidence_record_id"]
                    for record in evidence_records or []
                    if "evidence_record_id" in record
                ],
            )
        ],
    )


def _multi_object_envelope(
    identifiers: list[str],
    *,
    evidence_quotes: list[str] | None = None,
) -> DomainEnvelope:
    objects = []
    for index, identifier in enumerate(identifiers, start=1):
        payload: dict[str, Any] = {
            "gene": {
                "identifier": identifier,
                "symbol": f"ABC-{index}",
            }
        }
        if evidence_quotes is not None:
            evidence_record_id = f"evidence-{index}"
            payload["evidence_records"] = [
                {
                    "evidence_record_id": evidence_record_id,
                    "quote": evidence_quotes[index - 1],
                }
            ]
            evidence_record_ids = [evidence_record_id]
        else:
            evidence_record_ids = []
        objects.append(
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                pending_ref_id=f"object-{index}",
                payload=payload,
                evidence_record_ids=evidence_record_ids,
            )
        )
    return DomainEnvelope(
        envelope_id="dispatch-env",
        domain_pack_id="fixture.dispatch",
        extracted_objects=objects,
    )


def _staged_value(mention: str, *identity_keys: str) -> dict[str, Any]:
    """A gene-expression value staged for validation (ALL-1283): paper wording, no identity."""

    return {
        **{key: None for key in identity_keys},
        "mention": mention,
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }


def _gene_expression_envelope() -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="gene-expression-env",
        domain_pack_id="agr.alliance.gene_expression",
        metadata={"document_id": "paper-tmem67"},
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneExpressionAnnotation",
                pending_ref_id="gene-expression-1",
                object_role="curatable_unit",
                payload={
                    # The builder's exact provider-list lookup resolved the data provider.
                    "data_provider": {
                        "abbreviation": "MGI",
                        "mention": "MGI",
                        "resolution_state": "resolved",
                        "lookup_outcome": "matched",
                        "validator_explanation": None,
                    },
                    "expression_annotation_subject": _staged_value(
                        "Tmem67", "primary_external_id", "gene_symbol"
                    ),
                    "when_expressed_stage_name": "TS26",
                    "expression_pattern": {
                        "when_expressed": {
                            "developmental_stage_start": _staged_value("TS26", "curie", "name"),
                        },
                        "where_expressed": {
                            "anatomical_structure": _staged_value("metanephros", "curie", "name"),
                        },
                    },
                    "relation": _staged_value("is_expressed_in", "name", "vocabulary", "id"),
                    "single_reference": {
                        "pmid": "PMID:203506",
                        "title": "Paper supplied title",
                    },
                },
            )
        ],
    )


def _gene_mentions_envelope(mentions: list[str]) -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="gene-env",
        domain_pack_id="gene",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="gene_mention_evidence",
                pending_ref_id=f"gene-mention-{index}",
                object_role="validated_reference",
                payload={
                    "mention": mention,
                    "species": "Drosophila melanogaster",
                    "taxon_hint": "NCBITaxon:7227",
                    "data_provider_hint": "FlyBase",
                    "identity_resolution_notes": [
                        f"Paper-backed context for {mention}."
                    ],
                    "verified_quote": f"{mention} was discussed in the paper.",
                    "evidence_records": [{
                        "evidence_record_id": f"gene-evidence-{index}",
                        "verified_quote": f"{mention} was discussed in the paper.",
                    }],
                },
            )
            for index, mention in enumerate(mentions, start=1)
        ],
    )


def _result_payload(
    request,
    *,
    status: str = "resolved",
    resolved_values: dict[str, Any] | None = None,
    missing_expected_fields: list[str] | None = None,
    outcome: str = "success",
) -> dict[str, Any]:
    resolved_values = resolved_values if resolved_values is not None else {
        "identifier": "AGR:0001",
        "symbol": "ABC-1",
    }
    return {
        "status": status,
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": resolved_values,
        "resolved_objects": [
            {
                "object_type": "Gene",
                "canonical_id": resolved_values.get("identifier"),
                "payload": dict(resolved_values),
            }
        ]
        if status == "resolved"
        else [],
        "missing_expected_fields": missing_expected_fields or [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "fixture_lookup",
                "method": "exact_identifier",
                "query": {"identifier": request.selected_inputs.get("identifier")},
                "result_count": 1 if outcome == "success" else 0,
                "outcome": outcome,
            }
        ],
        "curator_message": None,
        "explanation": "Fixture validator result.",
    }


def _validation_request() -> DomainValidationRequest:
    return DomainValidationRequest(
        request_id="domain-validation:test",
        validator_binding_id="fixture.identifier_lookup",
        validator_agent=ValidatorAgentRef(
            package_id="fixture.validators",
            agent_id="identifier_validator",
        ),
        target=ValidationTarget(
            domain_pack_id="fixture.dispatch",
            object_type="GeneAssertion",
            field_path="gene.identifier",
        ),
        selected_inputs={"identifier": "BAD:0001"},
        expected_result_fields={"identifier": "gene.identifier"},
    )


def _verbose_validation_request() -> DomainValidationRequest:
    return DomainValidationRequest(
        request_id="domain-validation:verbose",
        validator_binding_id="fixture.identifier_lookup",
        validator_agent=ValidatorAgentRef(
            package_id="fixture.validators",
            agent_id="identifier_validator",
        ),
        target=ValidationTarget(
            domain_pack_id="fixture.dispatch",
            object_type="GeneAssertion",
            field_path="gene.identifier",
            input_values={"identifier": "BAD:0001", "evidence_quote": "crb quote"},
        ),
        selected_inputs={"identifier": "BAD:0001", "evidence_quote": "crb quote"},
        input_selectors={
            "identifier": {"source": "payload", "path": "gene.identifier"},
            "evidence_quote": {"source": "evidence", "path": "verified_quote"},
        },
        evidence=[
            {
                "evidence_record_id": "evidence-1",
                "verified_quote": "crb quote",
                "chunk_id": "chunk-1",
            }
        ],
        expected_result_fields={"identifier": "gene.identifier"},
    )


def _array_terms_validation_request() -> DomainValidationRequest:
    return DomainValidationRequest(
        request_id="domain-validation:array-terms",
        validator_binding_id="fixture.ontology_terms_lookup",
        validator_agent=ValidatorAgentRef(
            package_id="fixture.validators",
            agent_id="ontology_term_validation",
        ),
        target=ValidationTarget(
            domain_pack_id="fixture.dispatch",
            object_type="GeneExpressionAnnotation",
            field_path="expression_pattern.where_expressed.cellular_component_qualifiers",
            expected_fields=["terms"],
            input_values={
                "terms": [
                    {"name": "nuclear lumen"},
                    {"name": "nucleoplasm"},
                ],
                "ontology_family": "go",
                "go_aspect": "cellular_component",
            },
        ),
        selected_inputs={
            "terms": [
                {"name": "nuclear lumen"},
                {"name": "nucleoplasm"},
            ],
            "ontology_family": "go",
            "go_aspect": "cellular_component",
            "lookup_method": "search_go_terms",
        },
        expected_result_fields={
            "terms": "expression_pattern.where_expressed.cellular_component_qualifiers"
        },
    )


def _single_result_finding(result):
    return next(
        finding
        for finding in result.envelope.validation_findings
        if finding.code in {
            "domain_pack.validator_resolved",
            "domain_pack.validator_unresolved",
            "domain_pack.validator_error",
        }
    )


def test_guidance_survives_compaction_and_changes_dedupe_identity():
    from src.lib.domain_packs.materialization import _validation_request_finding_payload
    request = _verbose_validation_request()
    changed = request.model_copy(update={"validation_guidance": "Check conditional allele compatibility with the Cyagen source."})
    assert _validator_request_dedupe_key(request) != _validator_request_dedupe_key(changed)
    assert validator_request_payload_for_agent(changed)["validation_guidance"] == changed.validation_guidance
    assert _validation_request_finding_payload(changed)["validation_guidance"] == changed.validation_guidance
    assert request.validation_guidance is None


def test_validator_guidance_is_advisory_in_single_and_batch_prompts():
    from types import SimpleNamespace
    from src.lib.domain_packs.validator_dispatch import _append_validator_source_context_instructions
    for batch in (False, True):
        agent = SimpleNamespace(instructions="Existing validator policy")
        _append_validator_source_context_instructions(agent, batch=batch, runtime_context=None)
        assert "Existing validator policy" in agent.instructions
        assert "Never resolve an identity solely from guidance" in agent.instructions
        assert "higher-priority policy" in agent.instructions
        assert "Do not apply one request's guidance to another" in agent.instructions


def test_validator_request_payload_for_agent_omits_semantic_duplicates():
    request = _verbose_validation_request()

    payload = validator_request_payload_for_agent(request)

    assert payload["selected_inputs"] == request.selected_inputs
    assert "validation_guidance" not in payload
    assert "input_selectors" not in payload
    assert "evidence" not in payload
    assert "input_values" not in payload["target"]
    assert payload["evidence_summary"] == {
        "evidence_count": 1,
        "evidence_record_ids": ["evidence-1"],
    }
    assert payload["runtime_compaction"]["omitted_fields"] == [
        "input_selectors",
        "target.input_values",
        "evidence",
    ]


def test_validator_request_payload_preserves_long_selected_input_scalars():
    request = _verbose_validation_request().model_copy(
        update={
            "selected_inputs": {
                "identifier": "BAD:0001",
                "evidence_quote": "paper-supported quote " * 200,
            }
        },
        deep=True,
    )

    payload = validator_request_payload_for_agent(request)

    assert payload["selected_inputs"] == request.selected_inputs
    assert payload["selected_inputs"]["evidence_quote"].endswith(
        "paper-supported quote "
    )


def test_validator_request_payload_reports_runtime_capabilities_for_scoped_evidence():
    request = _validation_request().model_copy(
        update={
            "target": ValidationTarget(
                domain_pack_id="fixture.dispatch",
                object_type="GeneAssertion",
                object_id="object-1",
                field_path="gene.identifier",
            ),
            "selected_inputs": {
                "identifier": "BAD:0001",
                "evidence_quotes": [
                    {
                        "evidence_record_id": "evidence-1",
                        "field_path": "gene.identifier",
                        "verified_quote": "BAD:0001 was reported in the paper.",
                    }
                ],
            },
            "evidence": [
                {
                    "evidence_record_id": "evidence-1",
                    "verified_quote": "BAD:0001 was reported in the paper.",
                    "chunk_id": "chunk-1",
                    "document_id": "doc-123",
                }
            ],
        },
        deep=True,
    )

    payload = validator_request_payload_for_agent(
        request,
        runtime_context=ValidatorRuntimeContext(
            document_id="doc-123",
            user_id="user-1",
        ),
    )

    assert payload["validator_runtime_capabilities"] == {
        "paper_search_available": True,
        "scoped_evidence_update_available": True,
        "allowed_evidence_record_ids": ["evidence-1"],
        "target_object_id": "object-1",
        "target_field_path": "gene.identifier",
    }


def test_validator_runtime_capabilities_scope_from_request_evidence_for_bare_quote():
    request = _verbose_validation_request().model_copy(
        update={
            "target": ValidationTarget(
                domain_pack_id="fixture.dispatch",
                object_type="GeneAssertion",
                object_id="object-1",
                field_path="gene.identifier",
                input_values={"identifier": "BAD:0001", "evidence_quote": "crb quote"},
            ),
        },
        deep=True,
    )

    payload = validator_request_payload_for_agent(
        request,
        runtime_context=ValidatorRuntimeContext(
            document_id="doc-123",
            user_id="user-1",
        ),
    )

    assert payload["validator_runtime_capabilities"] == {
        "paper_search_available": True,
        "scoped_evidence_update_available": True,
        "allowed_evidence_record_ids": ["evidence-1"],
        "target_object_id": "object-1",
        "target_field_path": "gene.identifier",
    }


def test_validator_request_dedupe_key_includes_field_and_evidence_context():
    base_request = _validation_request().model_copy(
        update={
            "target": ValidationTarget(
                domain_pack_id="fixture.dispatch",
                object_type="GeneAssertion",
                object_id="object-1",
                field_path="gene.identifier",
            ),
            "selected_inputs": {
                "identifier": "BAD:0001",
                "evidence_quotes": [
                    {
                        "evidence_record_id": "evidence-1",
                        "field_path": "gene.identifier",
                        "verified_quote": "First quote.",
                    }
                ],
            },
        },
        deep=True,
    )
    different_evidence = base_request.model_copy(
        update={
            "selected_inputs": {
                "identifier": "BAD:0001",
                "evidence_quotes": [
                    {
                        "evidence_record_id": "evidence-2",
                        "field_path": "gene.identifier",
                        "verified_quote": "Second quote.",
                    }
                ],
            }
        },
        deep=True,
    )
    different_field = base_request.model_copy(
        update={
            "target": ValidationTarget(
                domain_pack_id="fixture.dispatch",
                object_type="GeneAssertion",
                object_id="object-1",
                field_path="gene.symbol",
            ),
        },
        deep=True,
    )

    assert _validator_request_dedupe_key(base_request) != _validator_request_dedupe_key(
        different_evidence
    )
    assert _validator_request_dedupe_key(base_request) != _validator_request_dedupe_key(
        different_field
    )


def test_apply_validator_evidence_updates_replaces_matching_envelope_records():
    envelope = _envelope(
        evidence_records=[
            {
                "evidence_record_id": "evidence-1",
                "entity": "BAD:0001",
                "verified_quote": "Previous quote.",
                "page": 3,
                "section": "Results",
                "chunk_id": "chunk-1",
            }
        ]
    )
    request = _validation_request().model_copy(
        update={
            "evidence": [
                {
                    "evidence_record_id": "evidence-1",
                    "entity": "BAD:0001",
                    "verified_quote": "Updated quote.",
                    "page": 4,
                    "section": "Results",
                    "chunk_id": "chunk-2",
                    "updated_at": "2026-06-08T00:00:00+00:00",
                    "evidence_revision_history": [
                        {
                            "revision": 1,
                            "previous_source": {
                                "verified_quote": "Previous quote.",
                                "chunk_id": "chunk-1",
                            },
                        }
                    ],
                }
            ]
        },
        deep=True,
    )

    updated = _apply_validator_evidence_updates_to_envelope(
        envelope,
        cast(Any, [SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={})))]),
    )

    evidence_record = updated.extracted_objects[0].payload["evidence_records"][0]
    assert evidence_record["verified_quote"] == "Updated quote."
    assert evidence_record["chunk_id"] == "chunk-2"
    assert evidence_record["evidence_revision_history"][0]["previous_source"] == {
        "verified_quote": "Previous quote.",
        "chunk_id": "chunk-1",
    }


def test_validator_batch_summary_reports_payload_sizes_and_large_scalars():
    request = _verbose_validation_request().model_copy(
        update={
            "selected_inputs": {
                "identifier": "BAD:0001",
                "evidence_quote": "long quote " * 200,
            }
        },
        deep=True,
    )
    binding = SimpleNamespace(
        binding_id="fixture.identifier_lookup",
        batch_family="fixture.family",
        validator_agent=None,
    )
    job = SimpleNamespace(
        request=request,
        match=SimpleNamespace(binding=binding),
    )

    summary = _validator_batch_summary(cast(Any, [job]))

    assert summary["payload_summary"]["request_payload_json_chars"] > 0
    assert summary["payload_summary"]["omitted_target_input_values_count"] == 1
    large_paths = summary["payload_summary"]["large_selected_input_scalar_paths"]
    assert large_paths[0]["request_id"] == request.request_id
    assert large_paths[0]["paths"][0]["path"] == "selected_inputs.evidence_quote"


def _parent_validator_findings(result):
    return [
        finding
        for finding in result.envelope.validation_findings
        if finding.code
        in {
            "domain_pack.validator_resolved",
            "domain_pack.validator_unresolved",
            "domain_pack.validator_error",
        }
        and not finding.details.get("validation_metadata", {}).get(
            "generated_from_expected_result_field"
        )
    ]


def test_dispatch_active_binding_sends_typed_request_and_appends_resolved_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)
    captured = {}

    def _runner(request, *, binding):
        captured["request"] = request
        captured["binding"] = binding
        return _result_payload(request)

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
        source_envelope_revision=7,
    )

    request = captured["request"]
    assert captured["binding"].max_tool_calls == 3
    assert request.validator_binding_id == "fixture.identifier_lookup"
    assert request.validator_agent.package_id == "fixture.validators"
    assert request.selected_inputs == {"identifier": "BAD:0001"}
    assert request.target.input_values == request.selected_inputs
    assert "evidence_quote" in request.input_selectors

    finding = _single_result_finding(result)
    assert finding.status.value == "resolved"
    assert finding.code == "domain_pack.validator_resolved"
    assert finding.field_ref is not None
    assert finding.field_ref.field_path == "gene.identifier"
    assert finding.details["validation_metadata"]["source_envelope_revision"] == 7
    assert finding.details["validation_result"]["resolved_objects"][0]["object_type"] == "Gene"
    materialized_gene = next(
        domain_object
        for domain_object in result.envelope.extracted_objects
        if domain_object.object_type == "Gene"
    )
    assert materialized_gene.status.value == "validated"
    assert materialized_gene.payload == {
        "identifier": "AGR:0001",
        "symbol": "ABC-1",
    }
    assert result.envelope.extracted_objects[0].object_refs == [
        materialized_gene.to_object_ref()
    ]
    assert result.validator_results[0].status == "resolved"


@pytest.mark.parametrize("state", ["replaced", "supplemental", "skipped"])
def test_flow_selection_suppresses_upstream_before_any_model_call(tmp_path, state):
    from src.lib.domain_packs.flow_validator_selection import (
        set_flow_validator_selections, reset_flow_validator_selections,
    )
    pack = _loaded_pack(tmp_path, second_binding=True)
    calls = []

    def runner(request, *, binding):
        calls.append(request.validator_binding_id)
        return _result_payload(request)

    token = set_flow_validator_selections([{
        "binding_id": "fixture.identifier_lookup", "state": state,
        "validator_node_id": "custom-validator" if state != "skipped" else None,
    }])
    try:
        # The real extraction dispatcher crosses an asyncio.to_thread boundary.
        async def dispatch():
            return await asyncio.to_thread(dispatch_active_validator_bindings,
                _envelope(), pack, runner=runner)
        result = asyncio.run(dispatch())
    finally:
        reset_flow_validator_selections(token)
    assert calls == ["fixture.symbol_lookup"]
    audit = result.envelope.metadata["suppressed_upstream_validators"]
    assert audit[0]["validator_binding_id"] == "fixture.identifier_lookup"
    assert audit[0]["reason"] == ("flow_skip" if state == "skipped" else "custom_replacement")
    assert audit[0]["target"]["pending_ref_id"] == "object-1"
    calls.clear()
    dispatch_active_validator_bindings(_envelope(), pack, runner=runner)
    assert set(calls) == {"fixture.identifier_lookup", "fixture.symbol_lookup"}


@pytest.mark.parametrize("custom_fails", [False, True])
def test_extractor_to_flow_runs_custom_once_without_standard_fallback(tmp_path, monkeypatch, custom_fails):
    from src.lib.flows import executor
    from src.lib.domain_packs.flow_validator_selection import (
        set_flow_validator_selections, reset_flow_validator_selections,
    )
    from src.lib.domain_packs.materialization import materialize_validator_results_into_envelope
    pack = _loaded_pack(tmp_path)
    groups = [{"group_id": "custom", "binding_id": "fixture.identifier_lookup",
               "state": "replaced", "validator_node_id": "custom-node"}]
    calls = []

    def standard(*args, **kwargs):
        calls.append("standard")
        raise AssertionError("standard must not run")

    async def custom(request, **kwargs):
        calls.append("custom")
        assert request.validator_agent.agent_id == "custom-agent"
        if custom_fails:
            raise RuntimeError("custom source failed")
        return _result_payload(request)

    monkeypatch.setattr(executor, "_run_custom_flow_validator_agent", custom)
    token = set_flow_validator_selections(groups)
    try:
        upstream = dispatch_active_validator_bindings(_envelope(), pack, runner=standard)
    finally:
        reset_flow_validator_selections(token)
    assert upstream.validator_agent_run_count == 0
    flow = SimpleNamespace(flow_definition={"nodes": [{"id": "custom-node", "data": {"agent_id": "custom-agent"}}]})
    units, findings, audit = asyncio.run(executor._collect_flow_validator_materialization_inputs(
        source_envelope=upstream.envelope, source_envelope_revision=1,
        registry=upstream.registry, groups=groups, flow=flow, agent_context={},
    ))
    assert calls == ["custom"]
    assert not findings
    assert len(units) == 1
    assert audit[-1]["validator_agent"]["agent_id"] == "custom-agent"
    assert audit[-1]["status"] == ("unresolved" if custom_fails else "resolved")
    materialized = materialize_validator_results_into_envelope(
        upstream.envelope, pack.metadata, units, actor_id="flow", source_envelope_revision=1,
    )
    results = [f.details["validation_result"] for f in materialized.envelope.validation_findings
               if "validation_result" in f.details]
    assert results
    assert all(r["validator_agent"]["agent_id"] == "custom-agent" for r in results)


def test_group_scope_normalizes_and_matching_authenticated_group_dispatches(
    tmp_path: Path,
):
    pack = _group_scoped_pack(tmp_path)
    binding = pack.metadata.metadata["validator_bindings"]["active"][0]
    assert binding["group_scope"] == {
        "required_any_active_group": ["ZFIN"],
        "provider_value_field_paths": ["data_provider.abbreviation"],
        "allowed_provider_values": ["ZFIN"],
        "allow_cross_provider": False,
    }
    envelope = _envelope().model_copy(deep=True)
    envelope.extracted_objects[0].payload["data_provider"] = {
        "abbreviation": "ZFIN"
    }
    calls: list[str] = []

    def _runner(request, *, binding):
        calls.append(binding.binding_id)
        return _result_payload(request)

    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=_runner,
        runtime_context=ValidatorRuntimeContext(
            authenticated_groups=("ZFIN",),
        ),
    )

    assert calls == ["fixture.identifier_lookup"]
    assert result.binding_audit[0]["eligibility_reason"] == "group_scope_satisfied"
    assert result.binding_audit[0]["authenticated_groups"] == ["ZFIN"]
    finding = _single_result_finding(result)
    assert finding.details["validation_metadata"]["dispatch_context"] == {
        "authenticated_groups": ["ZFIN"],
        "group_context_identity": '["ZFIN"]',
    }
    assert "authenticated_group_snapshot" not in result.envelope.metadata
    assert result.envelope.authenticated_context is None


def test_group_scope_missing_or_mismatching_context_skips_deterministically(
    tmp_path: Path,
):
    pack = _group_scoped_pack(tmp_path)

    missing = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=lambda *_args, **_kwargs: pytest.fail("scoped binding ran"),
    )
    missing_finding = next(
        finding
        for finding in missing.appended_findings
        if finding.code == "domain_pack.validator_group_context_missing"
    )
    assert missing_finding.field_ref is not None
    assert missing_finding.field_ref.field_path == "gene.identifier"
    assert missing.binding_audit[0]["eligibility_reason"] == "missing_context"

    mismatching = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=lambda *_args, **_kwargs: pytest.fail("scoped binding ran"),
        runtime_context=ValidatorRuntimeContext(authenticated_groups=("MGI",)),
    )
    assert mismatching.validator_agent_run_count == 0
    assert mismatching.appended_findings == ()
    assert mismatching.binding_audit[0]["eligibility_reason"] == "group_not_satisfied"


def test_group_scope_multiple_groups_runs_non_conflicting_and_reports_provider_mismatch(
    tmp_path: Path,
):
    pack = _group_scoped_pack(tmp_path)
    envelope = _envelope().model_copy(deep=True)
    envelope.extracted_objects[0].payload["data_provider"] = {
        "abbreviation": "MGI"
    }

    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=lambda request, *, binding: _result_payload(request),
        runtime_context=ValidatorRuntimeContext(
            authenticated_groups=("WB", "ZFIN"),
        ),
    )

    assert result.validator_agent_run_count == 1
    mismatch = next(
        finding
        for finding in result.appended_findings
        if finding.code == "domain_pack.validator_provider_group_mismatch"
    )
    assert mismatch.field_ref is not None
    assert mismatch.field_ref.field_path == "data_provider.abbreviation"
    assert mismatch.details["provider_value"] == "MGI"
    assert envelope.extracted_objects[0].payload["data_provider"]["abbreviation"] == "MGI"


def test_group_scope_explicit_cross_provider_policy_suppresses_mismatch(tmp_path: Path):
    pack = _group_scoped_pack(tmp_path, allow_cross_provider=True)
    envelope = _envelope().model_copy(deep=True)
    envelope.extracted_objects[0].payload["data_provider"] = {
        "abbreviation": "MGI"
    }

    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=lambda request, *, binding: _result_payload(request),
        runtime_context=ValidatorRuntimeContext(authenticated_groups=("ZFIN",)),
    )

    assert result.validator_agent_run_count == 1
    assert not any(
        finding.code == "domain_pack.validator_provider_group_mismatch"
        for finding in result.appended_findings
    )


def test_group_scope_same_field_ambiguity_blocks_only_scoped_bindings(tmp_path: Path):
    pack = _group_scoped_pack(tmp_path, ambiguous_second_binding=True)

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=lambda *_args, **_kwargs: pytest.fail("ambiguous binding ran"),
        runtime_context=ValidatorRuntimeContext(
            authenticated_groups=("WB", "ZFIN"),
        ),
    )

    assert result.validator_agent_run_count == 0
    ambiguity_findings = [
        finding
        for finding in result.appended_findings
        if finding.code == "domain_pack.validator_group_scope_ambiguous"
    ]
    assert ambiguity_findings
    assert all(
        entry["eligibility_reason"] == "same_field_ambiguity"
        for entry in result.binding_audit
    )


def test_generic_binding_runs_with_authenticated_empty_group_set(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=lambda request, *, binding: _result_payload(request),
        runtime_context=ValidatorRuntimeContext(authenticated_groups=()),
    )

    assert result.validator_agent_run_count == 1
    assert result.binding_audit[0]["eligibility_reason"] == "generic"


def test_dispatch_default_runner_uses_worker_thread_from_running_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack = _loaded_pack(tmp_path)
    event_loop_thread_id = threading.get_ident()
    captured = {}

    def _fake_package_validator(request, *, binding):
        from src.lib.observability.cost_context import current_cost_context
        captured["cost_context"] = current_cost_context()
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        captured["thread_id"] = threading.get_ident()
        captured["binding"] = binding
        return _result_payload(request)

    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch.run_package_scoped_validator_agent",
        _fake_package_validator,
    )

    async def _dispatch_inside_event_loop():
        return dispatch_active_validator_bindings(
            _envelope(),
            pack,
            source_envelope_revision=3,
        )

    from src.lib.observability.cost_context import cost_scope, current_cost_context
    with cost_scope({"run_id": "parent-run", "paper": {"namespace": "fixture", "id": "A"}}):
        result = asyncio.run(_dispatch_inside_event_loop())
    assert captured["cost_context"]["run_id"] == "parent-run"
    assert captured["cost_context"]["paper"]["id"] == "A"
    assert current_cost_context() == {}

    assert captured["thread_id"] != event_loop_thread_id
    assert captured["binding"].binding_id == "fixture.identifier_lookup"
    finding = _single_result_finding(result)
    assert finding.status.value == "resolved"


def test_dispatch_skips_active_binding_without_inputs_or_expected_results(
    tmp_path: Path,
):
    pack = _empty_dispatch_pack(tmp_path)
    envelope = DomainEnvelope(
        envelope_id="empty-dispatch-env",
        domain_pack_id="fixture.empty_dispatch",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Thing",
                pending_ref_id="thing-1",
                payload={"label": "empty dispatch"},
            )
        ],
    )

    def _runner(request, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("empty structural binding should not dispatch")

    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=_runner,
    )

    assert [match.binding.binding_id for match in result.matched_bindings] == [
        "fixture.structural_data_check"
    ]
    assert result.validator_results == ()
    assert result.appended_findings == ()


def test_dispatch_active_binding_returns_unresolved_validator_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            status="unresolved",
            resolved_values={},
            missing_expected_fields=["identifier", "symbol"],
            outcome="not_found",
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert finding.status.value == "open"
    assert finding.severity.value == "blocker"
    assert finding.code == "domain_pack.validator_unresolved"
    # The lookup outcome says why the fields are missing.
    assert finding.details["failure_classification"] == "not_found"
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "not_found"
    assert result.validator_results[0].status == "unresolved"


def test_provider_taxon_preflight_requires_explicit_binding_policy():
    request = _validation_request().model_copy(
        update={
            "selected_inputs": {
                "ontology_family": "project-neutral-fixture",
                "label": "fixture label",
                "taxon_id": "NCBITaxon:9999",
                "provider_taxon_ontology_mappings": [
                    {
                        "data_provider": "FIXTURE",
                        "taxon_id": "NCBITaxon:1111",
                        "ontology_term_type": "FixtureTerm",
                        "accepted_prefixes": ["FIX"],
                    }
                ],
            }
        },
        deep=True,
    )

    assert preflight_unresolved_validator_result(request) is None

    result = preflight_unresolved_validator_result(
        request,
        policy="provider_taxon_mapping_required",
    )

    assert result is not None
    assert result.status == "unresolved"
    assert result.lookup_attempts[0].method == "unsupported_provider_taxon_mapping"
    assert "Phenotype" not in (result.explanation or "")


def test_dispatch_deduplicates_equivalent_identity_requests_before_validation(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)
    calls = []

    def _runner(request, *, binding):
        calls.append(request.request_id)
        return _result_payload(request)

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(
            ["BAD:0001", "BAD:0001"],
            evidence_quotes=["First paper quote.", "Second paper quote."],
        ),
        pack,
        runner=_runner,
    )

    assert len(calls) == 1
    assert len(result.validator_results) == 2
    assert {item.status for item in result.validator_results} == {"resolved"}
    assert [
        finding.code for finding in _parent_validator_findings(result)
    ] == [
        "domain_pack.validator_resolved",
        "domain_pack.validator_resolved",
    ]
    materialized_gene = next(
        domain_object
        for domain_object in result.envelope.extracted_objects
        if domain_object.object_type == "Gene"
    )
    assert all(
        materialized_gene.to_object_ref() in domain_object.object_refs
        for domain_object in result.envelope.extracted_objects
        if domain_object.object_type == "GeneAssertion"
    )


def test_dispatch_runs_unique_validator_requests_in_parallel(tmp_path: Path):
    pack = _loaded_pack(tmp_path)
    barrier = threading.Barrier(2)
    seen_thread_ids: set[int] = set()

    def _runner(request, *, binding):
        seen_thread_ids.add(threading.get_ident())
        barrier.wait(timeout=1)
        return _result_payload(request)

    started_at = time.monotonic()
    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        runner=_runner,
        max_parallel_validators=2,
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.9
    assert len(seen_thread_ids) == 2
    assert len(result.validator_results) == 2
    assert {item.status for item in result.validator_results} == {"resolved"}


@pytest.mark.parametrize("guidance", [None, "Check the organism context against the database."])
def test_dispatch_batches_opted_in_validator_requests(tmp_path: Path, guidance):
    pack = _loaded_pack(tmp_path, batch_enabled=True)
    batch_calls: list[list[str]] = []

    def _single_runner(request, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("batch-enabled binding should use the batch runner")

    def _batch_runner(jobs, *, binding):
        batch_calls.append([job.request.selected_inputs["identifier"] for job in jobs])
        assert [job.request.validation_guidance for job in jobs] == [guidance, None]
        assert validator_request_payload_for_agent(jobs[0].request).get("validation_guidance") == guidance
        return [
            _result_payload(
                job.request,
                resolved_values={
                    "identifier": f"AGR:{index:04d}",
                    "symbol": f"ABC-{index}",
                },
            )
            for index, job in enumerate(jobs, start=1)
        ]

    envelope = _multi_object_envelope(["BAD:0001", "BAD:0002"])
    envelope.extracted_objects[0].validation_guidance = guidance
    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=_single_runner,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert batch_calls == [["BAD:0001", "BAD:0002"]]
    assert result.validator_agent_run_count == 1
    assert result.batch_validator_run_count == 1
    assert len(result.validator_results) == 2
    assert [item.status for item in result.validator_results] == [
        "resolved",
        "resolved",
    ]


def test_dispatch_batches_after_dedupe_and_remaps_to_original_requests(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path, batch_enabled=True)
    batch_request_ids: list[list[str]] = []

    def _batch_runner(jobs, *, binding):
        batch_request_ids.append([job.request.request_id for job in jobs])
        return [_result_payload(job.request) for job in jobs]

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(
            ["BAD:0001", "BAD:0001", "BAD:0002"],
            evidence_quotes=[
                "First paper quote.",
                "Second paper quote.",
                "Third paper quote.",
            ],
        ),
        pack,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert len(batch_request_ids) == 1
    assert len(batch_request_ids[0]) == 2
    assert result.validator_agent_run_count == 1
    assert result.batch_validator_run_count == 1
    assert len(result.validator_results) == 3
    assert result.validator_results[0].request_id != result.validator_results[1].request_id
    assert {item.status for item in result.validator_results} == {"resolved"}


def test_dispatch_batches_mixed_validator_agents_separately_and_preserves_order(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path, batch_enabled=True, second_binding=True)
    batch_groups: list[tuple[str, list[str]]] = []

    def _batch_runner(jobs, *, binding):
        batch_groups.append(
            (
                binding.validator_agent.agent_id,
                [job.request.validator_binding_id for job in jobs],
            )
        )
        if binding.binding_id == "fixture.symbol_lookup":
            return [
                _result_payload(
                    job.request,
                    resolved_values={"symbol": job.request.selected_inputs["symbol"]},
                )
                for job in jobs
            ]
        return [_result_payload(job.request) for job in jobs]

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert batch_groups == [
        (
            "identifier_validator",
            ["fixture.identifier_lookup", "fixture.identifier_lookup"],
        ),
        ("symbol_validator", ["fixture.symbol_lookup", "fixture.symbol_lookup"]),
    ]
    assert [item.validator_binding_id for item in result.validator_results] == [
        "fixture.identifier_lookup",
        "fixture.identifier_lookup",
        "fixture.symbol_lookup",
        "fixture.symbol_lookup",
    ]
    assert result.validator_agent_run_count == 2
    assert result.batch_validator_run_count == 2


def test_bad_batch_result_identity_becomes_controlled_unresolved_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path, batch_enabled=True)

    def _batch_runner(jobs, *, binding):
        payloads = [_result_payload(job.request) for job in jobs]
        payloads[0]["target"]["object_type"] = "stale_object"
        return {"results": payloads}

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert result.validator_results[0].status == "unresolved"
    assert result.validator_results[0].lookup_attempts[0].method == "invalid_schema"
    assert result.validator_results[1].status == "resolved"
    findings = _parent_validator_findings(result)
    assert [finding.code for finding in findings] == [
        "domain_pack.validator_error",
        "domain_pack.validator_resolved",
    ]


def test_bad_batch_extra_request_id_becomes_controlled_unresolved_results(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path, batch_enabled=True)

    def _batch_runner(jobs, *, binding):
        payloads = [_result_payload(job.request) for job in jobs]
        extra_payload = _result_payload(jobs[0].request)
        extra_payload["request_id"] = "domain-validation:unexpected"
        return {"results": [*payloads, extra_payload]}

    result = dispatch_active_validator_bindings(
        _multi_object_envelope(["BAD:0001", "BAD:0002"]),
        pack,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert [item.status for item in result.validator_results] == [
        "unresolved",
        "unresolved",
    ]
    assert {
        item.lookup_attempts[0].method for item in result.validator_results
    } == {"invalid_schema"}
    assert all(
        "unexpected request IDs" in item.explanation
        for item in result.validator_results
    )


def test_alliance_gene_pack_uses_singleton_gene_validation_with_handoff_context():
    pack = _alliance_gene_pack()
    mentions = ["crumbs", "crb", "ninaE", "Actin"]
    captured_mentions: list[str] = []
    captured_notes: list[list[str]] = []

    def _single_runner(request, *, binding):
        mention = str(request.selected_inputs["mention"])
        captured_mentions.append(mention)
        captured_notes.append(list(request.selected_inputs["identity_resolution_notes"]))
        assert request.selected_inputs["evidence_quotes"][0]["verified_quote"] == (
            f"{mention} was discussed in the paper."
        )

        if mention == "Actin":
            return {
                "status": "unresolved",
                "request_id": request.request_id,
                "validator_binding_id": request.validator_binding_id,
                "validator_agent": request.validator_agent.model_dump(mode="json"),
                "target": request.target.model_dump(mode="json"),
                "resolved_values": {},
                "resolved_objects": [],
                "missing_expected_fields": ["curie", "symbol", "taxon"],
                "candidates": [
                    {
                        "value": "Actin",
                        "label": "Actin",
                        "object_type": "Gene",
                        "matched_fields": {"mention": "Actin"},
                    }
                ],
                "lookup_attempts": [
                    {
                        "provider": "fixture_gene_lookup",
                        "method": "search_genes",
                        "query": {
                            "gene_symbol": mention,
                            "data_provider": "FlyBase",
                        },
                        "result_count": 4,
                        "outcome": "ambiguous",
                    }
                ],
                "curator_message": "Actin remains ambiguous.",
                "explanation": "Lookup returned ambiguous Actin candidates.",
            }

        return {
            "status": "resolved",
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "target": request.target.model_dump(mode="json"),
            "resolved_values": {
                "curie": f"AGR:{len(captured_mentions):07d}",
                "symbol": "crb" if mention in {"crumbs", "crb"} else mention,
                "taxon": "NCBITaxon:7227",
            },
            "resolved_objects": [],
            "missing_expected_fields": [],
            "candidates": [],
            "lookup_attempts": [
                {
                    "provider": "fixture_gene_lookup",
                    "method": "search_genes",
                    "query": {
                        "gene_symbol": mention,
                        "data_provider": "FlyBase",
                    },
                    "result_count": 1,
                    "outcome": "success",
                }
            ],
            "curator_message": f"{mention} resolved through lookup.",
            "explanation": "Lookup resolved this gene mention.",
        }

    def _batch_runner(jobs, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("gene_validation should use the singleton runner")

    result = dispatch_active_validator_bindings(
        _gene_mentions_envelope(mentions),
        pack,
        runner=_single_runner,
        batch_runner=_batch_runner,
        max_parallel_validators=1,
    )

    assert captured_mentions == mentions
    assert captured_notes == [
        [f"Paper-backed context for {mention}."]
        for mention in mentions
    ]
    assert result.validator_agent_run_count == len(mentions)
    assert result.batch_validator_run_count == 0
    assert result.validator_batch_groups == ()
    assert [item.status for item in result.validator_results] == [
        "resolved",
        "resolved",
        "resolved",
        "unresolved",
    ]
    assert result.validator_results[0].lookup_attempts[0].method == "search_genes"
    assert [item.payload.get("gene_symbol") for item in result.envelope.extracted_objects] == [
        "crb",
        "crb",
        "ninaE",
        None,
    ]


def test_alliance_gene_expression_materializes_subject_gene_and_reference_fields():
    pack = _alliance_gene_expression_pack()
    captured_bindings: list[str] = []

    def _runner(request, *, binding):
        captured_bindings.append(binding.binding_id)
        base = {
            "status": "resolved",
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "target": request.target.model_dump(mode="json"),
            "resolved_objects": [],
            "missing_expected_fields": [],
            "candidates": [],
            "curator_message": f"{binding.binding_id} resolved.",
            "explanation": "Fixture validator resolved this field.",
        }
        if binding.binding_id == "subject_gene_validation":
            # The gene lookup reads the subject's paper wording.
            assert request.selected_inputs == {
                "gene_symbol": "Tmem67",
                "data_provider": "MGI",
            }
            return {
                **base,
                "resolved_values": {
                    "primary_external_id": "MGI:1923928",
                    "gene_symbol": "Tmem67",
                },
                "lookup_attempts": [
                    {
                        "provider": "agr_curation_query",
                        "method": "search_genes",
                        "query": {"gene_symbol": "Tmem67", "data_provider": "MGI"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
            }
        if binding.binding_id == "source_reference_validation":
            assert request.selected_inputs == {
                "pmid": "PMID:203506",
                "title": "Paper supplied title",
                "source_document_id": "paper-tmem67",
            }
            return {
                **base,
                "resolved_values": {
                    "reference_id": 203506,
                    "curie": "PMID:203506",
                    "title": "Resolved literature title",
                },
                "lookup_attempts": [
                    {
                        "provider": "agr_literature_reference_lookup",
                        "method": "get_literature_reference",
                        "query": {"value": "PMID:203506"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
            }
        if binding.binding_id == "data_provider_validation":
            return {
                **base,
                "resolved_values": {"abbreviation": "MGI"},
                "lookup_attempts": [
                    {
                        "provider": "fixture_data_provider",
                        "method": "lookup",
                        "query": {"abbreviation": "MGI"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
            }
        if binding.binding_id == "expression_anatomical_structure_validation":
            return {
                **base,
                "resolved_values": {
                    "curie": "EMAPA:17373",
                    "name": "metanephros",
                },
                "lookup_attempts": [
                    {
                        "provider": "agr_curation_query",
                        "method": "search_anatomy_terms",
                        "query": {"term": "metanephros", "data_provider": "MGI"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
            }
        if binding.binding_id == "expression_stage_ontology_validation":
            return {
                **base,
                "resolved_values": {
                    "label": "Theiler stage 26",
                    "curie": "FIXTURE_STAGE:00026",
                    "name": "Theiler stage 26",
                },
                "lookup_attempts": [
                    {
                        "provider": "agr_curation_query",
                        "method": "search_life_stage_terms",
                        "query": {"term": "TS26", "data_provider": "MGI"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
            }
        if binding.binding_id == "relation_vocabulary_validation":
            return {
                **base,
                "resolved_values": {
                    "term_name": "is_expressed_in",
                    "vocabulary": "Expression Relation",
                    "internal_id": 1,
                },
                "lookup_attempts": [
                    {
                        "provider": "fixture_vocabulary",
                        "method": "lookup",
                        "query": {"term_name": "is_expressed_in"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
            }
        raise AssertionError(f"Unexpected binding {binding.binding_id}")

    result = dispatch_active_validator_bindings(
        _gene_expression_envelope(),
        pack,
        runner=_runner,
        max_parallel_validators=1,
    )

    assert result.validator_agent_run_count == 6
    assert set(captured_bindings) == {
        "data_provider_validation",
        "expression_anatomical_structure_validation",
        "expression_stage_ontology_validation",
        "relation_vocabulary_validation",
        "source_reference_validation",
        "subject_gene_validation",
    }
    annotation = result.envelope.extracted_objects[0]
    subject = annotation.payload["expression_annotation_subject"]
    assert {key: subject[key] for key in ("primary_external_id", "gene_symbol", "mention")} == {
        "primary_external_id": "MGI:1923928",
        "gene_symbol": "Tmem67",
        "mention": "Tmem67",
    }
    assert (subject["resolution_state"], subject["lookup_outcome"]) == ("resolved", "matched")
    # A reference stored before the contract is verified by this re-validation (ALL-1283).
    assert annotation.payload["single_reference"] == {
        "pmid": "PMID:203506",
        "title": "Resolved literature title",
        "reference_id": 203506,
        "curie": "PMID:203506",
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": "Fixture validator resolved this field.",
        "validator_curator_message": "source_reference_validation resolved.",
    }
    patch_events = {
        event["validator_binding_id"]: event
        for event in annotation.metadata["validator_resolved_value_materialization"]
    }
    assert patch_events["source_reference_validation"]["original_values"] == {
        "single_reference.title": "Paper supplied title"
    }
    assert patch_events["subject_gene_validation"]["materialized_field_paths"] == [
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
    ]
    field_paths = [
        finding.field_ref.field_path
        for finding in result.appended_findings
        if finding.field_ref is not None
    ]
    assert {
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "single_reference.reference_id",
        "single_reference.curie",
        "single_reference.title",
    } <= set(field_paths)


def test_alliance_gene_expression_unresolved_gene_and_reference_remain_visible():
    pack = _alliance_gene_expression_pack()

    def _runner(request, *, binding):
        base = {
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "target": request.target.model_dump(mode="json"),
            "resolved_objects": [],
        }
        if binding.binding_id == "subject_gene_validation":
            return {
                **base,
                "status": "unresolved",
                "resolved_values": {},
                "missing_expected_fields": ["primary_external_id", "gene_symbol"],
                "candidates": [
                    {
                        "value": "MGI:1923928",
                        "label": "Tmem67",
                        "object_type": "Gene",
                    },
                    {
                        "value": "RGD:1586167",
                        "label": "Tmem67",
                        "object_type": "Gene",
                    },
                ],
                "lookup_attempts": [
                    {
                        "provider": "agr_curation_query",
                        "method": "search_genes",
                        "query": {"gene_symbol": "Tmem67"},
                        "result_count": 2,
                        "outcome": "ambiguous",
                    }
                ],
                "curator_message": "Subject gene lookup is ambiguous.",
                "explanation": "Multiple provider candidates matched.",
            }
        if binding.binding_id == "source_reference_validation":
            return {
                **base,
                "status": "unresolved",
                "resolved_values": {},
                "missing_expected_fields": ["reference_id", "curie"],
                "candidates": [],
                "lookup_attempts": [
                    {
                        "provider": "agr_literature_reference_lookup",
                        "method": "get_literature_reference",
                        "query": {"value": "PMID:203506"},
                        "result_count": 0,
                        "outcome": "not_found",
                    }
                ],
                "curator_message": "No unambiguous reference match found.",
                "explanation": "The API-backed lookup found no source reference.",
            }
        if binding.binding_id == "expression_anatomical_structure_validation":
            return {
                **base,
                "status": "resolved",
                "resolved_values": {
                    "curie": "EMAPA:17373",
                    "name": "metanephros",
                },
                "missing_expected_fields": [],
                "candidates": [],
                "lookup_attempts": [
                    {
                        "provider": "agr_curation_query",
                        "method": "search_anatomy_terms",
                        "query": {"term": "metanephros", "data_provider": "MGI"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
                "curator_message": "Anatomy resolved.",
                "explanation": "Fixture anatomy term resolved.",
            }
        if binding.binding_id == "expression_stage_ontology_validation":
            return {
                **base,
                "status": "resolved",
                "resolved_values": {
                    "label": "Theiler stage 26",
                    "curie": "FIXTURE_STAGE:00026",
                    "name": "Theiler stage 26",
                },
                "missing_expected_fields": [],
                "candidates": [],
                "lookup_attempts": [
                    {
                        "provider": "agr_curation_query",
                        "method": "search_life_stage_terms",
                        "query": {"term": "TS26", "data_provider": "MGI"},
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
                "curator_message": "Stage resolved.",
                "explanation": "Fixture stage term resolved.",
            }
        return {
            **base,
            "status": "resolved",
            "resolved_values": (
                {"abbreviation": "MGI"}
                if binding.binding_id == "data_provider_validation"
                else {
                    "term_name": "is_expressed_in",
                    "vocabulary": "Expression Relation",
                    "internal_id": 1,
                }
            ),
            "missing_expected_fields": [],
            "candidates": [],
            "lookup_attempts": [
                {
                    "provider": "fixture",
                    "method": "lookup",
                    "query": {},
                    "result_count": 1,
                    "outcome": "success",
                }
            ],
            "curator_message": f"{binding.binding_id} resolved.",
            "explanation": "Fixture resolved supporting field.",
        }

    result = dispatch_active_validator_bindings(
        _gene_expression_envelope(),
        pack,
        runner=_runner,
        max_parallel_validators=1,
    )

    annotation = result.envelope.extracted_objects[0]
    # The unresolved gene keeps its paper wording and records why; no identity is written.
    assert annotation.payload["expression_annotation_subject"] == {
        "primary_external_id": None,
        "gene_symbol": None,
        "mention": "Tmem67",
        "resolution_state": "unresolved",
        "lookup_outcome": "ambiguous",
        "validator_explanation": "Multiple provider candidates matched.",
        "validator_curator_message": "Subject gene lookup is ambiguous.",
    }
    # The lookup found no reference (decisive): the stored, unverified title is set aside.
    assert annotation.payload["single_reference"] == {
        "pmid": "PMID:203506",
        "title": None,
        "overruled_title": "Paper supplied title",
        "resolution_state": "unresolved",
        "lookup_outcome": "not_found",
        "validator_explanation": "The API-backed lookup found no source reference.",
        "validator_curator_message": "No unambiguous reference match found.",
    }
    open_findings = [
        finding
        for finding in result.appended_findings
        if finding.status.value == "open"
    ]
    assert {
        finding.field_ref.field_path
        for finding in open_findings
        if finding.field_ref is not None
    } == {
        "expression_annotation_subject.primary_external_id",
        "expression_annotation_subject.gene_symbol",
        "expression_experiment.entity_assayed.primary_external_id",
        "expression_experiment.entity_assayed.gene_symbol",
        "single_reference.reference_id",
        "expression_experiment.single_reference.reference_id",
        "single_reference.curie",
        "single_reference.title",
        # The experiment's copy mirrors the full reference identity (N6).
        "expression_experiment.single_reference.curie",
        "expression_experiment.single_reference.title",
    }
    classifications = {
        finding.field_ref.field_path: finding.details["failure_classification"]
        for finding in open_findings
        if finding.field_ref is not None
    }
    assert classifications["expression_annotation_subject.primary_external_id"] == "ambiguous"
    assert classifications["single_reference.reference_id"] == "not_found"
    gene_finding = next(
        finding
        for finding in open_findings
        if finding.field_ref is not None
        and finding.field_ref.field_path
        == "expression_annotation_subject.primary_external_id"
    )
    assert [item["value"] for item in gene_finding.details["candidate_matches"]] == [
        "MGI:1923928",
        "RGD:1586167",
    ]
    reference_finding = next(
        finding
        for finding in open_findings
        if finding.field_ref is not None
        and finding.field_ref.field_path == "single_reference.reference_id"
    )
    assert reference_finding.details["lookup_attempts"][0]["lookup_status"] == (
        "not_found"
    )


def test_invalid_validator_schema_becomes_controlled_unresolved_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return {"status": "resolved", "request_id": request.request_id}

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert finding.code == "domain_pack.validator_error"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "incompatible output" in result.validator_results[0].explanation


def test_active_package_validator_envelopes_project_across_dispatch_and_finalization():
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from packages.alliance.agents.reference.schema import ReferenceValidationResult

    request = _verbose_validation_request()
    payload = _result_payload(request)
    package_results = [
        (
            GeneResultEnvelope.model_validate(
                {
                    **payload,
                    "gene_candidates": [
                        {
                            "gene_id": "AGR:0001",
                            "symbol": "ABC-1",
                            "data_provider": "FIXTURE",
                        }
                    ],
                }
            ),
            "gene_candidates",
        ),
        (
            ReferenceValidationResult.model_validate(
                {**payload, "reference_id": "AGRKB:101000000924191"}
            ),
            "reference_id",
        ),
    ]

    for concrete_result, package_field in package_results:
        result = validator_result_from_agent_output(
            SimpleNamespace(final_output=concrete_result),
            request=request,
        )
        feedback = _validator_result_finalization_feedback(
            concrete_result,
            request=request,
        )

        assert result.status == "resolved"
        assert result.resolved_values == {
            "identifier": "AGR:0001",
            "symbol": "ABC-1",
        }
        assert not hasattr(result, package_field)
        assert feedback.accepted_result is not None
        assert not hasattr(feedback.accepted_result, package_field)


def test_embedded_validator_result_is_rejected_by_dispatch():
    class EmbeddedValidatorResult(BaseModel):
        result: DomainValidatorResultBase

    request = _verbose_validation_request()
    wrapped_result = EmbeddedValidatorResult(
        result=DomainValidatorResultBase.model_validate(_result_payload(request))
    )

    result = validator_result_from_agent_output(wrapped_result, request=request)

    assert result.status == "unresolved"
    assert result.lookup_attempts[0].method == "invalid_schema"
    assert "incompatible output" in result.explanation


def test_validator_result_identity_mismatch_becomes_invalid_schema_result():
    request = _verbose_validation_request()
    payload = _result_payload(request)
    payload.update(
        {
            "request_id": "domain-validation:stale",
            "validator_binding_id": "stale.binding",
            "validator_agent": {
                "package_id": "stale.package",
                "agent_id": "stale_agent",
            },
            "target": {
                "domain_pack_id": "stale.pack",
                "object_type": "stale_object",
            },
        }
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "unresolved"
    assert result.request_id == request.request_id
    assert result.validator_binding_id == request.validator_binding_id
    assert result.validator_agent == request.validator_agent
    assert result.target == request.target
    assert result.resolved_values == {}
    assert result.lookup_attempts[0].method == "invalid_schema"
    assert "different request" in result.explanation


def test_validator_result_allows_target_input_value_context_drift():
    request = _verbose_validation_request()
    request = request.model_copy(
        update={
            "selected_inputs": {
                "identifier": "BAD:0001",
                "evidence_quote": "Molar abundance was 1.54 \u00b1 0.34 fmole/eye.",
            },
            "target": request.target.model_copy(
                update={
                    "object_id": "object-1",
                    "input_values": {
                        "identifier": "BAD:0001",
                        "evidence_quote": (
                            "Molar abundance was 1.54 \u00b1 0.34 fmole/eye."
                        ),
                    },
                }
            ),
        }
    )
    payload = _result_payload(request)
    payload["target"]["input_values"]["evidence_quote"] = (
        "Molar abundance was 1.54 \u0000b1 0.34 fmole/eye."
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "resolved"
    assert result.target == request.target
    assert result.target.input_values["evidence_quote"].endswith(
        "\u00b1 0.34 fmole/eye."
    )


def test_dispatch_rejects_identity_mismatch_without_materializing(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        payload = _result_payload(request)
        payload["request_id"] = "domain-validation:stale"
        return payload

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "different request" in result.validator_results[0].explanation
    assert not any(
        domain_object.object_type == "Gene"
        for domain_object in result.envelope.extracted_objects
    )


def test_unknown_lookup_outcome_becomes_invalid_schema_result(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            status="unresolved",
            resolved_values={},
            outcome="timeout",
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "incompatible output" in result.validator_results[0].explanation


def test_resolved_validator_without_lookup_evidence_becomes_invalid_schema_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        payload = _result_payload(request)
        payload["lookup_attempts"] = []
        return payload

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "successful lookup_attempt" in result.validator_results[0].explanation
    assert not any(
        domain_object.object_type == "Gene"
        for domain_object in result.envelope.extracted_objects
    )


def test_conflict_lookup_outcome_uses_explicit_blocked_status(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            status="unresolved",
            resolved_values={},
            outcome="conflict",
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert finding.details["failure_classification"] == "conflict"
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "blocked"


def test_blocked_lookup_outcome_uses_explicit_blocked_status(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            status="unresolved",
            resolved_values={},
            outcome="blocked",
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert finding.details["failure_classification"] == "blocked"
    assert finding.details["lookup_attempts"][0]["lookup_status"] == "blocked"


def test_unclassifiable_unresolved_output_becomes_invalid_schema_result(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        payload = _result_payload(
            request,
            status="unresolved",
            resolved_values={},
        )
        payload["lookup_attempts"] = []
        return payload

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert finding.details["failure_classification"] == "invalid_schema"
    assert "Unable to classify unresolved validator result" in (
        result.validator_results[0].explanation
    )


def test_resolved_validator_missing_expected_fields_is_unresolved(
    tmp_path: Path,
):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):
        return _result_payload(
            request,
            resolved_values={"identifier": "AGR:0001"},
        )

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert result.validator_results[0].status == "unresolved"
    assert result.validator_results[0].missing_expected_fields == ["symbol"]
    assert finding.details["failure_classification"] == "missing_expected_result_field"
    assert finding.details["missing_expected_fields"] == ["symbol"]


def test_resolved_array_validator_result_requires_per_item_values():
    request = _array_terms_validation_request()
    payload = _result_payload(
        request,
        resolved_values={
            "terms": [
                {"curie": "GO:0031981", "name": "nuclear lumen"},
                {"curie": "GO:0005654", "name": "nucleoplasm"},
            ]
        },
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "resolved"
    assert result.resolved_values["terms"] == [
        {"curie": "GO:0031981", "name": "nuclear lumen"},
        {"curie": "GO:0005654", "name": "nucleoplasm"},
    ]
    assert result.missing_expected_fields == []


@pytest.mark.parametrize(
    "resolved_terms",
    [
        [{"curie": "GO:0031981", "name": "nuclear lumen"}],
        {"curie": "GO:0031981", "name": "nuclear lumen"},
        [{"curie": "GO:0031981", "name": "nuclear lumen"}, {}],
    ],
)
def test_resolved_array_validator_result_rejects_invalid_item_projection(
    resolved_terms: Any,
):
    request = _array_terms_validation_request()
    payload = _result_payload(
        request,
        resolved_values={"terms": resolved_terms},
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "unresolved"
    assert result.missing_expected_fields == ["terms"]
    assert "one resolved value per selected array item" in result.explanation


def test_resolved_array_validator_result_accepts_allowed_term_curies():
    base_request = _array_terms_validation_request()
    request = base_request.model_copy(
        update={
            "selected_inputs": {
                **base_request.selected_inputs,
                "allowed_term_curies": ["GO:0031981", "GO:0005654"],
            }
        }
    )
    payload = _result_payload(
        request,
        resolved_values={
            "terms": [
                {"curie": "GO:0031981", "name": "nuclear lumen"},
                {"curie": "GO:0005654", "name": "nucleoplasm"},
            ]
        },
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "resolved"
    assert result.missing_expected_fields == []


@pytest.mark.parametrize("violation", ["cardinality", "allowed_term"])
def test_assembled_completeness_does_not_bypass_array_or_policy_checks(violation):
    from src.lib.domain_packs.validator_dispatch import _enforce_expected_result_fields
    from src.schemas.domain_validator import ValidatorFieldResolution

    request = _array_terms_validation_request()
    terms = [{"curie": "GO:0031981", "name": "nuclear lumen"}]
    if violation == "allowed_term":
        terms.append({"curie": "GO:0005654", "name": "nucleoplasm"})
        request = request.model_copy(update={"selected_inputs": {
            **request.selected_inputs, "allowed_term_curies": ["GO:0031981"],
        }})
    result = DomainValidatorResultBase.model_validate(
        _result_payload(request, resolved_values={"terms": terms}))
    result._assembled_field_completeness = True
    result.field_resolutions = {"terms": ValidatorFieldResolution(
        status="resolved", lookup_outcome="matched", resolved_values={"terms": terms},
        explanation="Program-assembled fixture.")}
    checked = _enforce_expected_result_fields(result, request=request)
    assert checked.status == "unresolved"
    assert checked.missing_expected_fields == ["terms"]
    expected = "one resolved value per selected array item" if violation == "cardinality" else "outside the field-specific allowed term list"
    assert expected in checked.explanation


def test_resolved_array_validator_result_rejects_out_of_allowlist_term_curie():
    base_request = _array_terms_validation_request()
    request = base_request.model_copy(
        update={
            "selected_inputs": {
                **base_request.selected_inputs,
                "allowed_term_curies": ["GO:0031981"],
            }
        }
    )
    payload = _result_payload(
        request,
        resolved_values={
            "terms": [
                {"curie": "GO:0031981", "name": "nuclear lumen"},
                {"curie": "GO:0005654", "name": "nucleoplasm"},
            ]
        },
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "unresolved"
    assert result.missing_expected_fields == ["terms"]
    assert "outside the field-specific allowed term list" in result.explanation
    assert "GO:0005654" in result.explanation


def test_resolved_array_validator_result_rejects_schema_allowed_unresolved_label():
    base_request = _array_terms_validation_request()
    request = base_request.model_copy(
        update={
            "selected_inputs": {
                **base_request.selected_inputs,
                "terms": [{"name": "post embryonic, pre-adult"}],
                "allowed_term_curies": ["UBERON:0000068", "UBERON:0000113"],
                "unresolved_allowed_term_labels": ["post embryonic, pre-adult"],
            }
        }
    )
    payload = _result_payload(
        request,
        resolved_values={
            "terms": [{"name": "post embryonic, pre-adult"}],
        },
    )

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "unresolved"
    assert result.missing_expected_fields == ["terms"]
    assert "post embryonic, pre-adult" in result.explanation


@pytest.mark.parametrize("outcome", ["ambiguous", "not_found", "conflict"])
def test_unresolved_array_validator_outcomes_remain_field_addressed(outcome: str):
    request = _array_terms_validation_request()
    payload = _result_payload(
        request,
        status="unresolved",
        resolved_values={},
        missing_expected_fields=["terms"],
        outcome=outcome,
    )
    payload["lookup_attempts"][0] = {
        "provider": "agr_curation_query",
        "method": "search_go_terms",
        "query": {
            "term": "nuclear lumen",
            "go_aspect": "cellular_component",
            "item_index": 0,
        },
        "result_count": 2 if outcome == "ambiguous" else 0,
        "outcome": outcome,
    }

    result = validator_result_from_agent_output(payload, request=request)

    assert result.status == "unresolved"
    assert result.target.field_path == (
        "expression_pattern.where_expressed.cellular_component_qualifiers"
    )
    assert result.missing_expected_fields == ["terms"]
    assert result.lookup_attempts[0].outcome == outcome


def test_runner_error_becomes_controlled_unresolved_result(tmp_path: Path):
    pack = _loaded_pack(tmp_path, max_tool_calls=1)
    captured = {}

    def _runner(request, *, binding):
        captured["max_tool_calls"] = binding.max_tool_calls
        raise RuntimeError("tool budget exhausted")

    result = dispatch_active_validator_bindings(
        _envelope(),
        pack,
        runner=_runner,
    )

    finding = _single_result_finding(result)
    assert captured["max_tool_calls"] == 1
    assert result.validator_results[0].status == "unresolved"
    assert "tool budget exhausted" in result.validator_results[0].explanation
    assert finding.details["failure_classification"] == "transient"


def test_ambiguous_optional_selector_still_blocks_dispatch(tmp_path: Path):
    pack = _loaded_pack(tmp_path)

    def _runner(request, *, binding):  # pragma: no cover - must not be called
        raise AssertionError("optional ambiguity should not dispatch")

    result = dispatch_active_validator_bindings(
        _envelope(
            evidence_records=[
                {"evidence_record_id": "evidence-1", "quote": "A"},
                {"evidence_record_id": "evidence-2", "quote": "B"},
            ]
        ),
        pack,
        runner=_runner,
    )

    assert {finding.code for finding in result.envelope.validation_findings} == {
        "selector_ambiguous"
    }
    assert result.validator_results == ()


def _compact_lookup_tool():
    from agents import function_tool

    @function_tool(strict_mode=False)
    async def agr_curation_query(method: str, gene_id: str) -> dict:
        return {"status": "ok", "data": [{"curie": gene_id, "symbol": "fixture", "identifier": gene_id}]}

    return agr_curation_query


def _compact_test_decision(agent, request):
    import asyncio
    from agents.tool_context import ToolContext
    from uuid import uuid4
    lookup = next(tool for tool in agent.tools if tool.name == "agr_curation_query")
    arguments = json.dumps({
        "method": "get_gene_by_id", "gene_id": "AGR:0001",
    })
    context = ToolContext(context=None, tool_name=lookup.name, tool_call_id=uuid4().hex, tool_arguments=arguments)
    response = json.loads(asyncio.run(lookup.on_invoke_tool(context, arguments)))
    reference = response["validator_record_refs"][0]["record_ref"]
    return {"request_id": request.request_id, "status": "resolved", "explanation": "Verified identity.",
            "candidates": [{"record_ref": reference, "disposition": "selected", "explanation": "Matches the source."}],
            "slots": {"identifier": {"kind": "record", "record_ref": reference, "field": "identifier"}}}


def test_package_scoped_validator_agent_uses_compact_finalization_schema(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _verbose_validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
        model="validator-model",
    )
    captured = {}
    captured_preflight = {}
    sentry_calls = []

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
        ),
    )

    def _get_agent_by_id(agent_key, **kwargs):
        captured["agent_lookup"] = (agent_key, kwargs)
        return source_agent

    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        _get_agent_by_id,
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda model: "anthropic" if model == "validator-model" else "unknown",
    )
    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch.provider_context_preflight",
        lambda **kwargs: captured_preflight.update(kwargs) or {},
    )
    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch.gen_ai_conversation_scope",
        lambda conversation_id: sentry_calls.append(("conversation", conversation_id)) or _FakeContextManager(),
    )

    class FakeSentrySpan:
        def set_data(self, key, value):
            sentry_calls.append(("data", key, value))

    def _fake_sentry_span(**kwargs):
        sentry_calls.append(("span", kwargs))
        return _FakeContextManager(FakeSentrySpan())

    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch.gen_ai_invoke_agent_span",
        _fake_sentry_span,
    )

    def _fake_run_sync(agent, **kwargs):
        captured["agent"] = agent
        captured["kwargs"] = kwargs
        from src.lib.observability.cost_context import current_cost_context
        captured["cost_context"] = current_cost_context()
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_result"
        )
        _unwrap_function_tool(tool)(result=_compact_test_decision(agent, request))
        return {"status": "resolved"}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    binding = cast(Any, SimpleNamespace(raw={}, max_tool_calls=16))
    from src.lib.observability.cost_context import cost_scope, current_cost_context
    with cost_scope({"run_id": "continued-run", "paper": {"namespace": "fixture", "id": "paper-A"}}):
        run_package_scoped_validator_agent(
            request,
            binding=binding,
            runtime_context=ValidatorRuntimeContext(authenticated_groups=("RGD",)),
        )
    assert captured["cost_context"]["run_id"] == "continued-run"
    assert captured["cost_context"]["paper"]["id"] == "paper-A"
    assert current_cost_context() == {}

    runtime_agent = captured["agent"]
    assert runtime_agent is not source_agent
    assert runtime_agent.output_type is None
    assert [tool.name for tool in runtime_agent.tools] == [
        "agr_curation_query", "finalize_validator_result"
    ]
    assert [tool.name for tool in source_agent.tools] == ["agr_curation_query"]
    assert source_agent.instructions == "Base validator instructions."
    assert "finalize_validator_result" in runtime_agent.instructions
    runtime_payload = json.loads(captured["kwargs"]["input"])
    assert runtime_payload["selected_inputs"] == request.selected_inputs
    assert "input_values" not in runtime_payload["target"]
    assert "input_selectors" not in runtime_payload
    assert "evidence" not in runtime_payload
    assert runtime_payload["evidence_summary"]["evidence_record_ids"] == ["evidence-1"]
    assert captured["kwargs"]["max_turns"] == 18
    assert captured["agent_lookup"][1] == {"authenticated_groups": ["RGD"]}
    assert captured_preflight["provider"] == "anthropic"
    assert captured_preflight["model"] == "validator-model"
    assert ("conversation", request.request_id) in sentry_calls
    span_call = next(call for call in sentry_calls if call[0] == "span")
    assert span_call[1]["workflow"] == "domain_validator"
    assert span_call[1]["agent_key"] == request.validator_agent.agent_id
    assert span_call[1]["span_data"]["ai_curation.validator.binding_id"] == (
        request.validator_binding_id
    )
    assert span_call[1]["span_data"]["ai_curation.validator.request_id"] == request.request_id
    assert ("data", "ai_curation.validation.status", "accepted") in sentry_calls


@pytest.mark.parametrize("output_format", ["csv", "tsv", "json"])
def test_compact_candidate_science_survives_materialization_and_formatter_cells(tmp_path, output_format):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.domain_packs.compact_runtime import runtime_for_schema
    from src.lib.flows.output_projection import (
        build_flow_output_artifact_bundle, apply_projection_plan, FlowOutputProjectionPlan,
    )
    captured = {}

    def runner(request, *, binding):
        runtime = runtime_for_schema([request], result_schema=GeneResultEnvelope)
        from agr_ai_curation_alliance.compact_adapter import capture_lookup
        call = capture_lookup(next(iter(runtime.contracts.values())), "agr_curation_query",
            {"method": "search_genes", "symbol": "ABC-1"}, {"status": "ambiguous", "data": [
                {"curie": "RGD:1", "symbol": "ABC-1", "data_provider": None},
                {"curie": "RGD:2", "symbol": "ABC-1", "data_provider": "RGD"},
            ]})
        refs = runtime.workspace.record_lookup(request.request_id, call_id="source-call",
            attempt=call.attempt, records=call.records)
        result = runtime.assemble({"request_id": request.request_id, "status": "unresolved",
            "explanation": "Two candidates remain plausible.", "candidates": [
                {"record_ref": ref, "disposition": "plausible", "explanation": "Paper does not distinguish the candidates.",
                 "evidence_record_ids": ["evidence-1"]} for ref in refs
            ]})
        captured["result"] = result
        from src.lib.domain_packs.validator_dispatch import _ValidatorAgentRunOutput
        return _ValidatorAgentRunOutput(raw_output=None, accepted_result=result)

    dispatched = dispatch_active_validator_bindings(
        _envelope(evidence_records=[{"evidence_record_id": "evidence-1", "quote": "ABC-1 was observed."}]),
        _loaded_pack(tmp_path), runner=runner)
    finding = _single_result_finding(dispatched)
    assert "candidate_matches" in finding.details, finding.model_dump(mode="json")
    assert [row["value"] for row in finding.details["candidate_matches"]] == ["RGD:1", "RGD:2"]
    bundle = build_flow_output_artifact_bundle(completed_steps=[{
        "step": 1, "agent_id": "gene_extractor", "agent_name": "Gene",
        "candidate": SimpleNamespace(agent_key="gene_extractor", adapter_key="gene", candidate_count=1,
            conversation_summary="Ambiguous gene.", payload_json=dispatched.envelope.model_dump(mode="json")),
    }], flow_name="Compact validation", output_format=output_format)
    projection = apply_projection_plan(bundle, FlowOutputProjectionPlan.model_validate({
        "format": output_format, "row_source": "validation_finding", "columns": [
            {"key": "candidates", "field_ref": "validation.candidate_matches"},
            {"key": "lookups", "field_ref": "validation.lookup_attempts"},
            {"key": "target", "field_ref": "validation.target"},
        ],
    }))
    assert projection.rows
    row = projection.rows[0]
    if output_format in {"csv", "tsv"}:
        # File cells hold display text: every candidate, its science and the
        # lookup audit stay visible (ALL-1115), with no JSON in the cell.
        cells = " ".join(str(value) for value in row.values())
        assert "{" not in cells
        for expected in ("value: RGD:1", "value: RGD:2", "Paper does not distinguish the candidates.",
                         "evidence-1", "source_call_id: source-call", "method: search_genes",
                         "symbol: ABC-1", "candidate_count: 2",
                         f"object_id: {captured['result'].target.object_id}",
                         f"field_path: {captured['result'].target.field_path}"):
            assert expected in cells, expected
        return
    candidates = json.loads(row["candidates"]) if isinstance(row["candidates"], str) else row["candidates"]
    assert [candidate["value"] for candidate in candidates] == ["RGD:1", "RGD:2"]
    for candidate in candidates:
        science = candidate["details"]["scientific_assessment"]
        assert science["explanation"] == "Paper does not distinguish the candidates."
        assert science["evidence_record_ids"] == ["evidence-1"]
        assert candidate["details"]["source_call_id"] == "source-call"
    attempts = json.loads(row["lookups"]) if isinstance(row["lookups"], str) else row["lookups"]
    assert attempts[0]["candidate_count"] == 2  # Existing finding/export name for the returned lookup count.
    assert attempts[0]["attempted_query"]["provider_query"] == {"method": "search_genes", "symbol": "ABC-1"}
    target = json.loads(row["target"]) if isinstance(row["target"], str) else row["target"]
    assert target["object_id"] == captured["result"].target.object_id
    assert target["field_path"] == captured["result"].target.field_path


def test_validator_finalization_feedback_accepts_valid_result():
    request = _verbose_validation_request()

    feedback = _validator_result_finalization_feedback(
        _result_payload(request),
        request=request,
    )
    payload = _validator_finalization_tool_payload(feedback)

    assert payload["status"] == "accepted"
    assert feedback.accepted_result is not None
    assert payload["validator_result"]["request_id"] == request.request_id


def test_validator_finalization_feedback_rejects_resolved_without_success_lookup():
    request = _validation_request()

    feedback = _validator_result_finalization_feedback(
        _result_payload(request, outcome="ambiguous"),
        request=request,
    )
    payload = _validator_finalization_tool_payload(feedback)

    assert payload["status"] == "rejected"
    assert feedback.accepted_result is None
    assert "successful lookup_attempt" in payload["message"]
    assert any(
        'outcome "success"' in instruction
        for instruction in payload["repair_instructions"]
    )


@pytest.mark.parametrize("profile_mapped", [False, True])
def test_package_scoped_validator_agent_prefers_accepted_finalization_tool_result(
    monkeypatch: pytest.MonkeyPatch, profile_mapped: bool,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )

    def _fake_run_sync(agent, **kwargs):
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_result"
        )
        result = _result_payload(request)
        feedback = _unwrap_function_tool(tool)(result=result)
        assert feedback["status"] == "rejected"  # Full model-authored copies are no longer accepted.
        assert "Runtime compact-decision contract" in agent.instructions
        feedback = _unwrap_function_tool(tool)(result=_compact_test_decision(agent, request))
        assert feedback["status"] == "accepted"
        assert "validator_result" not in feedback
        return {"status": "resolved"}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    raw_output = run_package_scoped_validator_agent(
        request,
        binding=cast(Any, SimpleNamespace(
            raw={"profile_validation": {"mapping": {}}} if profile_mapped else {},
            max_tool_calls=4,
        )),
    )
    result = validator_result_from_agent_output(raw_output, request=request)

    assert result.status == "resolved"
    assert result.resolved_values["identifier"] == "AGR:0001"
    if profile_mapped:
        assert result.resolved_objects == []


def test_package_scoped_validator_agent_adds_scoped_runtime_tools(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _validation_request().model_copy(
        update={
            "target": ValidationTarget(
                domain_pack_id="fixture.dispatch",
                object_type="GeneAssertion",
                object_id="object-1",
                field_path="gene.identifier",
            ),
            "selected_inputs": {
                "identifier": "BAD:0001",
                "evidence_quotes": [
                    {
                        "evidence_record_id": "evidence-1",
                        "field_path": "gene.identifier",
                        "verified_quote": "BAD:0001 was reported in the paper.",
                    }
                ],
            },
            "evidence": [
                {
                    "evidence_record_id": "evidence-1",
                    "entity": "BAD:0001",
                    "verified_quote": "BAD:0001 was reported in the paper.",
                    "page": 3,
                    "section": "Results",
                    "chunk_id": "chunk-1",
                    "document_id": "doc-123",
                }
            ],
        },
        deep=True,
    )
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )
    captured = {}

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.resolve_tools",
        lambda tool_ids, execution_context: [
            SimpleNamespace(name=tool_id) for tool_id in tool_ids
        ],
    )

    def _fake_run_sync(agent, **kwargs):
        captured["agent"] = agent
        captured["payload"] = json.loads(kwargs["input"])
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_result"
        )
        _unwrap_function_tool(tool)(result=_compact_test_decision(agent, request))
        return {"status": "resolved"}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    run_package_scoped_validator_agent(
        request,
        binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
        runtime_context=ValidatorRuntimeContext(document_id="doc-123", user_id="user-1"),
    )

    tool_names = [tool.name for tool in captured["agent"].tools]
    assert tool_names == [
        "agr_curation_query",
        "search_document",
        "read_chunk",
        "record_evidence",
        "list_recorded_evidence",
        "get_recorded_evidence",
        "finalize_validator_result",
    ]
    instructions = captured["agent"].instructions
    for removed_tool in NEVER_CALLED_VALIDATOR_TOOLS:
        assert removed_tool not in instructions
    assert "Extractor-provided evidence" in instructions
    assert (
        "`selected_inputs.evidence_quote` or `selected_inputs.evidence_quotes`"
        in instructions
    )
    assert "search_document" in instructions
    assert "document_id and user_id" not in instructions
    assert "paper; you do not" not in instructions
    assert captured["payload"]["validator_runtime_capabilities"][
        "scoped_evidence_update_available"
    ] is True


def test_package_scoped_validator_agent_describes_missing_runtime_paper_tools(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )
    captured = {}

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.resolve_tools",
        lambda tool_ids, execution_context: pytest.fail(
            "paper tools should not resolve without document/user runtime context"
        ),
    )

    def _fake_run_sync(agent, **kwargs):
        captured["agent"] = agent
        captured["payload"] = json.loads(kwargs["input"])
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_result"
        )
        _unwrap_function_tool(tool)(result=_compact_test_decision(agent, request))
        return {"status": "resolved"}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    run_package_scoped_validator_agent(
        request,
        binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
    )

    assert [tool.name for tool in captured["agent"].tools] == [
        "agr_curation_query", "finalize_validator_result"
    ]
    instructions = captured["agent"].instructions
    assert "Paper search and evidence update tools are unavailable" in instructions
    assert (
        "`selected_inputs.evidence_quote` or `selected_inputs.evidence_quotes`"
        in instructions
    )
    assert "paper; you do not" not in instructions
    assert "validator_runtime_capabilities" not in captured["payload"]


def test_package_scoped_validator_agent_clears_accepted_result_after_rejection(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )

    def _fake_run_sync(agent, **kwargs):
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_result"
        )
        finalize = _unwrap_function_tool(tool)
        accepted = finalize(result=_compact_test_decision(agent, request))
        assert accepted["status"] == "accepted"
        rejected = finalize(result=_result_payload(request, outcome="ambiguous"))
        assert rejected["status"] == "rejected"
        return _result_payload(request)

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    with pytest.raises(ValueError, match="mandatory finalize_validator_result"):
        run_package_scoped_validator_agent(
            request,
            binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
        )


def test_package_scoped_validator_agent_requires_accepted_finalization_tool(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope

    request = _validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources",
        lambda agent, **kwargs: _result_payload(request),
    )

    with pytest.raises(ValueError, match="mandatory finalize_validator_result"):
        run_package_scoped_validator_agent(
            request,
            binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
        )


def test_package_scoped_validator_batch_agent_uses_compact_finalization_schema(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _verbose_validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
        model="validator-batch-model",
    )
    captured = {}
    captured_preflight = {}

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
            batch_capabilities=["domain_validator_batch"],
        ),
    )

    def _get_agent_by_id(agent_key, **kwargs):
        captured["agent_lookup"] = (agent_key, kwargs)
        return source_agent

    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        _get_agent_by_id,
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda model: "gemini" if model == "validator-batch-model" else "unknown",
    )
    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch.provider_context_preflight",
        lambda **kwargs: captured_preflight.update(kwargs) or {},
    )

    def _fake_run_sync(agent, **kwargs):
        captured["agent"] = agent
        captured["kwargs"] = kwargs
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_batch_results"
        )
        _unwrap_function_tool(tool)(results=[_compact_test_decision(agent, request)])
        return {"results": [_result_payload(request)]}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    binding = cast(Any, SimpleNamespace(raw={}, max_tool_calls=4))
    run_package_scoped_validator_agent_batch(
        cast(Any, [SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={})))]),
        binding=binding,
        runtime_context=ValidatorRuntimeContext(authenticated_groups=("RGD",)),
    )

    runtime_agent = captured["agent"]
    assert runtime_agent is not source_agent
    assert runtime_agent.output_type is None
    assert [tool.name for tool in runtime_agent.tools] == [
        "agr_curation_query", "finalize_validator_batch_results"
    ]
    assert [tool.name for tool in source_agent.tools] == ["agr_curation_query"]
    assert source_agent.instructions == "Base validator instructions."
    assert "finalize_validator_batch_results" in runtime_agent.instructions
    payload = json.loads(captured["kwargs"]["input"])
    assert payload["mode"] == "domain_validator_batch"
    assert "one bulk lookup tool call per compatible shared lookup group" in payload[
        "instructions"
    ]
    assert captured_preflight["provider"] == "gemini"
    assert captured_preflight["model"] == "validator-batch-model"
    assert "gene_symbols" in payload["instructions"]
    assert payload["requests"][0]["request_id"] == request.request_id
    assert payload["requests"][0]["selected_inputs"] == request.selected_inputs
    assert "input_selectors" not in payload["requests"][0]
    assert "evidence" not in payload["requests"][0]
    assert "input_values" not in payload["requests"][0]["target"]
    assert payload["requests"][0]["evidence_summary"] == {
        "evidence_count": 1,
        "evidence_record_ids": ["evidence-1"],
    }
    assert captured["kwargs"]["max_turns"] == 6
    assert captured["agent_lookup"][1] == {"authenticated_groups": ["RGD"]}


# Never called by any validator in 522 production runs (Sep 16-22, 2026);
# validators read the paper through search_document and read_chunk only.
NEVER_CALLED_VALIDATOR_TOOLS = (
    "read_section",
    "read_subsection",
    "attach_evidence_to_object",
    "detach_evidence_from_object",
    "update_recorded_evidence_metadata",
)


def test_package_scoped_validator_batch_agent_offers_only_used_paper_tools(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )
    captured = {}

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
            batch_capabilities=["domain_validator_batch"],
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key, **kwargs: source_agent,
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.resolve_tools",
        lambda tool_ids, execution_context: [
            SimpleNamespace(name=tool_id) for tool_id in tool_ids
        ],
    )

    def _fake_run_sync(agent, **kwargs):
        captured["agent"] = agent
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_batch_results"
        )
        _unwrap_function_tool(tool)(results=[_compact_test_decision(agent, request)])
        return {"results": [_result_payload(request)]}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    run_package_scoped_validator_agent_batch(
        cast(Any, [SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={})))]),
        binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
        runtime_context=ValidatorRuntimeContext(document_id="doc-123", user_id="user-1"),
    )

    runtime_agent = captured["agent"]
    assert [tool.name for tool in runtime_agent.tools] == [
        "agr_curation_query",
        "search_document",
        "read_chunk",
        "finalize_validator_batch_results",
    ]
    for removed_tool in NEVER_CALLED_VALIDATOR_TOOLS:
        assert removed_tool not in runtime_agent.instructions


@pytest.mark.parametrize("batch", [False, True])
def test_package_scoped_validator_lookup_rejects_nonmatching_runtime_groups(
    monkeypatch: pytest.MonkeyPatch,
    batch: bool,
):
    request = _validation_request()

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
            batch_capabilities=["domain_validator_batch"] if batch else [],
        ),
    )

    def _deny_nonmatching_agent(_agent_key, **kwargs):
        assert kwargs == {"authenticated_groups": ["MGI"]}
        raise ValueError("restricted validator agent is unavailable")

    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        _deny_nonmatching_agent,
    )

    with pytest.raises(ValueError, match="restricted validator agent is unavailable"):
        if batch:
            run_package_scoped_validator_agent_batch(
                cast(Any, [SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={})))]),
                binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
                runtime_context=ValidatorRuntimeContext(
                    authenticated_groups=("MGI",),
                ),
            )
        else:
            run_package_scoped_validator_agent(
                request,
                binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
                runtime_context=ValidatorRuntimeContext(
                    authenticated_groups=("MGI",),
                ),
            )


def _batchable_dispatch_job(batch_max_size: int | None) -> Any:
    return SimpleNamespace(
        match=SimpleNamespace(
            binding=SimpleNamespace(batch_max_size=batch_max_size)
        )
    )


def test_plan_validator_run_groups_chunks_batch_by_configured_max_size(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch."
        "_batch_group_key_for_deduped_job_group",
        lambda group: "shared-key",
    )
    grouped_jobs = [[_batchable_dispatch_job(3)] for _ in range(7)]

    run_groups = _plan_validator_run_groups(grouped_jobs)

    # 7 deduped groups, configured cap 3 -> [0,1,2], [3,4,5], [6].
    # The trailing single-index chunk degrades to a standalone (non-batch) run.
    assert [
        (group.dedupe_group_indexes, group.batch_key) for group in run_groups
    ] == [
        ((0, 1, 2), "shared-key"),
        ((3, 4, 5), "shared-key"),
        ((6,), None),
    ]


def test_plan_validator_run_groups_chunks_batch_by_default_cap(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch."
        "_batch_group_key_for_deduped_job_group",
        lambda group: "shared-key",
    )
    total = _DEFAULT_VALIDATOR_BATCH_MAX_SIZE + 5
    grouped_jobs = [[_batchable_dispatch_job(None)] for _ in range(total)]

    run_groups = _plan_validator_run_groups(grouped_jobs)

    assert len(run_groups) == 2
    assert run_groups[0].batch_key == "shared-key"
    assert run_groups[0].dedupe_group_indexes == tuple(
        range(_DEFAULT_VALIDATOR_BATCH_MAX_SIZE)
    )
    assert run_groups[1].batch_key == "shared-key"
    assert run_groups[1].dedupe_group_indexes == tuple(
        range(_DEFAULT_VALIDATOR_BATCH_MAX_SIZE, total)
    )


def test_plan_validator_run_groups_preserves_order_with_mixed_groups(
    monkeypatch: pytest.MonkeyPatch,
):
    # Group keys by first job's marker: None -> standalone, str -> batch member.
    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch."
        "_batch_group_key_for_deduped_job_group",
        lambda group: group[0].marker,
    )

    def job(marker: str | None) -> Any:
        return SimpleNamespace(
            marker=marker,
            match=SimpleNamespace(binding=SimpleNamespace(batch_max_size=None)),
        )

    grouped_jobs = [
        [job(None)],  # 0 standalone
        [job("k")],  # 1 batch k
        [job("k")],  # 2 batch k
        [job(None)],  # 3 standalone
    ]

    run_groups = _plan_validator_run_groups(grouped_jobs)

    assert [
        (group.dedupe_group_indexes, group.batch_key) for group in run_groups
    ] == [
        ((0,), None),
        ((1, 2), "k"),
        ((3,), None),
    ]


def test_package_scoped_validator_agent_sets_max_turns_when_max_tool_calls_unset(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )
    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )
    captured: dict[str, Any] = {}

    def _fake_run_sync(agent, **kwargs):
        captured["kwargs"] = kwargs
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_result"
        )
        _unwrap_function_tool(tool)(result=_compact_test_decision(agent, request))
        return {"status": "resolved"}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    run_package_scoped_validator_agent(
        request,
        binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=None)),
    )

    # max_tool_calls unset must still pin max_turns rather than fall to the
    # Agents SDK default of 10: default tool-call budget (8) + finalization (2).
    assert captured["kwargs"]["max_turns"] == 10


def test_package_scoped_validator_batch_agent_sets_max_turns_when_max_tool_calls_unset(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _verbose_validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
        model="validator-batch-model",
    )
    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
            batch_capabilities=["domain_validator_batch"],
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda model: "gemini",
    )
    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch.provider_context_preflight",
        lambda **kwargs: {},
    )
    captured: dict[str, Any] = {}

    def _fake_run_sync(agent, **kwargs):
        captured["kwargs"] = kwargs
        tool = next(
            tool
            for tool in agent.tools
            if tool.name == "finalize_validator_batch_results"
        )
        _unwrap_function_tool(tool)(results=[_compact_test_decision(agent, request)])
        return {"results": [_result_payload(request)]}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    run_package_scoped_validator_agent_batch(
        cast(Any, [SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={})))]),
        binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=None)),
    )

    # Single-job batch with max_tool_calls unset: default budget (8) + 2 still
    # wins over the len(jobs)-derived floor, so max_turns is pinned at 10 rather
    # than silently inheriting the SDK default.
    assert captured["kwargs"]["max_turns"] == 10


@pytest.mark.parametrize(
    "max_tool_calls, expected_max_turns",
    [
        # Per-JOB-aware budget: 3 jobs * default per-job budget (8) + finalization
        # (2) == 26. A composite validator (experimental_condition) makes several
        # per-component lookups per job, so the budget must scale with the number
        # of jobs in the batch rather than a flat per-run cap.
        (None, 3 * 8 + 2),
        # 3 jobs * configured per-job budget (4) + finalization (2) == 14.
        (4, 3 * 4 + 2),
    ],
)
def test_package_scoped_validator_batch_agent_max_turns_scales_with_job_count(
    monkeypatch: pytest.MonkeyPatch,
    max_tool_calls: int | None,
    expected_max_turns: int,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope

    base_request = _verbose_validation_request()

    def _job(index: int) -> Any:
        request = base_request.model_copy(
            update={"request_id": f"domain-validation:verbose-{index}"}
        )
        return SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={})))

    jobs = [_job(index) for index in range(3)]

    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
        model="validator-batch-model",
    )
    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
            batch_capabilities=["domain_validator_batch"],
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda model: "gemini",
    )
    monkeypatch.setattr(
        "src.lib.domain_packs.validator_dispatch.provider_context_preflight",
        lambda **kwargs: {},
    )
    captured: dict[str, Any] = {}

    # Capture the run kwargs without driving the batch finalize tool: record the
    # max_turns the dispatcher pinned, then raise. The batch runner re-raises, so
    # we only need the captured kwargs to assert the per-job-aware budget.
    def _fake_run_sync(agent, **kwargs):
        captured["kwargs"] = kwargs
        raise RuntimeError("captured max_turns")

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    with pytest.raises(RuntimeError, match="captured max_turns"):
        run_package_scoped_validator_agent_batch(
            cast(Any, jobs),
            binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=max_tool_calls)),
        )

    assert captured["kwargs"]["max_turns"] == expected_max_turns


def test_package_scoped_validator_batch_agent_prefers_accepted_finalization_results(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import (
        _unwrap_function_tool,
    )

    request = _validation_request()
    job = cast(Any, SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={}))))
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
            batch_capabilities=["domain_validator_batch"],
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )

    conflicting_final_result = _result_payload(
        request,
        status="unresolved",
        resolved_values={},
        missing_expected_fields=["identifier"],
        outcome="ambiguous",
    )

    def _fake_run_sync(agent, **kwargs):
        tool = next(
            tool for tool in agent.tools if tool.name == "finalize_validator_batch_results"
        )
        _unwrap_function_tool(tool)(results=[_compact_test_decision(agent, request)])
        return {"results": [conflicting_final_result]}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", _fake_run_sync)

    raw_output = run_package_scoped_validator_agent_batch(
        [job],
        binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
    )
    results = _validated_results_from_agent_batch_output(raw_output, jobs=[job])

    assert len(results) == 1
    assert results[0].status == "resolved"
    assert results[0].resolved_values["identifier"] == "AGR:0001"


def test_package_scoped_validator_batch_agent_requires_accepted_finalization_tool(
    monkeypatch: pytest.MonkeyPatch,
):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope

    request = _validation_request()
    source_agent = SimpleNamespace(
        output_type=GeneResultEnvelope,
        tools=[_compact_lookup_tool()],
        instructions="Base validator instructions.",
    )

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(
            folder_name="gene",
            agent_id=agent_id,
            name="Gene Validation",
            package_id=package_id,
            batch_capabilities=["domain_validator_batch"],
        ),
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key: source_agent,
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources",
        lambda agent, **kwargs: {"results": [_result_payload(request)]},
    )

    with pytest.raises(
        ValueError,
        match="mandatory finalize_validator_batch_results",
    ):
        run_package_scoped_validator_agent_batch(
            cast(Any, [SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={})))]),
            binding=cast(Any, SimpleNamespace(raw={}, max_tool_calls=4)),
        )


def _multivalued_dispatch_pack(tmp_path: Path) -> LoadedDomainPack:
    """Pack with a ``multivalued: true`` field validated by an active binding.

    Mirrors the disease ``evidence_code_curies`` migration shape: a bare field_path with
    ``multivalued: true``, and a binding whose payload selector + expected_result_fields
    reference the bare field (the engine supplies each element index).
    """

    pack_path = tmp_path / "fixture.multivalued_dispatch"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        """
pack_id: fixture.multivalued_dispatch
display_name: Fixture Multivalued Dispatch Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
object_definitions:
  - object_type: Annotation
    display_name: Annotation
    fields:
      - field_path: evidence_code_curies
        field_type: string
        metadata:
          multivalued: true
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.evidence_lookup
        display_name: Evidence lookup
        validator_agent:
          package_id: fixture.validators
          agent_id: evidence_validator
        applies_to:
          domain_pack_id: fixture.multivalued_dispatch
          object_types:
            - Annotation
          field_paths:
            - evidence_code_curies
        required: true
        blocking: false
        input_fields:
          curie:
            source: payload
            path: evidence_code_curies
            required: false
        expected_result_fields:
          curie: evidence_code_curies
""".strip(),
        encoding="utf-8",
    )
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def test_dispatch_validates_and_materializes_every_multivalued_element(tmp_path: Path):
    pack = _multivalued_dispatch_pack(tmp_path)
    envelope = DomainEnvelope(
        envelope_id="multivalued-dispatch-env",
        domain_pack_id="fixture.multivalued_dispatch",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id="annotation-1",
                # Lowercase staged values so the validator canonicalizes each element to a
                # value that DIFFERS from what was staged (exercises per-element write-back
                # past the equality guard).
                payload={"evidence_code_curies": ["eco:0000315", "eco:0000316"]},
            )
        ],
    )

    dispatched_curies: list[str] = []

    def _runner(request, *, binding):
        curie = request.selected_inputs["curie"]
        dispatched_curies.append(curie)
        # Echo a canonicalized (uppercased) value back into the element's write-back slot
        # so the resolved value DIFFERS from the staged value (exercises write-back past
        # the per-element equality guard). No resolved_objects: the element is a scalar.
        payload = _result_payload(request, resolved_values={"curie": curie.upper()})
        payload["resolved_objects"] = []
        return payload

    result = dispatch_active_validator_bindings(envelope, pack, runner=_runner)

    # EVERY element was sent to the validator, not just [0].
    assert sorted(dispatched_curies) == ["eco:0000315", "eco:0000316"]

    # Both elements were written back per-element at field[0] and field[1].
    annotation = result.envelope.extracted_objects[0]
    assert annotation.payload["evidence_code_curies"] == [
        "ECO:0000315",
        "ECO:0000316",
    ]

    # Per-element findings carry the element index (D6).
    resolved_findings = [
        finding
        for finding in result.appended_findings
        if finding.code == "domain_pack.validator_resolved"
    ]
    indexed_paths = sorted(
        finding.field_ref.field_path
        for finding in resolved_findings
        if finding.field_ref is not None
    )
    assert indexed_paths == ["evidence_code_curies[0]", "evidence_code_curies[1]"]


@pytest.mark.parametrize("channel", ["resolved_objects", "extra_slot"])
def test_profile_finalization_rejects_unmapped_output_and_accepts_repair(channel):
    from src.lib.domain_packs.validator_dispatch import (
        _ValidatorFinalizationState, _build_finalize_validator_result_tool,
    )
    request = _verbose_validation_request().model_copy(update={
        "expected_result_fields": {
            "identifier": "attributes.allele_checks[0].confirmed_curie",
            "symbol": "attributes.allele_checks[0].confirmed_symbol",
        },
    })
    original = _result_payload(request)
    original["resolved_objects"] = []
    if channel == "resolved_objects":
        original["resolved_objects"] = [{"symbol": "ABC-1"}]
    else:
        original["resolved_values"]["unmapped"] = "not an approved destination"
    state = _ValidatorFinalizationState()
    tool = _build_finalize_validator_result_tool(
        request, finalization_state=state, profile_mapped=True,
        function_tool_factory=lambda **kwargs: lambda function: function,
    )
    rejected = tool(original)
    assert rejected["status"] == "rejected"
    assert state.accepted_result is None
    assert "expected_result_fields" in rejected["message"]
    repaired = _result_payload(request)
    repaired["resolved_objects"] = []
    accepted = tool(repaired)
    assert accepted["status"] == "accepted"
    assert state.accepted_result.resolved_values == repaired["resolved_values"]
    assert state.accepted_result.lookup_attempts
    # Packaged targets retain their existing database-object output contract.
    assert _validator_result_finalization_feedback(
        _result_payload(request), request=request,
    ).accepted_result is not None


def test_profile_batch_finalization_enforces_each_trusted_binding():
    from src.lib.domain_packs.validator_dispatch import _validator_batch_results_finalization_feedback
    request = _verbose_validation_request().model_copy(update={
        "expected_result_fields": {
            "identifier": "attributes.allele_checks[0].confirmed_curie",
            "symbol": "attributes.allele_checks[0].confirmed_symbol",
        },
    })
    job = SimpleNamespace(request=request, match=SimpleNamespace(
        binding=SimpleNamespace(raw={"profile_validation": {"mapping": {"mapping_id": "alleles"}}}),
    ))
    raw = _result_payload(request)
    feedback = _validator_batch_results_finalization_feedback([raw], jobs=[job])
    assert not feedback.accepted_results
    raw["resolved_objects"] = []
    feedback = _validator_batch_results_finalization_feedback([raw], jobs=[job])
    assert len(feedback.accepted_results) == 1


@pytest.mark.parametrize("batch", [False, True])
def test_profile_finalization_instructions_scope_package_override_to_requests(batch):
    from src.lib.domain_packs.validator_dispatch import _append_validator_finalization_instructions
    agent = SimpleNamespace(instructions="Package requests database facts in resolved_objects.")
    _append_validator_finalization_instructions(agent, batch=batch, profile_request_ids=("profile-request",))
    assert '"profile-request"' in agent.instructions
    assert "takes precedence over package instructions" in agent.instructions
    assert "resolved_objects as an empty list" in agent.instructions
    assert "must remain unresolved" in agent.instructions


# --- KANBAN-1773: write-back selects by actual change, not by a timestamp ----


def _canonical_evidence(**overrides):
    record = {
        "evidence_record_id": "evidence-1",
        "entity": "BAD:0001",
        "verified_quote": "Previous quote.",
        "page": 3,
        "section": "Results",
        "chunk_id": "chunk-1",
        "source_span_ids": ["span-1"],
    }
    record.update(overrides)
    return record


def _items_with_evidence(evidence):
    request = _validation_request().model_copy(update={"evidence": evidence}, deep=True)
    return cast(Any, [SimpleNamespace(
        request=request,
        match=SimpleNamespace(binding=SimpleNamespace(raw={})),
    )])


def test_untouched_evidence_is_not_written_back():
    """A validator that reads but does not change evidence leaves the envelope alone."""
    envelope = _envelope(evidence_records=[_canonical_evidence()])

    updated = _apply_validator_evidence_updates_to_envelope(
        envelope, _items_with_evidence([_canonical_evidence()])
    )

    assert updated is envelope


def test_changed_evidence_is_written_back_without_a_timestamp():
    """The old gate required updated_at or evidence_revision_history to be present.

    Every mutating tool a validator currently holds stamps updated_at, so the
    gate happened to be correct. It coupled correctness to a side effect, and
    discard writes no updated_at at all. Selection is now by actual change.
    """
    envelope = _envelope(evidence_records=[_canonical_evidence()])
    mutated = _canonical_evidence(verified_quote="Corrected quote.")
    assert "updated_at" not in mutated

    updated = _apply_validator_evidence_updates_to_envelope(
        envelope, _items_with_evidence([mutated])
    )

    written = updated.extracted_objects[0].payload["evidence_records"][0]
    assert written["verified_quote"] == "Corrected quote."


def test_written_back_evidence_is_projected_to_canonical_provenance():
    envelope = _envelope(evidence_records=[_canonical_evidence()])
    mutated = {
        **_canonical_evidence(verified_quote="Corrected quote."),
        "status": "verified",
        "span_ids": ["span-1"],
        "updated_at": "2026-09-18T12:31:54+00:00",
    }

    updated = _apply_validator_evidence_updates_to_envelope(
        envelope, _items_with_evidence([mutated])
    )

    written = updated.extracted_objects[0].payload["evidence_records"][0]
    assert written["source_span_ids"] == ["span-1"]
    for field in ("status", "span_ids", "updated_at"):
        assert field not in written


def test_a_discarded_record_is_written_back():
    """Review finding: the new gate misses a discard, like the old one did.

    discard writes status/workspace_status/discard_reason/discarded_at, all of
    which are workspace-only keys. strip_workspace_fields removes them from
    BOTH sides of the comparison, so a discarded record compares equal to its
    active envelope copy and nothing is written back.

    Validators hold no discard tool today, so this is latent. It is exactly the
    latency the previous commit claimed to remove.
    """
    envelope = _envelope(evidence_records=[_canonical_evidence()])
    discarded = {
        **_canonical_evidence(),
        "status": "discarded",
        "workspace_status": "discarded",
        "discard_reason": "Superseded by a better quote.",
        "discarded_at": "2026-09-18T12:32:00+00:00",
    }

    updated = _apply_validator_evidence_updates_to_envelope(
        envelope, _items_with_evidence([discarded])
    )

    assert updated is not envelope, "a discard must reach the envelope"
    records = updated.extracted_objects[0].payload["evidence_records"]
    assert records == [], "discarded evidence must not remain as active support"


def test_a_discard_prunes_the_object_reference():
    """Second-review finding C1: dropping the record orphaned its ID.

    CuratableObjectEnvelope.evidence_record_ids was never pruned, so a discard
    left a dangling reference. gene_expression/conversion.py:1693 raises
    alliance.gene_expression.evidence_records_missing (severity BLOCKER) for
    exactly that state, and inspect_results reports a runtime exception to
    Sentry when it cannot resolve the id to text. A normal curator action would
    have manufactured a non-repairable blocker and a false alert.
    """
    envelope = _envelope(evidence_records=[_canonical_evidence()])
    assert envelope.extracted_objects[0].evidence_record_ids == ["evidence-1"]

    discarded = {
        **_canonical_evidence(),
        "status": "discarded",
        "workspace_status": "discarded",
        "discard_reason": "Superseded by a better quote.",
    }
    updated = _apply_validator_evidence_updates_to_envelope(
        envelope, _items_with_evidence([discarded])
    )

    assert updated.extracted_objects[0].payload["evidence_records"] == []
    assert updated.extracted_objects[0].evidence_record_ids == [], (
        "a dropped record must not leave a dangling reference"
    )


def test_an_update_leaves_the_object_reference_intact():
    envelope = _envelope(evidence_records=[_canonical_evidence()])
    mutated = _canonical_evidence(verified_quote="Corrected quote.")

    updated = _apply_validator_evidence_updates_to_envelope(
        envelope, _items_with_evidence([mutated])
    )

    assert updated.extracted_objects[0].evidence_record_ids == ["evidence-1"]


def test_allowed_term_list_checks_identifier_fields_not_per_element_labels():
    """A slim element's name comes back as a plain label; only its CURIE is checked."""

    from src.lib.domain_packs.validator_result_policies import allowed_term_policy_violations

    base_request = _array_terms_validation_request()
    request = base_request.model_copy(
        update={
            "selected_inputs": {**base_request.selected_inputs, "allowed_term_curies": ["UBERON:0000068"]},
            "expected_result_fields": {
                "curie": "stage_uberon_slim_terms[1].curie",
                "name": "stage_uberon_slim_terms[1].name",
            },
        }
    )

    def violations(values):
        result = DomainValidatorResultBase.model_validate(
            _result_payload(request, resolved_values=values)
        )
        return [violation.field_name for violation in allowed_term_policy_violations(result, request=request)]

    assert violations({"curie": "UBERON:0000068", "name": "embryo stage"}) == []
    assert violations({"curie": "UBERON:0000092", "name": "embryo stage"}) == ["curie"]
    # A result field named as an identifier is checked even when its write leaf is not.
    request = request.model_copy(update={"expected_result_fields": {"id": "stage_term_ref"}})
    assert violations({"id": "UBERON:0000092"}) == ["id"]
