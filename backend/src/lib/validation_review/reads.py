"""Exact, byte-bounded review snapshots. Continuations never truncate stored data."""

import json
from uuid import uuid4

from sqlalchemy import select

from src.lib.validation_history import store
from .audit import append_fact
from src.lib.validation_history.selections import manifest_in_transaction
from src.models.sql.validation_history import ValidationFact, ValidationBody
from .admission import lock_episode, _allowances, provider_usage
from .policy import TOKEN_USAGE_BUCKETS


def status_reference(scope, episode_id):
    """Return a compact status with an exact reference to arbitrarily large coverage."""
    from .admission import status

    value = status(scope, episode_id)
    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        fact = append_fact(
            db,
            ep.artifact_id,
            operation_id="review-status:" + str(uuid4()),
            kind="review_status",
            parent_id=ep.id,
            body=value,
        )
        return {
            "state": value["state"],
            "ready": value["ready"],
            "fact_id": fact.id,
            "sha256": fact.body_sha,
        }


def snapshot(scope, episode_id, *, operation_id):
    """Pin mutable review state once; all subsequent reads name its immutable SHA."""
    with store.SessionLocal.begin() as db:
        ep, chain = lock_episode(db, scope, episode_id, require_open=False)
        from .decisions import bounded_text

        bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
        key = "review-packet:" + ep.id + ":" + operation_id
        existing = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == ep.artifact_id,
                ValidationFact.operation_id == key,
            )
        )
        if existing is not None:
            if existing.parent_id != episode_id or existing.kind != "review_packet":
                raise store.HistoryConflict("Review packet identity conflict")
            return {"fact_id": existing.id, "sha256": existing.body_sha}
        artifact = store._artifact(db, ep.artifact_id, scope)
        facts = db.scalars(
            select(ValidationFact)
            .where(
                ValidationFact.artifact_id == ep.artifact_id,
                ValidationFact.kind != "review_packet",
            )
            .order_by(ValidationFact.sequence)
        ).all()
        body = {
            "episode_id": ep.id,
            "artifact_id": ep.artifact_id,
            "baseline": {
                "fact_id": "baseline",
                "sha256": artifact.baseline_sha,
                "subject_kind": artifact.subject_kind,
            },
            "manifest": manifest_in_transaction(
                db, scope, ep.artifact_id, episode_id=ep.id
            ),
            "budgets": [
                {
                    "scope_id": row.id,
                    "charged": {key: value for key, value in row.charged.items() if key not in TOKEN_USAGE_BUCKETS},
                    "provider_usage": provider_usage(db, row),
                    "allowances": _allowances(db, row),
                }
                for row in [*chain, ep]
            ],
            "facts": [
                {
                    "id": f.id,
                    "kind": f.kind,
                    "sha256": f.body_sha,
                    "target_id": f.target_id,
                    "parent_id": f.parent_id,
                    "sequence": f.sequence,
                }
                for f in facts
            ],
        }
        fact = append_fact(
            db,
            ep.artifact_id,
            operation_id=key,
            kind="review_packet",
            parent_id=ep.id,
            body=body,
        )
        return {"fact_id": fact.id, "sha256": fact.body_sha}


