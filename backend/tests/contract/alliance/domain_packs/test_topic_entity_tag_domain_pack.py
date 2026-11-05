"""Contract tests for the gene-expression topic-tag agent's proposal record (ALL-1461).

The record uses ABC's own topic-entity-tag fields and values. LinkML has no topic-tag
model, so the contract is ABC's API at a pinned commit plus ATP terms. Nothing here
writes to ABC; a curator signs off and ALL-1463 owns submission.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.openai_agents.extraction_builder_workspace import ExtractionBuilderWorkspace
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.execution_provenance import (
    ExtractionExecutionContext,
    SourceDocumentProvenance,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance import curation_adapters  # noqa: E402
from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.topic_entity_tag import (  # noqa: E402
    TOPIC_ENTITY_TAG_ADAPTER_KEY,
    TOPIC_ENTITY_TAG_DOMAIN_PACK_ID,
    TOPIC_ENTITY_TAG_MOD_ABBREVIATION,
    TOPIC_ENTITY_TAG_OBJECT_TYPE,
    TOPIC_ENTITY_TAG_TOPIC,
    TopicEntityTagReviewRowMaterializer,
    materialize_topic_entity_tag_builder_state,
)
from agr_ai_curation_alliance.tools import topic_entity_tag_builder_tools as tools  # noqa: E402

PACK_DIR = REPO_ROOT / "packages" / "alliance" / "domain_packs" / "topic_entity_tag"
AGENT_PATH = (
    REPO_ROOT / "packages" / "alliance" / "agents" / "gene_expression_topic_tag" / "agent.yaml"
)
BINDINGS_PATH = REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"

# Fields of TopicEntityTagSchemaPost at agr_literature_service c4b6436c.
ABC_POST_FIELDS = {
    "topic", "entity_type", "entity", "entity_id_validation", "species", "display_tag",
    "tag_source_id", "negated", "data_novelty", "data_context", "confidence_level",
    "confidence_score", "note", "validation_by_author",
    "validation_by_professional_biocurator", "ml_model_id", "reference_curie",
    "force_insertion", "index_wft",
}
# Fields of ValidateTopicRequest (POST /topic_entity_tag/validate) at the same commit.
ABC_VALIDATE_FIELDS = {
    "reference_curie", "topic", "mod_abbreviation", "negated", "note", "species",
}
# The platform's protected rationale, and ABC's read-only display name for the topic.
AI_CURATION_ONLY_FIELDS = {"rationale", "topic_name"}
DOCUMENT_CURIE = "AGRKB:101000000000001"


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-1",
            "entity": "expression",
            "verified_quote": "unc-54 expression was observed in body wall muscle.",
            "page": 4,
            "section": "Results",
            "chunk_id": "chunk-1",
        }
    ]


def _workspace(run_id: str = "topic-tag-run") -> ExtractionBuilderWorkspace:
    return ExtractionBuilderWorkspace(
        run_id=run_id,
        domain_pack_id=TOPIC_ENTITY_TAG_DOMAIN_PACK_ID,
        agent_id="gene_expression_topic_tag",
    )


@pytest.fixture
def bound_tools(monkeypatch):
    workspace = _workspace()
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(tools, "get_active_evidence_records_snapshot", _evidence_records)
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_event: None)
    return workspace


def _context(reference_curie: str | None) -> ExtractionExecutionContext:
    return ExtractionExecutionContext(
        captured_at=datetime.now(timezone.utc),
        source_kind="flow",
        flow_id=str(uuid4()),
        step_id="step-1",
        agent_key="gene_expression_topic_tag",
        executed_query="Assess this paper.",
        document=SourceDocumentProvenance(
            document_id=uuid4(),
            provider="abc_literature" if reference_curie else None,
            reference_curie=reference_curie,
        ),
    )


def _finalized_object(bound_workspace, **stage_args) -> dict[str, Any]:
    staged = tools._stage_topic_entity_tag_impl(**stage_args)
    assert staged.status == "ok", staged.message
    result = materialize_topic_entity_tag_builder_state(
        workspace=bound_workspace,
        candidate_ids=[staged.data["candidate_id"]],
        evidence_records=_evidence_records(),
    )
    assert result.ok, result.summary()
    return result.payload["curatable_objects"][0]


def _review_rows(curatable_object: dict[str, Any], reference_curie: str | None):
    pack = load_alliance_domain_pack_registry().get_pack(TOPIC_ENTITY_TAG_DOMAIN_PACK_ID)
    envelope = DomainEnvelope(
        envelope_id="topic-tag-envelope",
        domain_pack_id=TOPIC_ENTITY_TAG_DOMAIN_PACK_ID,
        extracted_objects=[CuratableObjectEnvelope.model_validate(curatable_object)],
        execution_context=_context(reference_curie),
    )
    return TopicEntityTagReviewRowMaterializer(metadata=pack.metadata).materialize(
        envelope, envelope_revision=1
    )


def _field_value(row, field_path: str) -> Any:
    for field in row.metadata["workspace_fields"]:
        if field["field_path"] == field_path:
            return field["value"]
    raise AssertionError(f"{field_path} is not on the review row")


def test_negated_proposal_keeps_its_evidence_on_one_review_row(bound_tools):
    curatable_object = _finalized_object(
        bound_tools,
        negated=True,
        rationale="No expression data in this paper.",
        evidence_record_ids=["evidence-1"],
    )

    rows = _review_rows(curatable_object, DOCUMENT_CURIE)

    assert curatable_object["evidence_record_ids"] == ["evidence-1"]
    assert len(rows) == 1
    assert _field_value(rows[0], "negated") is True
    assert _field_value(rows[0], "rationale") == "No expression data in this paper."
    assert rows[0].display_label == "gene expression in wild type"


def test_positive_proposal_keeps_evidence_and_abc_values(bound_tools):
    curatable_object = _finalized_object(
        bound_tools,
        negated=False,
        rationale="Reports expression in wild-type animals.",
        evidence_record_ids=["evidence-1"],
    )

    assert curatable_object["object_type"] == TOPIC_ENTITY_TAG_OBJECT_TYPE
    assert curatable_object["evidence_record_ids"] == ["evidence-1"]
    assert curatable_object["payload"]["topic"] == TOPIC_ENTITY_TAG_TOPIC == "ATP:0000041"
    assert curatable_object["payload"]["mod_abbreviation"] == TOPIC_ENTITY_TAG_MOD_ABBREVIATION == "WB"
    assert curatable_object["payload"]["negated"] is False


def test_record_holds_only_real_abc_fields_plus_the_platform_rationale(bound_tools):
    curatable_object = _finalized_object(
        bound_tools,
        negated=False,
        rationale="Reports expression.",
        evidence_record_ids=["evidence-1"],
    )

    abc_fields = set(curatable_object["payload"]) - AI_CURATION_ONLY_FIELDS
    assert abc_fields <= ABC_VALIDATE_FIELDS
    assert abc_fields - {"mod_abbreviation"} <= ABC_POST_FIELDS
    for decided_elsewhere in (
        "confidence_level", "confidence_score", "data_novelty", "data_context",
        "tag_source_id", "validation_by_professional_biocurator", "ml_model_id",
    ):
        assert decided_elsewhere not in curatable_object["payload"]


def test_document_reference_is_the_server_value_on_the_review_row(bound_tools):
    curatable_object = _finalized_object(
        bound_tools, negated=True, rationale="None.", evidence_record_ids=["evidence-1"]
    )

    rows = _review_rows(curatable_object, DOCUMENT_CURIE)

    assert _field_value(rows[0], "reference_curie") == DOCUMENT_CURIE
    assert "reference_curie" not in curatable_object["payload"]


def test_upload_without_a_reference_still_gets_a_review_row_with_a_blank_reference(bound_tools):
    curatable_object = _finalized_object(
        bound_tools, negated=True, rationale="None.", evidence_record_ids=["evidence-1"]
    )

    rows = _review_rows(curatable_object, None)

    assert len(rows) == 1
    assert _field_value(rows[0], "reference_curie") in (None, "")


@pytest.mark.parametrize("negated", [True, False])
def test_stage_rejects_a_proposal_without_evidence(bound_tools, negated):
    result = tools._stage_topic_entity_tag_impl(
        negated=negated, rationale="Reason.", evidence_record_ids=[]
    )

    assert result.status == "error"
    assert result.data["validation_issues"][0]["field_path"] == "evidence_record_ids"
    assert bound_tools.candidates == {}


@pytest.mark.parametrize(
    "bad_args, field_path",
    [
        ({"negated": "yes", "rationale": "r", "evidence_record_ids": ["evidence-1"]}, "negated"),
        ({"negated": True, "rationale": "   ", "evidence_record_ids": ["evidence-1"]}, "rationale"),
    ],
)
def test_stage_rejects_invalid_values(bound_tools, bad_args, field_path):
    result = tools._stage_topic_entity_tag_impl(**bad_args)

    assert result.status == "error"
    assert [issue["field_path"] for issue in result.data["validation_issues"]] == [field_path]


def test_stage_schema_cannot_carry_decisions_owned_elsewhere():
    properties = set(tools.stage_topic_entity_tag.params_json_schema["properties"])

    assert properties == {"negated", "rationale", "evidence_record_ids"}


def test_second_stage_is_rejected_because_one_paper_gets_one_proposal(bound_tools):
    first = tools._stage_topic_entity_tag_impl(
        negated=True, rationale="None.", evidence_record_ids=["evidence-1"]
    )
    second = tools._stage_topic_entity_tag_impl(
        negated=True, rationale="Again.", evidence_record_ids=["evidence-1"]
    )

    assert first.status == "ok"
    assert second.status == "error"
    assert second.data["validation_issues"][0]["reason"] == "topic_entity_tag_already_staged"


def test_materializer_rejects_two_candidates():
    workspace = _workspace("topic-tag-two")
    for index in (1, 2):
        workspace.upsert_candidate(
            candidate_id=f"candidate-{index}",
            staged_fields={"negated": True, "rationale": "None."},
            pending_ref_ids=[f"ref-{index}"],
            evidence_record_ids=["evidence-1"],
            status="valid",
        )

    result = materialize_topic_entity_tag_builder_state(
        workspace=workspace,
        candidate_ids=["candidate-1", "candidate-2"],
        evidence_records=_evidence_records(),
    )

    assert not result.ok
    assert [issue["reason"] for issue in result.issues] == ["multiple_topic_entity_tags"]


def test_materializer_rejects_unknown_evidence_record():
    workspace = _workspace("topic-tag-bad-evidence")
    workspace.upsert_candidate(
        candidate_id="candidate-1",
        staged_fields={"negated": False, "rationale": "Reports expression."},
        pending_ref_ids=["ref-1"],
        evidence_record_ids=["evidence-missing"],
        status="valid",
    )

    result = materialize_topic_entity_tag_builder_state(
        workspace=workspace,
        candidate_ids=["candidate-1"],
        evidence_records=_evidence_records(),
    )

    assert not result.ok
    assert [issue["reason"] for issue in result.issues] == ["unknown_evidence_record_id"]


def test_finalize_negated_proposal_with_evidence_succeeds(bound_tools):
    staged = tools._stage_topic_entity_tag_impl(
        negated=True, rationale="None.", evidence_record_ids=["evidence-1"]
    )

    result = tools._finalize_topic_entity_tag_extraction_impl([staged.data["candidate_id"]])

    assert result.status == "ok", result.message
    assert bound_tools.finalization.evidence_record_ids == ("evidence-1",)


def test_empty_finalize_succeeds_with_no_record(bound_tools):
    result = tools._finalize_topic_entity_tag_extraction_impl([])

    assert result.status == "ok", result.message
    assert bound_tools.finalization.payload["curatable_objects"] == []


def test_empty_finalize_cannot_silently_drop_a_staged_proposal(bound_tools):
    tools._stage_topic_entity_tag_impl(
        negated=False, rationale="Reports expression.", evidence_record_ids=["evidence-1"]
    )

    result = tools._finalize_topic_entity_tag_extraction_impl([])

    assert result.status == "error"
    assert result.data["validation_issues"][0]["reason"] == "staged_topic_entity_tag_not_finalized"
    assert bound_tools.finalization is None


def test_discarded_proposal_cannot_be_finalized_or_patched_back(bound_tools):
    staged = tools._stage_topic_entity_tag_impl(
        negated=True, rationale="None.", evidence_record_ids=["evidence-1"]
    )
    candidate_id = staged.data["candidate_id"]
    assert tools._discard_topic_entity_tag_impl(candidate_id=candidate_id).status == "ok"

    finalized = tools._finalize_topic_entity_tag_extraction_impl([candidate_id])
    patched = tools._patch_topic_entity_tag_impl(candidate_id=candidate_id, rationale="Back.")

    assert finalized.status == "error"
    assert finalized.data["validation_issues"][0]["reason"] == "discarded_candidate"
    assert patched.status == "error"
    assert patched.data["validation_issues"][0]["reason"] == "discarded_candidate"
    assert bound_tools.finalization is None


def test_patch_can_flip_negated_and_keeps_the_evidence_rule(bound_tools):
    staged = tools._stage_topic_entity_tag_impl(
        negated=True, rationale="None.", evidence_record_ids=["evidence-1"]
    )
    candidate_id = staged.data["candidate_id"]

    rejected = tools._patch_topic_entity_tag_impl(candidate_id=candidate_id, evidence_record_ids=[])
    accepted = tools._patch_topic_entity_tag_impl(candidate_id=candidate_id, negated=False)

    assert rejected.status == "error"
    assert accepted.status == "ok"
    assert bound_tools.get_candidate(candidate_id).staged_fields["negated"] is False


def test_discard_allows_staging_again(bound_tools):
    staged = tools._stage_topic_entity_tag_impl(
        negated=True, rationale="None.", evidence_record_ids=["evidence-1"]
    )

    discarded = tools._discard_topic_entity_tag_impl(candidate_id=staged.data["candidate_id"])
    restaged = tools._stage_topic_entity_tag_impl(
        negated=True, rationale="Again.", evidence_record_ids=["evidence-1"]
    )

    assert discarded.status == "ok"
    assert restaged.status == "ok"


def test_pack_loads_with_valid_fixture_and_no_validators():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(TOPIC_ENTITY_TAG_DOMAIN_PACK_ID)
    assert pack is not None

    fixture_pack = load_domain_fixture_pack(PACK_DIR / "fixtures" / "wb_gene_expression.yaml")
    objects = [
        obj for fixture in fixture_pack.fixtures for obj in fixture.envelope.extracted_objects
    ]
    assert {obj.payload["negated"] for obj in objects} == {True, False}
    for obj in objects:
        assert set(obj.payload) - AI_CURATION_ONLY_FIELDS <= ABC_VALIDATE_FIELDS
        assert obj.evidence_record_ids
        assert "reference_curie" not in obj.payload
    assert pack.metadata.metadata["validators"]["active"] == []


def test_pack_pins_the_abc_and_atp_contract():
    pack = yaml.safe_load((PACK_DIR / "domain_pack.yaml").read_text(encoding="utf-8"))
    contract = pack["metadata"]["abc_contract"]

    assert contract["literature_service_commit"] == "c4b6436c849aff86c6b7b3914f364b75c606dee5"
    assert contract["atp_commit"] == "74b358e69d4660d0c6bfce80eac6d486774cedaa"
    assert contract["topic"] == {
        "curie": TOPIC_ENTITY_TAG_TOPIC,
        "name": "gene expression in wild type",
    }
    assert contract["mod_abbreviation"] == TOPIC_ENTITY_TAG_MOD_ABBREVIATION
    assert set(contract["post_request_mapping"]) <= ABC_POST_FIELDS
    assert {"reference_curie", "topic", "negated", "tag_source_id"} <= set(
        contract["post_request_mapping"]
    )


def test_no_export_or_abc_submission_path_is_registered():
    assert curation_adapters._STRUCTURED_ADAPTER_DOMAIN_PACKS[TOPIC_ENTITY_TAG_ADAPTER_KEY] == (
        TOPIC_ENTITY_TAG_DOMAIN_PACK_ID
    )
    assert curation_adapters._export_adapter_for(TOPIC_ENTITY_TAG_ADAPTER_KEY) is None
    assert curation_adapters._submission_transport_adapters_for(TOPIC_ENTITY_TAG_ADAPTER_KEY) == ()


def test_agent_declares_builder_tools_and_curation_routing_without_lookups():
    agent = yaml.safe_load(AGENT_PATH.read_text(encoding="utf-8"))

    assert agent["output_schema"] is None
    assert agent["category"] == "Extraction"
    assert agent["curation"] == {
        "adapter_key": TOPIC_ENTITY_TAG_ADAPTER_KEY,
        "domain_pack_id": TOPIC_ENTITY_TAG_DOMAIN_PACK_ID,
        "launchable": True,
    }
    assert agent["frontend"]["show_in_palette"] is True
    assert {"stage_topic_entity_tag", "finalize_topic_entity_tag_extraction"} <= set(agent["tools"])
    assert "agr_literature_reference_lookup" not in agent["tools"]


def test_finalize_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {entry["tool_id"]: entry for entry in bindings["tools"]}

    finalize = by_id["finalize_topic_entity_tag_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    for tool_id in (
        "stage_topic_entity_tag",
        "patch_topic_entity_tag",
        "discard_topic_entity_tag",
        "list_staged_topic_entity_tags",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_curators_can_edit_their_decision_but_not_the_configured_or_agent_fields():
    from src.lib.domain_envelopes.patches import _field_editability

    pack = load_alliance_domain_pack_registry().get_pack(TOPIC_ENTITY_TAG_DOMAIN_PACK_ID)
    definition = next(
        item
        for item in pack.metadata.object_definitions
        if item.object_type == TOPIC_ENTITY_TAG_OBJECT_TYPE
    )
    editable = {
        field.field_path: _field_editability(field)[0] for field in definition.fields
    }

    assert editable == {
        "topic": False,
        "topic_name": False,
        "negated": True,
        "rationale": False,
        "note": True,
        "reference_curie": False,
        "mod_abbreviation": False,
    }


def test_agent_can_read_its_own_contract(monkeypatch):
    from src.lib.agent_contracts import get_agent_contract
    from src.lib.config import agent_loader

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_ROOT / "packages"))
    agent_loader.load_agent_definitions(force_reload=True)

    envelope = get_agent_contract("gene_expression_topic_tag", "domain_envelope")
    field = get_agent_contract("gene_expression_topic_tag", "field", field_path="negated")

    assert envelope["success"] is True, envelope
    assert field["success"] is True, field
    assert field["items"][0]["field"]["field_type"] == "boolean"
