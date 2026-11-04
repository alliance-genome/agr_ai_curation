"""Available review actions agree with durable source authority."""
from uuid import uuid4

import pytest

from src.lib.validation_history import selections, store
from src.lib.validation_review import admission, continuation, reads
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.extractor_phase import TARGETED_EXTRACTION_PROTOCOL, TargetedFollowupTransport
from tests.integration.persistence.test_review_record_source import setup_record
from tests.integration.persistence.test_selected_value_authority import answer
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated


EXTRACTION_TOOLS = {
    "request_targeted_extraction", "select_extraction_contribution",
    "decide_record_retirement", "validate_selected_extraction_source",
}


@pytest.mark.parametrize("historical", [False, True])
def test_outstanding_and_continuation_preserve_supported_work(scope, historical):
    aid, _, _, _, binding, _, _ = setup_record(scope, historical=historical)
    original = store.register_target(scope, aid, identity="object:unrelated", original={
        "object_ref": "unrelated", "origin": {"baseline_target": True}})
    ep = selections.start_episode(scope, aid, episode={
        "episode_id": uuid4().hex, "reason": "mixed source review",
        "obligations": [
            {"target_id": binding, "mode": "validation", "binding_id": "fixture"},
            {"target_id": original, "mode": "extraction_only"},
        ],
    })
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=2, episodes=1)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    answer(scope, aid, binding)
    before = store.read_baseline(scope, aid)
    manifest = selections.read_manifest(scope, aid, episode_id=ep)
    reason = "historical_review_unknown" if historical else "unreviewed"
    assert {"target_id": original, "code": reason} in manifest["blockers"]
    page = reads.outstanding(scope, ep)
    rows = [row for row in page["rows"] if row["decision_unit_id"] == original]
    assert len(rows) == 1
    assert rows[0]["agent_may_decide"] is (not historical)
    if historical:
        assert "extraction_ref" not in rows[0]
    else:
        assert rows[0]["extraction_ref"] == before["sha256"]
    view = continuation.inspect(ReviewContext(scope, ep, "supervisor"), EXTRACTION_TOOLS)
    assert binding in view["eligible"]
    assert (original in view["eligible"]) is (not historical)
    if historical:
        assert {"target_id": original, "code": reason} in view["causes"]
    assert store.read_baseline(scope, aid) == before


@pytest.mark.parametrize("configured", [False, True])
def test_missing_followup_contract_does_not_offer_validation_repair(scope, configured):
    provenance = {"agent_key": "fixture_extractor", "extractor_contract": {
        "followup_protocol": TARGETED_EXTRACTION_PROTOCOL,
        "followup_transport_schema": TargetedFollowupTransport.model_json_schema(),
    }} if configured else {}
    _, ep, _, _, binding, _, _ = setup_record(scope, provenance=provenance)
    view = continuation.inspect(ReviewContext(scope, ep, "supervisor"), EXTRACTION_TOOLS)
    assert (binding in view["eligible"]) is configured
    if not configured:
        assert {"target_id": binding, "code": "required_inputs_unavailable"} in view["causes"]


def test_two_extractors_in_one_run_each_review_under_their_own_rules(scope, monkeypatch):
    from agents import Agent
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.validation_review.extractor_adapter import ExtractorAdapter
    from src.lib.validation_review.reviewer import create_supervisor_phase

    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    monkeypatch.setenv("VALIDATION_REVIEW_MODEL", "")
    supervisor = Agent(name="Supervisor", model="gpt-6-astra", instructions="Policy")
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    supervisor.validation_review_policy_layers = ()
    rules = {"reagents": "Keep only reagents used in this study.",
             "alleles": "Keep every allele with a phenotype."}
    seen = {}
    for agent_key, text in rules.items():
        aid, ep, _, _, _, _, _ = setup_record(scope, provenance={
            "agent_key": agent_key, "extractor_contract": {"instructions": text}})
        context = ReviewContext(scope, ep, "supervisor.validation_review")
        receipt = ExtractorAdapter(context, aid, authenticated_groups=(),
                                   checkpoint_provider=lambda: None).receipt
        seen[agent_key] = create_supervisor_phase(
            context, supervisor=supervisor, checkpoint_provider=lambda: None,
            authenticated_groups=[], extractor_receipt=receipt).instructions
    assert rules["reagents"] in seen["reagents"] and rules["alleles"] not in seen["reagents"]
    assert rules["alleles"] in seen["alleles"] and rules["reagents"] not in seen["alleles"]
