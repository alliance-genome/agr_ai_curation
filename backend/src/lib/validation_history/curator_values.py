"""Resolve explicit curator restore/withdraw intent through authorized selections."""
from copy import deepcopy

from src.schemas.domain_envelope import parse_field_path
from . import projection, selections, store


def _value(payload, path):
    value = payload
    for part in parse_field_path(path) if path != "" else []:
        value = value[part]
    return value


def resolve_curator_identity(scope, *, envelope, object_id, value_path, identity_keys, action, reference, include_state=False, authenticated_groups=()):
    def result(value):
        if not include_state:
            return {key: deepcopy(value.get(key)) for key in identity_keys}
        from src.lib.domain_packs.resolvable_values import RESOLUTION_STATE_KEY, LOOKUP_OUTCOME_KEY, VALIDATOR_EXPLANATION_KEY, VALIDATOR_CURATOR_MESSAGE_KEY
        allowed = [*identity_keys, RESOLUTION_STATE_KEY, LOOKUP_OUTCOME_KEY, VALIDATOR_EXPLANATION_KEY, VALIDATOR_CURATOR_MESSAGE_KEY]
        return {key: deepcopy(value[key]) for key in allowed if key in value} | {
            key: deepcopy(value.get(key)) for key in identity_keys}
    artifact_id = envelope.metadata["validation_history"]["artifact_id"]
    if action == "restore_selected":
        if reference is None or reference.get("artifact_id") != artifact_id:
            raise store.HistoryConflict("Restore requires an exact selection from this artifact")
        chosen, manifest = projection.read_published_projection(scope, reference)
        selections.require_ready(manifest)
    elif action == "withdraw_override":
        if reference is not None:
            raise store.HistoryConflict("Withdrawal cannot choose an arbitrary historical value")
        underlying = projection.read_underlying_curator_value(scope, envelope, object_id=object_id, value_path=value_path, identity_keys=identity_keys, authenticated_groups=authenticated_groups)
        if underlying is not None:
            return result(underlying)
        reference = projection.latest_publication_reference(scope, artifact_id)
        if reference is None:
            return result({})
        # A retired/changed source has no eligible underlying selection. This is
        # explicitly a blank withdrawal, never an implicit restoration of history.
        try:
            chosen, _ = projection.read_published_projection(scope, reference, current=True)
        except store.HistoryConflict:
            return result({})
    else:
        raise ValueError("Unknown explicit curator selection action")
    obj = next((obj for obj in chosen.extracted_objects
                if object_id in {obj.object_id, obj.pending_ref_id}), None)
    if obj is None:
        raise store.HistoryConflict("Historical selection does not contain this object")
    try:
        value = _value(obj.payload, value_path)
    except (KeyError, IndexError, TypeError) as exc:
        raise store.HistoryConflict("Historical selected identity is unavailable") from exc
    if not isinstance(value, dict) or any(key not in value for key in identity_keys):
        raise store.HistoryConflict("Selected identity does not match the declared field contract")
    if action == "withdraw_override" and value.get("curator_override") is not None:
        return result({})
    return result(value)
