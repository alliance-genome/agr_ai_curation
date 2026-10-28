"""Real packaged selectors distinguish omitted optional context from required data."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.domain_packs.validator_dispatch import prepare_validator_dispatch, ValidatorRuntimeContext
from src.lib.validation_history import runtime, selections, store
from src.lib.validation_review import admission, decisions, source_inputs
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated
from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider


@pytest.mark.parametrize("shape", ["missing", "null"])
def test_real_gene_expression_cellular_component_can_be_assessed_without_fabrication(scope, shape):
    from tests.contract.alliance.domain_packs.test_gene_expression_domain_pack import (
        _load_gene_expression_fixture_pack, GENE_EXPRESSION_FIXTURE_PACK_ID,
        load_alliance_domain_pack_registry,
    )
    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.gene_expression")
    source = _load_gene_expression_fixture_pack(GENE_EXPRESSION_FIXTURE_PACK_ID).fixtures[0].envelope.model_copy(
        deep=True, update={"envelope_id": uuid4().hex})
    location = source.extracted_objects[0].payload["expression_pattern"]["where_expressed"]
    if shape == "missing":
        location.pop("cellular_component", None)
    else:
        location["cellular_component"] = None
    original = deepcopy(source.extracted_objects[0].payload)
    source = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        envelope=source, typed=source.model_dump(mode="json"), provenance={})
    plan = prepare_validator_dispatch(source, pack, runtime_context=ValidatorRuntimeContext(
        user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=("RGD",)))
    aid, ep = plan.history_binding.artifact_id, plan.envelope.metadata["review_episode_id"]
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="discovered_document_run", targets=0, episodes=0)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    match = next(m for m in plan.matches if m.binding.binding_id == "expression_cellular_component_validation")
    target = runtime.binding_target(plan.history_binding, match)
    with store.SessionLocal() as db:
        fact = source_inputs.latest(db, aid, target)
        assert source_inputs.is_absence(store._content(db, fact))
        reference = source_inputs.reference(fact)
        source_inputs.require_absence(db, aid, target, fact.id, scope=scope)
    manifest = selections.read_manifest(scope, aid, episode_id=ep)
    unit = next(u for u in manifest["units"] if u["target_id"] == target)
    checkpoint = checkpoint_provider(SimpleNamespace(scope=scope, aid=aid, ep=ep, plan=plan))
    decisions.record_decisions(decisions.ReviewContext(scope, ep, "supervisor"),
        checkpoint_request=checkpoint(), operation_id="review-omitted-cellular-component", entries=[{
            "target_id": target, "decision_unit_id": target, "expected_version": unit["version"],
            "action": "accept", "applicability_ref": reference, "absence_assessment": "omission_supported",
            "reason": "Reviewer finds no cellular-component assertion in the supplied extraction"}])
    assert checkpoint().envelope.extracted_objects[0].payload == original
    assert next(u for u in selections.read_manifest(scope, aid, episode_id=ep)["units"]
                if u["target_id"] == target)["eligible"]
    assert not any(j.match.binding.binding_id == match.binding.binding_id for j in plan.initial_jobs)


@pytest.mark.parametrize("shape", ["missing_required_mention", "empty_collection"])
def test_real_go_with_from_does_not_invent_optional_absence_for_required_inputs(scope, shape):
    from tests.contract.alliance.domain_packs.test_go_domain_pack import _contracts, load_alliance_domain_pack_registry
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.go")
    _, fixtures = _contracts()
    source = fixtures.fixtures[0].envelope.model_copy(deep=True, update={"envelope_id": uuid4().hex})
    source.extracted_objects[0].payload["with_from"] = ([{"proposed_curie": None}] if shape == "missing_required_mention" else [])
    source = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        envelope=source, typed=source.model_dump(mode="json"), provenance={})
    plan = prepare_validator_dispatch(source, pack, runtime_context=ValidatorRuntimeContext(
        user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=("RGD",)))
    matches = [m for m in plan.matches if m.binding.binding_id == "go_with_from_gene_validation"]
    if shape == "empty_collection":
        assert matches == []  # No fabricated element or optional scalar absence.
    else:
        assert len(matches) == 1
        selected = build_domain_validation_request(matches[0])
        assert selected.request is None and selected.findings and not selected.absent_inputs
        target = runtime.binding_target(plan.history_binding, matches[0])
        with store.SessionLocal() as db:
            assert source_inputs.latest(db, plan.history_binding.artifact_id, target) is None
