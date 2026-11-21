"""A correction re-validates only the bindings whose own inputs changed (ALL-1584).

Production shape (19 Nov 2026): four records with thirteen validator bindings each.
Every record's original evidence is tagged to a field no validator reads. The review
selects an extractor correction for each record that changes two values and adds
evidence tagged to those two fields. Before ALL-1584 every binding received the
record's whole evidence list, so each correction made twelve bindings stale and the
16 extra attempts ran out on the second record. Now each correction affects exactly
its two bindings and all four records fit in the document budget.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext, prepare_validator_dispatch
from src.lib.validation_history import runtime, selections, store
from src.lib.validation_review import admission, extraction_results, extraction_scheduling, source_inputs, work
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.validator_adapter import REGISTRY, ValidatorAdapter, save_plan
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from tests.cancellation_test_support import install_cancel_reader
from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
from tests.integration.persistence.test_validation_history import migrated as migrated, scope as scope
from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload

PACK_ID = "fixture.field_scoped_evidence"
FIELDS = [f"f{index:02d}" for index in range(1, 14)]
RECORDS = 4
CORRECTED = ("f01", "f02")


def _pack(tmp_path):
    fields = "".join(f"\n      - field_path: {name}\n        field_type: string" for name in [*FIELDS, "statement"])
    bindings = "".join(f"""
      - binding_id: fixture.{name}_lookup
        display_name: {name} lookup
        validator_agent:
          package_id: fixture.validators
          agent_id: {name}_validator
        applies_to:
          domain_pack_id: {PACK_ID}
          object_types: [Annotation]
          field_paths: [{name}]
        required: true
        input_fields:
          value:
            source: payload
            path: {name}
        expected_result_fields:
          curie: {name}""" for name in FIELDS)
    path = tmp_path / PACK_ID
    path.mkdir()
    metadata_path = path / "domain_pack.yaml"
    metadata_path.write_text(f"""
pack_id: {PACK_ID}
display_name: Field Scoped Evidence Fixture
version: 0.1.0
metadata_api_version: 1.0.0
status: active
object_definitions:
  - object_type: Annotation
    display_name: Annotation
    fields:{fields}
metadata:
  validator_bindings:
    active:{bindings}
