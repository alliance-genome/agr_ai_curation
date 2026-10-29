"""Owned, read-only Studio access to durable review facts without workspace creation."""
from sqlalchemy import select, func

from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.lib.openai_agents.config import get_validation_history_page_size
from src.lib.validation_history import store
from src.lib.validation_review.policy import ReviewLimits
from src.lib.validation_review.reads import exact_page
from src.models.sql.validation_history import ValidationBody, ValidationFact


def inspect_history(*, session_factory, user_auth_sub, envelope_id, action="summary",
                    after=0, through_sequence=None, kind=None, target_id=None,
                    fact_id=None, sha256=None, offset=0):
    """Summary -> filtered immutable fact references -> exact hash-pinned bodies."""
    try:
        if action not in {"summary", "list", "detail"}:
            raise ValueError("Unknown history action")
        for name, value in (("after", after), ("offset", offset)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        with session_factory() as db:
            row = db.get(DomainEnvelopeModel, envelope_id)
            if row is None:
                raise store.HistoryAccessError("History source unavailable")
            ref = row.envelope_json.get("metadata", {}).get("validation_history")
            if ref is None:
                # Do not disclose existence or history availability before checking visibility.
                from .domain_envelope_tools import _envelope_visible_to_user
                if not _envelope_visible_to_user(db, row=row, user_auth_sub=user_auth_sub):
                    raise store.HistoryAccessError("History source unavailable")
                raise store.HistoryConflict("Historical validation history is unavailable")
            scope = store.HistoryScope(user_auth_sub,
                str(row.document_id) if row.document_id else None, ref["session_id"])
            artifact = store._artifact(db, ref["artifact_id"], scope)
            base = {"envelope_id": envelope_id, "action": action}
            if action == "detail":
                if not fact_id or not sha256:
                    raise ValueError("fact_id and sha256 are required for detail")
                if fact_id == "baseline":
                    expected = artifact.baseline_sha
                    body = db.get(ValidationBody, expected).content
                else:
                    fact = store._fact(db, artifact.id, fact_id)
                    expected, body = fact.body_sha, store._content(db, fact)
                if sha256 != expected:
                    raise store.HistoryConflict("Historical reference changed")
                page = exact_page(body, fact_id=fact_id, sha256=sha256, offset=offset,
                                  limit=ReviewLimits.configured().packet_bytes)
                next_call = None
                if page["next_offset"] is not None:
                    next_call = {"tool": "get_domain_envelope_review_history", "arguments": {
                        **base, "fact_id": fact_id, "sha256": sha256, "offset": page["next_offset"]}}
                return {"success": True, **page, "complete": next_call is None, "next_call": next_call}
            conditions = [ValidationFact.artifact_id == artifact.id]
            if action == "summary":
                high = db.scalar(select(func.max(ValidationFact.sequence)).where(*conditions)) or 0
                counts = dict(db.execute(select(ValidationFact.kind, func.count())
                    .where(*conditions, ValidationFact.sequence <= high)
                    .group_by(ValidationFact.kind)).all())
                return {"success": True, "envelope_id": envelope_id, "revision": row.revision,
                    "through_sequence": high, "kind_counts": counts,
                    "baseline": {"fact_id": "baseline", "sha256": artifact.baseline_sha,
                        "subject_kind": artifact.subject_kind,
                        "original_available": artifact.subject_kind != "historical"},
                    "instruction": "List relevant kinds using through_sequence, then read exact fact bodies. "
                        "target_id filters only directly anchored facts; decisions may reference targets inside their bodies. "
                        "Current materialized state is read with get_domain_envelope_state."}
            if type(through_sequence) is not int or through_sequence < after:
                raise ValueError("list requires through_sequence from summary, at least after")
            conditions += [ValidationFact.sequence > after, ValidationFact.sequence <= through_sequence]
            if kind is not None:
                conditions.append(ValidationFact.kind == kind)
            if target_id is not None:
                conditions.append(ValidationFact.target_id == target_id)
            limit = get_validation_history_page_size()
            rows = list(db.scalars(select(ValidationFact).where(*conditions)
                        .order_by(ValidationFact.sequence).limit(limit + 1)))
            items = [{"fact_id": f.id, "kind": f.kind, "target_id": f.target_id,
                      "parent_id": f.parent_id, "sequence": f.sequence, "sha256": f.body_sha}
                     for f in rows[:limit]]
            next_call = None
            if len(rows) > limit:
                next_call = {"tool": "get_domain_envelope_review_history", "arguments": {
                    **base, "after": items[-1]["sequence"], "through_sequence": through_sequence,
                    **({"kind": kind} if kind is not None else {}),
                    **({"target_id": target_id} if target_id is not None else {})}}
            return {"success": True, "items": items, "through_sequence": through_sequence,
                    "complete": next_call is None, "next_call": next_call}
    except store.HistoryAccessError:
        return {"success": False, "code": "history_access_denied", "error": "History source unavailable"}
    except store.HistoryConflict as exc:
        return {"success": False, "code": "history_unavailable_or_changed", "error": str(exc)}
    except ValueError as exc:
        return {"success": False, "code": "invalid_history_request", "error": str(exc)}
