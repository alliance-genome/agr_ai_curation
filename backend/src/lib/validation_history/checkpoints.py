"""Immutable checkpoint deltas avoid copying an expanding history at every revision."""

from copy import deepcopy

from src.models.sql.validation_history import ValidationBody
from .store import HistoryConflict, _body, canonical


def _changes(before, after, path=()):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = [
            {"op": "remove", "path": [*path, key]} for key in before if key not in after
        ]
        for key, value in after.items():
            changes.extend(
                _changes(before[key], value, (*path, key))
                if key in before
                else [{"op": "set", "path": [*path, key], "value": value}]
            )
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        for index in range(min(len(before), len(after))):
            changes.extend(_changes(before[index], after[index], (*path, index)))
        if len(after) > len(before):
            changes.append(
                {"op": "append", "path": list(path), "value": after[len(before) :]}
            )
        elif len(after) < len(before):
            changes.append({"op": "truncate", "path": list(path), "length": len(after)})
        return changes
    return [{"op": "set", "path": list(path), "value": after}]


def write_checkpoint_body(
    db, payload: dict, *, previous_payload: dict | None, previous_sha: str | None
) -> str:
    if (previous_sha is None) != (previous_payload is None):
        raise HistoryConflict(
            "Checkpoint predecessor and payload must be supplied together"
        )
    if previous_sha is not None:
        previous = db.get(ValidationBody, previous_sha)
        if previous is None:
            raise HistoryConflict("Checkpoint predecessor is unavailable")
        content = previous.content
        expected = (
            content["payload_sha256"]
            if content.get("checkpoint_encoding") == "delta/v1"
            else canonical(content)[0]
        )
        if canonical(previous_payload)[0] != expected:
            raise HistoryConflict(
                "Current projection differs from its immutable checkpoint"
            )
    return _body(
        db,
        {
            "checkpoint_encoding": "delta/v1",
            "parent_sha256": previous_sha,
            "payload_sha256": canonical(payload)[0],
            "changes": _changes(previous_payload, payload),
        },
    )


def read_checkpoint_body(db, sha: str) -> dict:
    chain = []
    seen = set()
    payload = None
    expected = None
    while sha is not None:
        if sha in seen:
            raise HistoryConflict("Checkpoint ancestry contains a cycle")
        seen.add(sha)
        row = db.get(ValidationBody, sha)
        if row is None:
            raise HistoryConflict("Checkpoint body is unavailable")
        body = row.content
        if body.get("checkpoint_encoding") != "delta/v1":
            # Explicitly imported historical checkpoints are complete known bodies.
            payload = deepcopy(body)
            break
        expected = expected or body["payload_sha256"]
        chain.append(body["changes"])
        sha = body["parent_sha256"]
    for changes in reversed(chain):
        for change in changes:
            path = change["path"]
            if change["op"] == "set" and not path:
                payload = deepcopy(change["value"])
                continue
            target = payload
            address = path[:-1] if change["op"] in {"set", "remove"} else path
            for token in address:
                target = target[token]
            if change["op"] == "set":
                target[path[-1]] = deepcopy(change["value"])
            elif change["op"] == "remove":
                del target[path[-1]]
            elif change["op"] == "append":
                target.extend(deepcopy(change["value"]))
            elif change["op"] == "truncate":
                del target[change["length"] :]
            else:
                raise HistoryConflict("Unknown checkpoint operation")
    if expected is not None and canonical(payload)[0] != expected:
        raise HistoryConflict(
            "Reconstructed checkpoint does not match its immutable hash"
        )
    return payload