""".strip(), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(pack_id=metadata.pack_id, display_name=metadata.display_name,
                            version=metadata.version, pack_path=path, metadata_path=metadata_path,
                            metadata=metadata)


def _quote(record_id, document_id, field_path):
    return {"evidence_record_id": record_id, "document_id": document_id,
            "verified_quote": f"Quoted support {record_id}.", "page": 2, "section": "Results",
            "field_path": field_path, "field_paths": [field_path]}


@pytest.fixture
def paper(scope, tmp_path, monkeypatch, request):
    """``request.param`` names the field each original quote is labelled with, by part."""
    from unittest.mock import AsyncMock
    from src.lib.config.agent_loader import AgentDefinition

    labels = getattr(request, "param", {"a": "statement", "b": "statement"})
    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    pack = _pack(tmp_path)
    records, objects = [], []
    for index in range(RECORDS):
        ids = [f"original-{index}-{part}" for part in "ab"]
        records.extend(_quote(f"original-{index}-{part}", scope.document_id, labels[part]) for part in "ab")
        objects.append(CuratableObjectEnvelope(
            object_type="Annotation", pending_ref_id=f"record-{index}", evidence_record_ids=ids,
            payload={"statement": f"Statement {index}", **{name: f"{name}-{index}" for name in FIELDS}}))
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=pack.pack_id,
                         extracted_objects=objects, metadata={"evidence_records": records})
    env = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, typed=env.model_dump(mode="json"),
                                     envelope=env, provenance={})
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package",
                        lambda package, agent: AgentDefinition(folder_name=agent, agent_id=agent, name=agent,
                                                               package_id=package))
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
                        lambda _: pack)
    context = ValidatorRuntimeContext(user_id=scope.owner_id, document_id=scope.document_id,
                                      authenticated_groups=())
    plan = prepare_validator_dispatch(env, pack, runtime_context=context)
    plan_ref = save_plan(plan, context=context)
    aid = plan.history_binding.artifact_id
    ep = plan.envelope.metadata["review_episode_id"]
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="discovered_document_run",
                                 targets=0, episodes=0)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    adapter = ValidatorAdapter(scope, aid, ep, plan_ref, authenticated_groups=())
    item = SimpleNamespace(plan=plan, adapter=adapter, aid=aid, ep=ep, scope=scope, pack=pack, context=context)
    item.targets = {}
    for match in plan.matches:
        key = (match.object_envelope.pending_ref_id, match.field_path)
        item.targets[key] = runtime.binding_target(plan.history_binding, match)
    return item


def _extra_attempts_left(item):
    return admission.status(item.scope, item.ep)["budgets"][0]["remaining"]["extra_attempts"]


def _latest_inputs(item):
    with store.SessionLocal() as db:
        return {key: source_inputs.reference(source_inputs.latest(db, item.aid, target))
                for key, target in item.targets.items()}


def _correction(item, checkpoint, index):
    """A supervisor defer, then an extractor proposal changing two values with tagged evidence."""
    pending = f"record-{index}"
    current = next(obj for obj in checkpoint().envelope.extracted_objects if obj.pending_ref_id == pending)
    entry = current.metadata["validation_history"]["target_id"]
    op = uuid4().hex
    with store.SessionLocal.begin() as db:
        parent = store._append(db, item.aid, operation_id="decision:" + op, kind="decision", body={
            "actor": {"type": "agent", "id": "supervisor"},
            "entries": [{"target_id": item.targets[(pending, "f01")], "action": "defer",
                         "reason": "Check the Methods"}]})
        store._append(db, item.aid, operation_id="episode-disposition:" + op, kind="episode_disposition",
                      parent_id=item.ep, body={"selection_ids": []})
    context = ReviewContext(item.scope, item.ep, "supervisor")
    wid = extraction_scheduling.request(
        context, operation_id=uuid4().hex, parent_decision_id=parent.id, entry_ids=[entry],
        instructions="Check the Methods.",
        receipt={"agent_key": "saved", "execution_receipt": None, "contract": {"instructions": "Re-read"}},
        current_entries={entry: current.model_dump(mode="json")})
    claim = work.claim(item.scope, wid)
    work.mark_submitted(item.scope, wid, claim["lease_token"])
    evidence = [_quote(f"new-{index}-{name}", item.scope.document_id, name) for name in CORRECTED]
    result = {"entries": [{"entry_id": entry, "outcome": "proposed_correction",
        "assessment": "The Methods give both values", "remaining_uncertainty": "", "evidence_record_ids": [],
        "proposed_values": [{"field_path": name, "value": f"corrected-{name}-{index}",
                             "evidence_record_ids": [f"new-{index}-{name}"]} for name in CORRECTED]}]}
    retained = extraction_results.retain(item.scope, wid, claim["lease_token"], result, evidence_records=evidence)
    work.finish(item.scope, wid, claim["lease_token"], outcome=retained)
    return context, retained["contributions"][0], [record["evidence_record_id"] for record in evidence]


@pytest.mark.asyncio
async def test_four_corrected_records_revalidate_only_changed_bindings_within_budget(paper, monkeypatch):
    item = paper
    calls = []

    def run(request, binding, **kwargs):
        calls.append((request.validator_binding_id, [record["evidence_record_id"] for record in request.evidence]))
        return {**_result_payload(request, resolved_values={"curie": request.selected_inputs["value"]}),
                "resolved_objects": []}

    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
                        lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    await item.adapter.initial()
    assert len(calls) == RECORDS * len(FIELDS)
    # Unclaimed (statement-tagged) evidence still reaches every binding on its record.
    assert all(len(ids) == 2 and all(identity.startswith("original-") for identity in ids) for _, ids in calls)
    budget = _extra_attempts_left(item)
    assert budget == 16
    # The saved plan is in main's v0.11.11 format: nothing new is saved. Each
    # match's checked field paths are derived from the plan's own registry.
    body = store.read_fact(item.scope, item.aid, item.adapter.plan_ref)["content"]
    assert set(body["match_parts"]) == {"envelopes", "bindings", "object_definitions",
                                        "field_definitions", "policies"}
    assert all(set(saved) == {"envelope", "object_index", "binding", "object_definition", "field_definition",
                              "policy", "element_index", "resolved_field_path", "element_index_path"}
               for saved in body["matches"].values())
    assert set(body["registry"]) == {"domain_pack", "validator_metadata", "bindings", "field_policies"}
    # The rest of the review resumes from the saved plan in a fresh adapter.
    item.adapter = ValidatorAdapter(item.scope, item.aid, item.ep, item.adapter.plan_ref, authenticated_groups=())
    checkpoint = checkpoint_provider(item)

    for index in range(RECORDS):
        pending = f"record-{index}"
        context, contribution, evidence_ids = _correction(item, checkpoint, index)
        before = _latest_inputs(item)
        with store.SessionLocal() as db:
            digest = selections.source_state_digest(db, item.aid)
        result = await item.adapter.select_source(
            context, checkpoint, operation_id=f"select-{index}", contribution_ref=contribution,
            field_paths=list(CORRECTED), evidence_ids=evidence_ids, expected_source_sha=digest,
            reason="The Methods give both values")
        affected = {change["target_id"] for change in result["affected_bindings"]}
        assert affected == {item.targets[(pending, name)] for name in CORRECTED}
        after = _latest_inputs(item)
        assert {key for key in item.targets if before[key] != after[key]} == {(pending, name) for name in CORRECTED}
        outcome = await item.adapter.assess_source(
            context, operation_id=f"assess-{index}", selection_id=result["selection_id"],
            target_ids=sorted(affected), instructions="Assess the corrected values")
        assert outcome["state"] == "completed"
        budget -= len(CORRECTED)
        assert _extra_attempts_left(item) == budget
        # Each re-validated binding received the original record evidence plus only its own new quote.
        revalidated = dict(calls[-len(CORRECTED):])
        for name in CORRECTED:
            assert revalidated[f"fixture.{name}_lookup"] == [f"original-{index}-a", f"original-{index}-b",
                                                              f"new-{index}-{name}"]

    assert _extra_attempts_left(item) == 16 - RECORDS * len(CORRECTED)
    # The review did not stop: every correction fit inside the paper's re-check limit.
    assert admission.status(item.scope, item.ep)["state"] in {"complete", "pending"}
    final = checkpoint().envelope
    # Revalidate reads the saved registry and plans a new round on it; it assigns
    # every record's evidence to the same checks the first dispatch did.
    replanned = prepare_validator_dispatch(final, item.pack, runtime_context=item.context,
                                           registry=REGISTRY.validate_python(body["registry"]))
    owners = {(m.object_envelope.pending_ref_id, m.field_path): m.record_binding_paths for m in item.plan.matches}
    assert {(m.object_envelope.pending_ref_id, m.field_path): m.record_binding_paths
            for m in replanned.matches} == owners
    for index, obj in enumerate(final.extracted_objects):
        # The record keeps all of its evidence; only validator inputs are field-scoped.
        assert obj.evidence_record_ids == [f"original-{index}-a", f"original-{index}-b",
                                           *(f"new-{index}-{name}" for name in CORRECTED)]
        assert [obj.payload[name] for name in CORRECTED] == [f"corrected-{name}-{index}" for name in CORRECTED]


@pytest.mark.asyncio
@pytest.mark.parametrize("paper", [{"a": "f01", "b": "f02"}], indirect=True)
async def test_restoring_a_set_aside_record_in_a_closed_round_of_an_older_review(paper, monkeypatch):
    """A review saved before ALL-1584; a curator restores a set-aside record after the round closed.

    Before the release every check received the record's whole quote list. In
    that review record-1 was corrected and re-checked, and record-0 was set
    aside. The restore is the first save after the release.
    """
    from copy import deepcopy

    from src.lib.domain_packs import input_selectors
    from src.models.sql.validation_review import ValidationReviewEpisode
    from tests.integration.persistence.test_record_retirement import proposal

    item = paper
    routed = input_selectors.binding_evidence
    release = {"applied": False}
    monkeypatch.setattr(input_selectors, "binding_evidence",
                        lambda records, **kwargs: routed(records, **kwargs) if release["applied"] else tuple(records))

    def run(request, binding, **kwargs):
        return {**_result_payload(request, resolved_values={"curie": request.selected_inputs["value"]}),
                "resolved_objects": []}

    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
                        lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    # Before the release: first dispatch, a correction of record-1, record-0 set aside.
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    context, contribution, evidence_ids = _correction(item, checkpoint, 1)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    selected = await item.adapter.select_source(
        context, checkpoint, operation_id="old-correction", contribution_ref=contribution,
        field_paths=list(CORRECTED), evidence_ids=evidence_ids, expected_source_sha=digest, reason="Methods")
    old_affected = sorted(change["target_id"] for change in selected["affected_bindings"])
    assert len(old_affected) == len(FIELDS)  # the old fan-out: every check on the record
    assessed = await item.adapter.assess_source(context, operation_id="old-assess",
        selection_id=selected["selection_id"], target_ids=old_affected, instructions="Assess")
    assert assessed["state"] == "completed"
    context, ref, target = proposal(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    await item.adapter.select_source(context, checkpoint, operation_id="set-aside", contribution_ref=ref,
                                     retirement_decision="approve", expected_source_sha=digest,
                                     reason="Outside the task")
    with store.SessionLocal.begin() as db:
        episode = db.get(ValidationReviewEpisode, item.ep)
        episode.state = "complete"
        charged, allowances = deepcopy(episode.charged), deepcopy(episode.allowances)
        version = store._current(db, item.aid, target)["version"]
    before = _latest_inputs(item)

    # After the release: the curator restores record-0 in the closed round.
    release["applied"] = True
    resumed = ValidatorAdapter(item.scope, item.aid, item.ep, item.adapter.plan_ref, authenticated_groups=())
    curator = ReviewContext(item.scope, item.ep, item.scope.owner_id, actor_type="curator")
    restored = await resumed.select_source(curator, checkpoint, operation_id="restore", restore_target_id=target,
                                           expected_version=version, reason="Review again")
    after = _latest_inputs(item)
    changed = {key for key in item.targets if before[key] != after[key]}
    # Checks that were never re-checked keep no saved follow-up input and stay current.
    assert not {key for key in changed if key[0] in {"record-2", "record-3"}}
    # Record-1's checks were re-checked under the old routing; their saved inputs
    # hold every quote, so the narrower rebuild makes each of them stale.
    assert {key for key in changed if key[0] == "record-1"} == {("record-1", name) for name in FIELDS}
    # Restoring runs no check and charges nothing.
    with store.SessionLocal() as db:
        episode = db.get(ValidationReviewEpisode, item.ep)
        assert (episode.charged, episode.allowances, episode.state) == (charged, allowances, "complete")
    assert restored["publication"]["ready"] is False
    with store.SessionLocal() as db:
        manifest = selections.manifest_in_transaction(db, item.scope, item.aid, episode_id=item.ep)
    blocked = {blocker["target_id"] for blocker in manifest["blockers"]}
    record_1 = {item.targets[("record-1", name)] for name in FIELDS}
    assert record_1 <= blocked
    # Record-1's checks already used their re-check, and the round is closed:
    # they stay stale in this round. The paper shows as awaiting review until
    # the curator presses Revalidate, which starts a new round.
    with pytest.raises(admission.ReviewStopped) as stopped:
        await resumed.assess_source(curator, operation_id="recheck-closed",
                                    selection_id=restored["restoration_id"],
                                    target_ids=sorted(record_1), instructions="Re-check")
    assert stopped.value.reason == "scientific_attempts_exhausted"
    status = admission.status(item.scope, item.ep)
    assert (status["state"], status["ready"]) == ("pending", False)
