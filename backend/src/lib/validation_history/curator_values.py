"""Resolve explicit curator restore/withdraw intent through authorized selections."""
from copy import deepcopy

from src.schemas.domain_envelope import parse_field_path
from . import projection, selections, store


def _value(payload, path):
    value = payload
    for part in parse_field_path(path):
        value = value[part]
    return value


def resolve_curator_identity(scope, *, envelope, object_id, value_path, identity_keys, action, reference):
    artifact_id = envelope.metadata["validation_history"]["artifact_id"]
    if action == "restore_selected":
        if reference is None or reference.get("artifact_id") != artifact_id:
            raise store.HistoryConflict("Restore requires an exact selection from this artifact")
        chosen, manifest = projection.read_published_projection(scope, reference)
        selections.require_ready(manifest)
    elif action == "withdraw_override":
        if reference is not None:
            raise store.HistoryConflict("Withdrawal cannot choose an arbitrary historical value")
        reference = projection.latest_publication_reference(scope, artifact_id)
        if reference is None:
            return {key: None for key in identity_keys}
        # A retired/changed source has no eligible underlying selection. This is
        # explicitly a blank withdrawal, never an implicit restoration of history.
        manifest = selections.read_manifest(scope, artifact_id, episode_id=reference["episode_id"])
        if not manifest["ready"]:
            return {key: None for key in identity_keys}
        try:
            chosen, _ = projection.read_published_projection(scope, reference, current=True)
        except store.HistoryConflict:
            return {key: None for key in identity_keys}
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
        return {key: None for key in identity_keys}
    return {key: deepcopy(value[key]) for key in identity_keys}
