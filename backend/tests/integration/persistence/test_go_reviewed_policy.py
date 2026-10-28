"""The real GO dependency wave consumes explicitly selected identities."""

import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.validation_history import runtime, store
from src.lib.validation_review import admission, decisions
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.validator_adapter import ValidatorAdapter, save_plan
from src.lib.domain_packs.validator_dispatch import prepare_validator_dispatch, ValidatorRuntimeContext
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated
from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider, entries_for
from tests.contract.alliance.domain_packs.test_go_domain_pack import (
    _contracts, _go_runner, _identity_result, load_alliance_domain_pack_registry,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_resolved", [True, False])
@pytest.mark.parametrize("with_from_unresolved", [True, False, "empty"])
async def test_go_policy_waits_for_review_then_reads_selected_identities(scope, monkeypatch, policy_resolved, with_from_unresolved):
    from agr_ai_curation_alliance.domain_packs.go.values import (
        gene_product_value, go_term_value, reference_value, with_from_value,
    )
    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.go")
    _, fixtures = _contracts()
    source = fixtures.fixtures[0].envelope.model_copy(deep=True, update={"envelope_id": uuid4().hex})
    payload = source.extracted_objects[0].payload
    payload["gene_product"] = gene_product_value("Lta", proposed_curie=None,
        entity_type=payload["gene_product"]["entity_type"], taxon_curie="NCBITaxon:10116")
    payload["go_term"] = go_term_value("extracellular fraction", proposed_curie=None,
                                    aspect=payload["go_term"]["aspect"])
    payload["reference_curie"] = reference_value("Lta secretion in rat", proposed_curie="PMID:12345678")
    payload["with_from"] = ([] if with_from_unresolved == "empty" else
        [with_from_value("UniProtKB:P01374", proposed_curie="UniProtKB:P01374")])
    original = copy.deepcopy(payload)
    source = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        typed=source.model_dump(mode="json"), envelope=source, provenance={})
    context = ValidatorRuntimeContext(user_id=scope.owner_id, document_id=scope.document_id,
                                      authenticated_groups=("RGD",))
    plan = prepare_validator_dispatch(source, pack, runtime_context=context)
    plan_ref = save_plan(plan, context=context)
    aid, episode = plan.history_binding.artifact_id, plan.envelope.metadata["review_episode_id"]
    operation = admission.create_scope(scope, operation_id=uuid4().hex,
        kind="discovered_document_run", targets=0, episodes=0)
    admission.attach_episode(scope, artifact_id=aid, episode_id=episode, scope_id=operation)
    adapter = ValidatorAdapter(scope, aid, episode, plan_ref, authenticated_groups=("RGD",))
    item = SimpleNamespace(scope=scope, aid=aid, ep=episode, plan=plan)
    checkpoint = checkpoint_provider(item)
    requests = []
    base_runner = _go_runner(requests, policy_resolved=policy_resolved)

    def run(request, *, binding, **kwargs):
        if with_from_unresolved is True and binding.binding_id == "go_with_from_gene_validation":
            requests.append(request)
            return {**_identity_result(request, {}), "status": "unresolved",
                "lookup_attempts": [{"provider": "agr_curation_query", "method": "search_genes",
                    "query": dict(request.selected_inputs), "result_count": 0, "outcome": "not_found"}],
                "explanation": "UniProtKB:P01374 is a protein accession, not a gene."}
        return base_runner(request, binding=binding)

    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    await adapter.initial()
    by_binding = {request.validator_binding_id: request for request in requests}
    assert by_binding["go_gene_product_validation"].selected_inputs["gene_symbol"] == "Lta"
    assert by_binding["go_term_validation"].selected_inputs["go_aspect"] == original["go_term"]["aspect"]
    assert by_binding["go_reference_validation"].selected_inputs["curie"] == "PMID:12345678"
    assert by_binding["go_reference_validation"].target.optional_fields == ["pmid", "doi"]
    assert "rgd_go_evidence_policy_validation" not in by_binding
    initial_count = len(requests)
    await adapter.advance(checkpoint)
    assert len(requests) == initial_count
    assert plan.envelope.extracted_objects[0].payload == original

    # This explicit review accepts the unresolved With/From judgment as such;
    # it does not fabricate an identity or assert submission readiness.
    review = ReviewContext(scope, episode, "supervisor.validation_review")
    decisions.record_decisions(review, checkpoint_request=checkpoint(), operation_id="accept-identities",
        entries=entries_for(item, "accept"))
    await adapter.advance(checkpoint)
    policy_requests = [request for request in requests if request.validator_binding_id == "rgd_go_evidence_policy_validation"]
    assert len(policy_requests) == 1
    if with_from_unresolved == "empty":
        assert "with_from" not in policy_requests[0].selected_inputs
        assert "go_with_from_gene_validation" not in by_binding
    policy = policy_requests[0].selected_inputs
    assert (policy["gene_product"]["curie"], policy["gene_product"]["label"]) == ("RGD:3023", "Lta")
    assert policy["resolution_state"] == "resolved"
    assert (policy["go_term"]["curie"], policy["go_term"]["resolution_state"]) == ("GO:0005615", "resolved")
    assert policy["reference_curie"]["curie"] == "AGRKB:101000000400377"
    assert (policy["reference_curie"]["pmid"], policy["reference_curie"]["doi"]) == ("PMID:12345678", None)
    if with_from_unresolved is True:
        assert policy["with_from"][0]["lookup_outcome"] == "not_found"
    assert policy["evidence_code"]["code"] == "IDA"
    assert policy["provider_context"]["provider_key"] == "RGD"
    assert policy["evidence_quotes"][0]["verified_quote"] == "Lta protein was detected in the extracellular fraction."
    decisions.record_decisions(review, checkpoint_request=checkpoint(), operation_id="accept-policy-judgment",
        entries=entries_for(item, "accept", binding_id="rgd_go_evidence_policy_validation"))
    selected = checkpoint().envelope
    findings = [finding for finding in selected.validation_findings
        if (finding.details.get("validation_metadata") or {}).get("validator_binding_id") == "rgd_go_evidence_policy_validation"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding.object_ref.object_id == "go-candidate-rgd-lta-1"
    assert finding.details["validation_metadata"]["dispatch_context"] == {
        "authenticated_groups": ["RGD"], "group_context_identity": '["RGD"]'}
    assert finding.code == ("domain_pack.validator_resolved" if policy_resolved else "domain_pack.validator_unresolved")
    assert finding.status.value == ("resolved" if policy_resolved else "open")
    if not policy_resolved:
        assert finding.severity.value == "blocker"
        assert finding.message == "Insufficient primary evidence for a submit-ready RGD GO annotation; curator review is required."
    assert plan.envelope.extracted_objects[0].payload == original
    assert selected.extracted_objects[0].payload["reference_curie"]["proposed_curie"] == "PMID:12345678"

    first_wave = [finding for finding in selected.validation_findings
        if (finding.details.get("validation_metadata") or {}).get("validator_binding_id") == "go_gene_product_validation"]
    assert first_wave  # Parent and per-field findings all retain the same audit.
    assert all(entry.details["validation_metadata"]["dispatch_context"] == {
        "authenticated_groups": ["RGD"], "group_context_identity": '["RGD"]'} for entry in first_wave)
    if with_from_unresolved is True:
        value = selected.extracted_objects[0].payload["with_from"][0]
        assert (value["resolution_state"], value["lookup_outcome"], value["curie"]) == ("unresolved", "not_found", None)
        assert value["mention"] == value["proposed_curie"] == "UniProtKB:P01374"
        events = selected.extracted_objects[0].metadata["validator_unresolved_value_materialization"]
        event = next(event for event in events if event["validator_binding_id"] == "go_with_from_gene_validation")
        assert event["field_paths"] == ["with_from[0].curie"]
        assert event["lookup_outcome"] == "not_found"

    from src.lib.validation_history import selections
    from src.lib.validation_history.projection import rebuild_selected_envelope
    from src.models.sql.validation_history import ValidationBody
    from sqlalchemy import event
    manifest = selections.read_manifest(scope, aid, episode_id=episode)
    with store.SessionLocal.begin() as db:
        completions = {unit["selected"] for unit in manifest["units"]
            if unit["eligible"] and unit["selected"] not in {"baseline", "curator"}
            and unit.get("applicability_ref") is None}
        assert len(completions) >= 2
        for identity in completions:
            completion = store._fact(db, aid, identity, kind="completion")
            attempt = store._fact(db, aid, completion.parent_id, kind="attempt")
            execution = store._fact(db, aid, attempt.parent_id, kind="execution")
            assert store._content(db, execution)["receipt"]["plan_ref"] == plan_ref
        plan_fact = store._fact(db, aid, plan_ref, kind="review_chat_plan")
        reads = []

        def capture_plan_read(conn, cursor, statement, parameters, context, executemany):
            if plan_fact.body_sha not in parameters.values() or "validation_bodies" not in statement:
                return
            columns = context.compiled.statement.selected_columns
            reads.append(any(column.compare(ValidationBody.__table__.c.content) for column in columns))

        connection = db.connection()
        event.listen(connection, "before_cursor_execute", capture_plan_read)
        try:
            for _ in range(2):
                reads.clear()
                replayed = rebuild_selected_envelope(db, scope, aid, envelope=source,
                    manifest=manifest, domain_pack=pack)
                # Exactly one narrow read for this rebuild; never a full body
                # fetch or reuse from the previous rebuild's transaction work.
                assert reads == [False]
                assert replayed.extracted_objects[0].payload == selected.extracted_objects[0].payload
        finally:
            event.remove(connection, "before_cursor_execute", capture_plan_read)
    assert replayed.extracted_objects[0].payload == selected.extracted_objects[0].payload
    replayed_audits = {finding.details["validation_metadata"]["validator_binding_id"]:
        finding.details["validation_metadata"].get("dispatch_context")
        for finding in replayed.validation_findings if "validation_metadata" in finding.details}
    assert replayed_audits["go_gene_product_validation"] == first_wave[0].details["validation_metadata"]["dispatch_context"]
    assert replayed_audits["rgd_go_evidence_policy_validation"] == finding.details["validation_metadata"]["dispatch_context"]

    if with_from_unresolved is True and policy_resolved:
        # Simulate an unsupported historical recipe at the read boundary;
        # immutable database facts themselves must never be rewritten.
        original_content = store._content
        def historical_content(db, fact):
            content = original_content(db, fact)
            if fact.kind == "candidate":
                return {key: value for key, value in content.items() if key != "dispatch_context"}
            return content
        with monkeypatch.context() as historical:
            historical.setattr(store, "_content", historical_content)
            with store.SessionLocal.begin() as db, pytest.raises(store.HistoryConflict, match="new authenticated validation"):
                rebuild_selected_envelope(db, scope, aid, envelope=source,
                    manifest=manifest, domain_pack=pack)
        assert checkpoint().envelope.extracted_objects[0].payload == selected.extracted_objects[0].payload


@pytest.mark.parametrize("episode_id", ["123", 123, None])
def test_replay_authority_preserves_episode_json_type(episode_id):
    from src.lib.validation_history.replay_binding import _plan_authority
    body = {"registry": {"bindings": []}, "contracts": {},
            "envelope": {"metadata": {"review_episode_id": episode_id}},
            "matches": [{"unused_scientific_context": "retained"}]}
    with store.SessionLocal.begin() as db:
        plan = SimpleNamespace(body_sha=store._body(db, body))
        bindings, contracts, restored_episode = _plan_authority(db, plan)
        assert bindings == [] and contracts == {}
        assert restored_episode == episode_id
        assert type(restored_episode) is type(episode_id)
        assert store._content(db, plan) == body


def test_replay_authority_does_not_materialize_large_scientific_context():
    import hashlib
    import json
    from src.lib.validation_history.replay_binding import _plan_authority
    from src.models.sql.validation_history import ValidationBody

    # Generated, varied context exercises a toasted body without a large file
    # in the repository or a timing threshold sensitive to CI host load.
    body = {"registry": {"bindings": [{"binding_id": "gene", "raw": {"validator": "gene"}}]},
            "contracts": {"gene": "frozen"},
            "envelope": {"metadata": {"review_episode_id": "episode"}},
            "matches": [{"object_id": str(index), "scientific_context": " ".join(
                hashlib.sha256(f"{index}:{part}".encode()).hexdigest()
                for part in range(256))} for index in range(128)]}
    assert len(json.dumps(body)) > 2_000_000
    with store.SessionLocal.begin() as db:
        digest = store._body(db, body)
    with store.SessionLocal.begin() as db:
        authority = _plan_authority(db, SimpleNamespace(body_sha=digest))
        assert authority == (body["registry"]["bindings"], body["contracts"], "episode")
        assert len(json.dumps(authority)) < 256
        assert not any(isinstance(row, ValidationBody) for row in db.identity_map.values())
        assert store._content(db, SimpleNamespace(body_sha=digest)) == body
