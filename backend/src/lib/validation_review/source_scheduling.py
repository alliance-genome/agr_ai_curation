"""Validation of explicitly selected follow-up source, with inherited accounting."""
from copy import deepcopy

from src.lib.validation_history import store
from . import admission, scheduling, source_inputs
from .decisions import bounded_text


def request(context, *, operation_id, selection_id, target_ids, instructions, receipt):
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(db, context.history_scope, context.episode_id, require_open=False)
        bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
        bounded_text(instructions, ep.policy["instruction_chars"], "Assessment instructions")
        if not target_ids or len(target_ids) != len(set(target_ids)) or len(target_ids) > ep.policy["decision_entries"]:
            raise ValueError("Assessment requires distinct bounded target coverage")
        identity = {"selection_id": selection_id, "target_ids": target_ids, "instructions": instructions,
                    "actor": {"type": context.actor_type, "id": context.actor_id}, "receipt": receipt}
        prior = scheduling._existing(db, ep, operation_id, identity)
        if prior is not None:
            return prior.id
        admission.lock_episode(db, context.history_scope, context.episode_id)
        selection = store._fact(db, ep.artifact_id, selection_id, kind="extraction_source_selection")
        source = store._content(db, selection)
        requests = []
        for target in target_ids:
            fact = source_inputs.latest(db, ep.artifact_id, target)
            if fact is None or fact.parent_id != selection.id:
                raise store.HistoryConflict("Target has no changed input from this approved source selection")
            request = deepcopy(store._content(db, fact)["request"])
            request["source_input_ref"] = source_inputs.reference(fact)
            request["review_episode_id"] = ep.id
            requests.append(request)
        return scheduling._enqueue(db, context.history_scope, ep, operation_id=operation_id,
            requests=requests, receipt=receipt, identity=identity, reconsideration=True, first_from_source=True,
            followup={"instructions": instructions, "parent_decision_id": selection_id,
                      "parent_decision": source["identity"], "previous_answers": [], "evidence": []})
