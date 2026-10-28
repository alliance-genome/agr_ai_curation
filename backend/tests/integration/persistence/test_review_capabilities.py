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