def _wire_size(value):
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def exact_page(body, *, fact_id, sha256, offset, limit):
    """Offset is in canonical ASCII JSON bytes, so it cannot bisect UTF-8."""
    digest, canonical = store.canonical(body)
    if digest != sha256:
        raise store.HistoryConflict("Exact history SHA mismatch")
    text = json.dumps(
        canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ValueError("Invalid history continuation offset")

    def page(end):
        return {
            "fact_id": fact_id,
            "sha256": digest,
            "encoding": "canonical-json-ascii",
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_bytes": len(text),
            "content": text[offset:end],
        }

    low, high = offset, min(len(text), offset + limit)
    while low < high:
        mid = (low + high + 1) // 2
        if _wire_size(page(mid)) <= limit:
            low = mid
        else:
            high = mid - 1
    result = page(low)
    if _wire_size(result) > limit or (low == offset and offset < len(text)):
        raise ValueError(
            "Configured packet size cannot carry history continuation metadata"
        )
    return result


def read(scope, episode_id, *, fact_id, sha256, offset=0):
    from .observations import record_read

    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        if fact_id == "baseline":
            artifact = store._artifact(db, ep.artifact_id, scope)
            if artifact.baseline_sha != sha256:
                raise store.HistoryConflict("Baseline SHA mismatch")
            return record_read("baseline", exact_page(
                db.get(ValidationBody, sha256).content,
                fact_id="baseline",
                sha256=sha256,
                offset=offset,
                limit=ep.policy["packet_bytes"],
            ))
        fact = store._fact(db, ep.artifact_id, fact_id)
        if fact.body_sha != sha256:
            raise store.HistoryConflict("History reference SHA mismatch")
        body = store._content(db, fact)
        page = exact_page(body, fact_id=fact.id, sha256=sha256,
                          offset=offset, limit=ep.policy["packet_bytes"])
        from . import source_inputs
        if fact.kind == "validation_source_input" and source_inputs.is_absence(body):
            # Observe scientific content, not fresh prerequisite selection IDs.
            # The caller still receives the untouched hash-pinned history page.
            content = source_inputs.absence_scientific_content(body)
            digest = store.canonical(content)[0]
            record_read(fact.kind, {**page, "sha256": digest})
            return page
        return record_read(fact.kind, page)


def outstanding(scope, episode_id, *, cursor=None):
    """Live, scoped keyset scan; a completed page never substitutes for readiness.

    The cursor is an after-key, not an authorization grant. Every call checks
    the bound scope/episode and computes readiness from the entire live manifest.
    """
    import base64
    from collections import Counter
    from dataclasses import asdict
    from . import source_inputs
    from .record_scope import allowed_units, review_ready, review_blockers
    from src.lib.validation_history.selections import effective_episode

    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        episode = store._fact(db, ep.artifact_id, episode_id, kind="review_episode")
        coverage = effective_episode(db, episode)
        artifact = store._artifact(db, ep.artifact_id, scope)
        manifest = manifest_in_transaction(db, scope, ep.artifact_id, episode_id=episode_id)
        blockers = review_blockers(manifest)
        units = {unit["target_id"]: unit for unit in manifest["units"]
                 if unit["target_id"] in allowed_units(coverage)}
        obligations = {item["target_id"]: item for item in coverage["obligations"]}
        targets = {fact.id: fact for fact in db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == ep.artifact_id, ValidationFact.kind == "target"))}
        context_key = store.canonical({"scope": asdict(scope), "episode_id": episode_id})[0]
        after = ""
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > ep.policy["packet_bytes"]:
                    raise ValueError("Invalid cursor size")
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                if (set(decoded) != {"context", "after"} or decoded["context"] != context_key
                        or not isinstance(decoded["after"], str)):
                    raise ValueError("Cursor belongs to another review scope")
                after = decoded["after"]
            except (ValueError, TypeError, UnicodeError) as error:
                raise ValueError("Invalid outstanding-review cursor for this bound episode") from error

        def encode(key):
            return base64.urlsafe_b64encode(json.dumps(
                {"context": context_key, "after": key}, separators=(",", ":")).encode()).decode()

        def source_record(target_id):
            while target_id is not None:
                fact = targets[target_id]
                if "object_ref" in store._content(db, fact):
                    return target_id
                target_id = fact.parent_id
            return None

        rows = []
        for blocker in blockers:
            unit_id = blocker["target_id"]
            row = dict(blocker)
            if unit_id is not None:
                unit = units[unit_id]
                target = targets[unit_id]
                body = store._content(db, target)
                parent = target.parent_id if body.get("decision_unit") is not None else target.id
                row.update(target_id=parent, decision_unit_id=unit_id,
                    expected_version=unit["version"], mode=obligations[unit_id]["mode"],
                    agent_may_decide=obligations[unit_id]["mode"] != "curator",
                    record_target_id=source_record(unit_id),
                    unit_ref={"fact_id": target.id, "sha256": target.body_sha})
                source = source_inputs.latest(db, ep.artifact_id, parent)
                if source is not None and source_inputs.is_absence(store._content(db, source)):
                    row["applicability_ref"] = source_inputs.reference(source)
                if obligations[unit_id]["mode"] == "extraction_only":
                    row["extraction_ref"] = artifact.baseline_sha
            # Each answer remains independently reachable, even for one unit.
            key = json.dumps([unit_id or "", blocker["code"],
                blocker.get("attempt_ref", {}).get("fact_id", ""),
                blocker.get("completion_ref", {}).get("fact_id", ""),
                blocker.get("binding_id", "")], separators=(",", ":"))
            rows.append((key, row))
        rows.sort(key=lambda item: item[0])
        remaining = [(key, row) for key, row in rows if key > after]
        page = {
            "episode_id": episode_id, "ready": review_ready(manifest),
            "artifact_ready": manifest["ready"], "state": ep.state,
            "source_state_sha256": manifest["source_state_sha256"],
            "decision_entries": ep.policy["decision_entries"],
            "outstanding_units": len({item["target_id"] for item in blockers if item["target_id"] is not None}),
            "outstanding_rows": len(rows), "remaining_rows": len(remaining),
            "global_blockers": dict(Counter(item["code"] for item in blockers if item["target_id"] is None)),
            "rows": [], "next_cursor": None, "scan_complete": not remaining,
        }
        for index, (key, row) in enumerate(remaining):
            candidate = {**page, "rows": [*page["rows"], row],
                "next_cursor": encode(key) if index + 1 < len(remaining) else None,
                "remaining_rows": len(remaining) - index - 1,
                "scan_complete": index + 1 == len(remaining)}
            if _wire_size(candidate) > ep.policy["packet_bytes"]:
                break
            page = candidate
        if _wire_size(page) > ep.policy["packet_bytes"] or (remaining and not page["rows"]):
            raise ValueError("Configured packet size cannot carry outstanding-review metadata; use exact review history")
        return page
