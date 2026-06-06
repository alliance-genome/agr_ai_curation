"""Contract tests for the gene builder-pattern migration (Phase 1).

Mirrors ``test_gene_expression_domain_pack.py`` for the gene extractor's envelope -> builder
migration: the per-domain materializer (``materialize_gene_builder_state``), RELATIVE metadata_refs,
the golden pending fixture, and the ``builder_finalization`` tool-binding detection flag.

The pre-existing ``test_alliance_gene_domain_pack.py`` covers the envelope-pattern conversion and
export/submission adapters and is intentionally left untouched (envelope legacy stays until Phase 6).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
    project_validation_summary_projections,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.lib.openai_agents.extraction_builder_workspace import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderWorkspace,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    field_path_exists,
)
from src.schemas.domain_validator import DomainValidatorResultBase

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.gene import (  # noqa: E402
    GENE_DOMAIN_PACK_ID,
    GENE_MATERIALIZER_ID,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
    GENE_OBJECT_ROLE,
    GENE_REFERENCE_VALIDATOR_BINDING_ID,
    materialize_gene_builder_state,
)
from agr_ai_curation_alliance.domain_packs.gene.conversion import (  # noqa: E402
    GeneBuilderExtractionOutput,
    validate_gene_builder_objects,
)

GENE_PACK_DIR = ALLIANCE_PYTHON_SRC.parent.parent / "domain_packs" / "gene"
BUILDER_FIXTURE_PATH = GENE_PACK_DIR / "fixtures" / "daf16_builder_pending.yaml"
BINDINGS_PATH = (
    REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"
)


def _staged_fields() -> dict[str, Any]:
    return {
        "domain_pack_id": GENE_DOMAIN_PACK_ID,
        "object_type": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
        "pending_ref_id": "gene-mention-evidence-1",
        "mention": "daf-16",
        "confidence": "high",
        "identity_resolution_notes": [
            "The paper reports a daf-16 nuclear translocation phenotype in C. elegans."
        ],
        "species": "Caenorhabditis elegans",
        "taxon_hint": "NCBITaxon:6239",
        "data_provider_hint": "WB",
        "proposed_gene_symbol": "daf-16",
        "proposed_taxon": "NCBITaxon:6239",
    }


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-daf16-1",
            "entity": "daf-16",
            "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
            "page": 4,
            "section": "Results",
            "subsection": "Stress response assay",
            "chunk_id": "chunk-daf16-1",
            "figure_reference": "Figure 2A",
        }
    ]


def _materialize_one_candidate() -> Any:
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-builder-test-run",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        agent_id="gene_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="gene-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["gene-mention-evidence-1"],
        evidence_record_ids=["evidence-daf16-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return materialize_gene_builder_state(
        workspace=workspace,
        candidate_ids=["gene-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )


def test_gene_pack_loads_with_builder_fixture():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(GENE_DOMAIN_PACK_ID)
    assert pack is not None

    fixture_ref = registry.get_fixture_pack_ref(
        GENE_DOMAIN_PACK_ID, "daf16_builder_pending"
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/daf16_builder_pending.yaml"
    assert fixture_ref.object_types == [GENE_MENTION_EVIDENCE_OBJECT_TYPE]


def test_gene_builder_materializer_produces_clean_extraction_output():
    result = _materialize_one_candidate()
    assert result.ok, result.summary()
    payload = result.payload
    assert payload is not None

    objects = payload["curatable_objects"]
    assert len(objects) == 1
    obj = objects[0]
    assert obj["object_type"] == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    assert obj["object_role"] == GENE_OBJECT_ROLE
    assert obj["pending_ref_id"] == "gene-mention-evidence-1"
    assert obj["evidence_record_ids"] == ["evidence-daf16-1"]
    assert obj["payload"]["mention"] == "daf-16"
    assert obj["payload"]["evidence_record_id"] == "evidence-daf16-1"
    assert obj["payload"]["verified_quote"] == (
        "DAF-16 translocated to nuclei after heat shock."
    )
    # No resolver/helper machinery: the gene validator owns identity.
    assert "helper_selections" not in payload["metadata"]["provenance"]
    assert payload["metadata"]["provenance"]["source"] == GENE_MATERIALIZER_ID
    assert result.evidence_record_ids == ("evidence-daf16-1",)


def test_gene_builder_metadata_refs_are_relative_and_resolve():
    result = _materialize_one_candidate()
    payload = result.payload
    assert payload is not None
    obj = payload["curatable_objects"][0]

    metadata_paths = {ref["metadata_path"] for ref in obj["metadata_refs"]}
    assert metadata_paths == {"raw_mentions[0]", "evidence_records[0]"}
    # RELATIVE refs resolve against the extraction metadata namespace, never absolute.
    metadata_root = payload["metadata"]
    for ref in obj["metadata_refs"]:
        assert not ref["metadata_path"].startswith("extraction_metadata")
        assert field_path_exists(metadata_root, ref["metadata_path"])


def test_gene_builder_output_validates_against_object_contract():
    result = _materialize_one_candidate()
    assert result.payload is not None
    # Re-validating the materialized payload through the output model must succeed.
    output = GeneBuilderExtractionOutput.model_validate(result.payload)
    assert validate_gene_builder_objects(output) == ()


def test_gene_validator_resolution_projects_materialized_fields():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(GENE_DOMAIN_PACK_ID)
    assert pack is not None
    envelope = DomainEnvelope(
        envelope_id="gene-validation-projection-fixture",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        objects=[
            CuratableObjectEnvelope(
                object_type=GENE_MENTION_EVIDENCE_OBJECT_TYPE,
                pending_ref_id="gene-mention-evidence-1",
                object_role=GENE_OBJECT_ROLE,
                status=CuratableObjectStatus.PENDING,
                payload={
                    "mention": "daf-16",
                    "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
                    "identity_resolution_notes": [
                        "The paper reports daf-16 in C. elegans."
                    ],
                    "species": "Caenorhabditis elegans",
                    "taxon_hint": "NCBITaxon:6239",
                    "data_provider_hint": "WB",
                },
            )
        ],
    )
    validation_registry = DomainPackValidationRegistry.from_domain_pack(pack)
    matches = [
        match
        for match in validation_registry.match_bindings(
            envelope,
            states=[ValidationBindingState.ACTIVE],
        )
        if match.binding.binding_id == GENE_REFERENCE_VALIDATOR_BINDING_ID
    ]
    assert len(matches) == 1
    request = build_domain_validation_request(matches[0]).request
    assert request is not None

    result = materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        [
            ValidatorResultMaterializationInput(
                match=matches[0],
                request=request,
                result=DomainValidatorResultBase(
                    status="resolved",
                    request_id=request.request_id,
                    validator_binding_id=request.validator_binding_id,
                    validator_agent=request.validator_agent,
                    target=request.target,
                    resolved_values={
                        "curie": "WB:WBGene00000912",
                        "symbol": "daf-16",
                        "taxon": "NCBITaxon:6239",
                    },
                    resolved_objects=[
                        {
                            "object_type": "Gene",
                            "resolved_id": "WB:WBGene00000912",
                            "projection_type": "gene_reference",
                            "projection_status": "resolved",
                        }
                    ],
                    missing_expected_fields=[],
                    candidates=[],
                    lookup_attempts=[
                        {
                            "provider": "agr_curation_query",
                            "method": "search_genes",
                            "query": {"symbol": "daf-16", "taxon": "NCBITaxon:6239"},
                            "result_count": 1,
                            "outcome": "success",
                        }
                    ],
                    curator_message="Resolved daf-16.",
                    explanation="Resolved by gene validation fixture.",
                ),
            )
        ],
    )

    assert result.materialized_objects == ()
    assert result.envelope.objects[0].payload["primary_external_id"] == (
        "WB:WBGene00000912"
    )
    assert result.envelope.objects[0].payload["gene_symbol"] == "daf-16"
    assert result.envelope.objects[0].payload["taxon"] == "NCBITaxon:6239"
    summaries = project_validation_summary_projections(
        result.envelope,
        envelope_revision=1,
        object_id="gene-mention-evidence-1",
    )
    assert {
        summary.field_path: summary.status.value
        for summary in summaries
        if summary.field_path is not None
    } == {
        "primary_external_id": "resolved",
        "gene_symbol": "resolved",
        "taxon": "resolved",
    }


def test_gene_builder_rejects_evidence_record_not_in_metadata():
    workspace = ExtractionBuilderWorkspace(
        run_id="gene-builder-bad-evidence",
        domain_pack_id=GENE_DOMAIN_PACK_ID,
        agent_id="gene_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="gene-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["gene-mention-evidence-1"],
        evidence_record_ids=["evidence-MISSING"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_gene_builder_state(
        workspace=workspace,
        candidate_ids=["gene-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "unknown_evidence_record_id" for issue in result.issues
    )


def test_gene_builder_golden_fixture_loads_with_relative_refs():
    fixture_pack = load_domain_fixture_pack(BUILDER_FIXTURE_PATH)
    envelope = fixture_pack.fixtures[0].envelope
    assert envelope.domain_pack_id == GENE_DOMAIN_PACK_ID
    assert envelope.objects[0].object_type == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    assert envelope.objects[0].pending_ref_id == "gene-mention-evidence-1"

    extraction_metadata = envelope.metadata.get("extraction_metadata")
    assert isinstance(extraction_metadata, Mapping)
    for obj in envelope.objects:
        for ref in obj.metadata_refs:
            assert not ref.metadata_path.startswith("extraction_metadata")
            assert field_path_exists(extraction_metadata, ref.metadata_path)


def test_finalize_gene_extraction_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {
        entry["tool_id"]: entry
        for entry in bindings["tools"]
        if isinstance(entry, Mapping) and "tool_id" in entry
    }
    finalize = by_id["finalize_gene_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    assert finalize["callable"] == (
        "agr_ai_curation_alliance.tools.gene_builder_tools:finalize_gene_extraction"
    )
    # The four staging tools are run-state builder tools.
    for tool_id in (
        "stage_gene_mention_evidence",
        "patch_gene_mention_evidence",
        "discard_gene_mention_evidence",
        "list_staged_gene_mention_evidence",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_gene_extractor_agent_has_no_output_schema_and_builder_tools():
    agent_path = (
        REPO_ROOT / "packages" / "alliance" / "agents" / "gene_extractor" / "agent.yaml"
    )
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    assert agent["output_schema"] is None
    tools = set(agent["tools"])
    assert "stage_gene_mention_evidence" in tools
    assert "finalize_gene_extraction" in tools
    # Builder agents must not carry an output schema (forbidden by the platform guard).
    assert "GeneExtractionResultEnvelope" not in str(agent.get("output_schema"))
