"""Domain-envelope signal extraction for TraceReview.

This analyzer is intentionally provider-agnostic. It does not validate or
interpret Alliance schema payloads; it only pulls trace-visible envelope
identifiers, object/finding references, field paths, repair-loop metadata, and
readiness blockers into compact diagnostic summaries.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from typing import Any, Iterable, Mapping, Optional


_MAX_DEPTH = 14
_MAX_LIST_ITEMS = 500
_MAX_STRING_PARSE_CHARS = 1_000_000
_NON_STABLE_DEFINITION_STATES = {"draft", "in_development", "deprecated"}
_DOMAIN_BLOCKER_SEVERITIES = {"error", "blocker"}
_REPAIR_EVENT_TYPES = {
    "repair_requested",
    "repair_patch_accepted",
    "repair_patch_rejected",
    "validation_rerun_requested",
    "repair_final_classified",
}
_CURATOR_EVENT_TYPES = {
    "curator_field_patch_accepted",
    "curator_field_patch_rejected",
}


def _as_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _state_value(value: Any) -> Optional[str]:
    normalized = _as_string(value)
    if normalized is None:
        return None
    return normalized.split(".")[-1].lower()


def _coerce_json_like(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text or text[0] not in "{[" or len(text) > _MAX_STRING_PARSE_CHARS:
        return value

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return value

    return parsed if isinstance(parsed, (dict, list)) else value


def _path_contains(path: str, *needles: str) -> bool:
    lowered = path.lower()
    return any(needle in lowered for needle in needles)


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _iter_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, list):
        for item in value:
            if _is_mapping(item):
                yield item


def _short_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 240 else f"{value[:240]}..."
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_short_value(item) for item in value[:10]]
    if isinstance(value, Mapping):
        return {
            str(key): _short_value(nested)
            for key, nested in list(value.items())[:20]
            if key not in {"payload", "projection_json", "object", "objects"}
        }
    return str(value)[:240]


class DomainEnvelopeTraceAnalyzer:
    """Extract compact domain-envelope diagnostics from trace payloads."""

    @classmethod
    def analyze(
        cls,
        trace: Mapping[str, Any],
        observations: list[Mapping[str, Any]],
        scores: Optional[list[Mapping[str, Any]]] = None,
    ) -> dict[str, Any]:
        raw_trace = trace.get("raw_trace", trace)
        raw_scores = scores
        if raw_scores is None and isinstance(trace, Mapping):
            embedded_scores = trace.get("scores")
            raw_scores = embedded_scores if isinstance(embedded_scores, list) else None

        roots: list[tuple[str, Any]] = [
            ("raw_trace", raw_trace),
            ("observations", observations),
        ]
        if raw_scores is not None:
            roots.append(("scores", raw_scores))
        return cls._analyze_roots(roots)

    @classmethod
    def analyze_payload(cls, payload: Any, source_name: str = "payload") -> dict[str, Any]:
        return cls._analyze_roots([(source_name, payload)])

    @classmethod
    def compact(cls, summary: Mapping[str, Any]) -> dict[str, Any]:
        """Return a small form suitable for trace metadata and summary cards."""
        summary_counts = dict(summary.get("summary") or {})
        return {
            "found": bool(summary.get("found")),
            "summary": summary_counts,
            "envelope_ids": list(summary.get("envelope_ids") or []),
            "object_ids": list(summary.get("object_ids") or []),
            "pending_ref_ids": list(summary.get("pending_ref_ids") or []),
            "finding_ids": list(summary.get("finding_ids") or []),
            "field_paths": list(summary.get("field_paths") or []),
            "validation_state_counts": dict(summary.get("validation_state_counts") or {}),
            "definition_state_counts": dict(summary.get("definition_state_counts") or {}),
            "has_repair_loop": bool(summary.get("repair_attempts")),
            "has_blockers": bool(summary.get("blockers")),
            "has_definition_state_flags": bool(summary.get("definition_state_flags")),
        }

    @classmethod
    def _analyze_roots(cls, roots: list[tuple[str, Any]]) -> dict[str, Any]:
        accumulator = cls._empty_summary()

        for root_path, root_value in roots:
            for source_path, node in cls._walk(root_value, root_path):
                if isinstance(node, Mapping):
                    cls._inspect_mapping(accumulator, node, source_path)

        return cls._finalize(accumulator)

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        return {
            "found": False,
            "summary": {},
            "envelope_ids": [],
            "object_ids": [],
            "pending_ref_ids": [],
            "finding_ids": [],
            "field_paths": [],
            "validation_states": [],
            "validation_state_counts": {},
            "definition_state_counts": {},
            "envelopes": [],
            "objects": [],
            "validation_findings": [],
            "repair_attempts": [],
            "definition_state_flags": [],
            "blockers": [],
            "curator_edits": [],
            "projections": [],
            "submission_states": [],
            "_seen": {},
            "_definition_state_counter": Counter(),
            "_validation_state_counter": Counter(),
        }

    @classmethod
    def _walk(
        cls,
        value: Any,
        path: str,
        depth: int = 0,
    ) -> Iterable[tuple[str, Any]]:
        if depth > _MAX_DEPTH:
            return

        coerced = _coerce_json_like(value)
        yield path, coerced

        if isinstance(coerced, Mapping):
            for key, nested in coerced.items():
                key_path = f"{path}.{key}" if path else str(key)
                yield from cls._walk(nested, key_path, depth + 1)
            return

        if isinstance(coerced, list):
            for index, nested in enumerate(coerced[:_MAX_LIST_ITEMS]):
                yield from cls._walk(nested, f"{path}[{index}]", depth + 1)

    @classmethod
    def _inspect_mapping(
        cls,
        accumulator: dict[str, Any],
        payload: Mapping[str, Any],
        source_path: str,
    ) -> None:
        envelope_id = _as_string(payload.get("envelope_id"))
        object_id, pending_ref_id, object_type = cls._object_reference(payload)
        finding_id = _as_string(payload.get("finding_id"))
        field_path = cls._field_path(payload)
        nested_envelope_object = (
            envelope_id is None
            and _path_contains(source_path, ".objects[", ".curatable_objects[")
        )
        nested_envelope_finding = (
            envelope_id is None
            and _path_contains(source_path, ".validation_findings[")
        )
        nested_envelope_history = (
            envelope_id is None
            and _path_contains(source_path, ".history[")
        )

        cls._add_unique(accumulator, "envelope_ids", envelope_id)
        cls._add_unique(accumulator, "object_ids", object_id)
        cls._add_unique(accumulator, "pending_ref_ids", pending_ref_id)
        cls._add_unique(accumulator, "finding_ids", finding_id)
        cls._add_unique(accumulator, "field_paths", field_path)

        validation_state = _state_value(payload.get("validation_state"))
        if validation_state and not nested_envelope_object:
            cls._record_validation_state(accumulator, validation_state)

        definition_state = _state_value(payload.get("definition_state"))
        if definition_state and not nested_envelope_object:
            cls._record_definition_state(
                accumulator,
                definition_state=definition_state,
                source_path=source_path,
                envelope_id=envelope_id,
                object_id=object_id,
                pending_ref_id=pending_ref_id,
                object_type=object_type,
                field_path=field_path,
                source="payload",
                message=_as_string(payload.get("message")),
            )

        if cls._looks_like_envelope(payload):
            cls._record_envelope(accumulator, payload, source_path)

        if cls._looks_like_object(payload) and not nested_envelope_object:
            cls._record_object(
                accumulator,
                payload,
                source_path=source_path,
                envelope_id=envelope_id,
            )

        if cls._looks_like_finding(payload) and not nested_envelope_finding:
            cls._record_finding(
                accumulator,
                payload,
                source_path=source_path,
                envelope_id=envelope_id,
            )

        if cls._looks_like_repair(payload) and not nested_envelope_history:
            cls._record_repair_attempt(accumulator, payload, source_path)

        if cls._looks_like_curator_edit(payload) and not nested_envelope_history:
            cls._record_curator_edit(accumulator, payload, source_path)

        if cls._looks_like_blocker(payload, source_path) and not nested_envelope_finding:
            cls._record_blocker(accumulator, payload, source_path)

        if cls._looks_like_projection(payload, source_path):
            cls._record_projection(accumulator, payload, source_path)

        if cls._looks_like_submission_state(payload, source_path):
            cls._record_submission_state(accumulator, payload, source_path)

    @staticmethod
    def _looks_like_envelope(payload: Mapping[str, Any]) -> bool:
        return "envelope_id" in payload and (
            "domain_pack_id" in payload
            or "objects" in payload
            or "curatable_objects" in payload
            or "validation_findings" in payload
            or "history" in payload
        )

    @staticmethod
    def _looks_like_object(payload: Mapping[str, Any]) -> bool:
        has_identity = "object_id" in payload or "pending_ref_id" in payload
        has_object_shape = (
            "object_type" in payload
            or "payload" in payload
            or "evidence_record_ids" in payload
            or "field_refs" in payload
            or "repair_hints" in payload
            or "validation_state" in payload
        )
        return has_identity and has_object_shape

    @staticmethod
    def _looks_like_finding(payload: Mapping[str, Any]) -> bool:
        if "finding_id" in payload:
            return True
        has_finding_fields = (
            ("severity" in payload or "code" in payload)
            and "message" in payload
            and ("field_ref" in payload or "object_ref" in payload or "field_path" in payload)
        )
        return has_finding_fields

    @staticmethod
    def _looks_like_repair(payload: Mapping[str, Any]) -> bool:
        action = _as_string(payload.get("repair_action"))
        event_type = _state_value(payload.get("event_type"))
        return bool(
            action
            or payload.get("retry_budget")
            or payload.get("source_finding_ids")
            or ("patch_id" in payload and "operations" in payload)
            or event_type in _REPAIR_EVENT_TYPES
        )

    @staticmethod
    def _looks_like_curator_edit(payload: Mapping[str, Any]) -> bool:
        event_type = _state_value(payload.get("event_type"))
        actor_type = _state_value(payload.get("actor_type"))
        return event_type in _CURATOR_EVENT_TYPES or (
            actor_type == "human"
            and ("field_ref" in payload or "field_path" in payload)
        )

    @staticmethod
    def _looks_like_blocker(payload: Mapping[str, Any], source_path: str) -> bool:
        severity = _state_value(payload.get("severity"))
        code = _as_string(payload.get("code")) or ""
        return bool(
            _path_contains(source_path, "blocker")
            or severity == "blocker"
            or code.startswith("domain_envelope.")
        ) and (
            "message" in payload
            or "status" in payload
            or "field_path" in payload
            or "details" in payload
        )

    @staticmethod
    def _looks_like_projection(payload: Mapping[str, Any], source_path: str) -> bool:
        return bool(
            "projection_ref" in payload
            or _path_contains(source_path, "projection_ref")
            or ("projection_type" in payload and "projection_key" in payload)
            or (
                "envelope_id" in payload
                and "envelope_revision" in payload
                and "review_row_count" in payload
            )
        )

    @staticmethod
    def _looks_like_submission_state(payload: Mapping[str, Any], source_path: str) -> bool:
        return _path_contains(source_path, "submission_state") or "submission_state" in payload

    @classmethod
    def _record_envelope(
        cls,
        accumulator: dict[str, Any],
        envelope: Mapping[str, Any],
        source_path: str,
    ) -> None:
        envelope_id = _as_string(envelope.get("envelope_id"))
        if envelope_id is None:
            return

        objects = envelope.get("objects")
        if not isinstance(objects, list):
            objects = envelope.get("curatable_objects")
        object_items = list(_iter_mappings(objects))

        findings = list(_iter_mappings(envelope.get("validation_findings")))
        history = list(_iter_mappings(envelope.get("history")))

        cls._add_unique(accumulator, "envelope_ids", envelope_id)
        cls._add_detail(
            accumulator,
            "envelopes",
            (
                envelope_id,
                _as_string(envelope.get("envelope_revision"))
                or _as_string(envelope.get("revision"))
                or source_path,
            ),
            {
                "envelope_id": envelope_id,
                "envelope_revision": _as_string(envelope.get("envelope_revision"))
                or _as_string(envelope.get("revision")),
                "domain_pack_id": _as_string(envelope.get("domain_pack_id")),
                "domain_pack_version": _as_string(envelope.get("domain_pack_version")),
                "status": _state_value(envelope.get("status")),
                "object_count": len(object_items),
                "validation_finding_count": len(findings),
                "history_event_count": len(history),
                "source_path": source_path,
            },
        )

        schema_ref = envelope.get("schema_ref")
        if isinstance(schema_ref, Mapping):
            definition_state = _state_value(schema_ref.get("definition_state"))
            if definition_state:
                cls._record_definition_state(
                    accumulator,
                    definition_state=definition_state,
                    source_path=f"{source_path}.schema_ref",
                    envelope_id=envelope_id,
                    object_id=None,
                    pending_ref_id=None,
                    object_type=None,
                    field_path=None,
                    source="domain_envelope.schema_ref",
                    message=None,
                )

        for index, obj in enumerate(object_items):
            cls._record_object(
                accumulator,
                obj,
                source_path=f"{source_path}.objects[{index}]",
                envelope_id=envelope_id,
            )

        for index, finding in enumerate(findings):
            cls._record_finding(
                accumulator,
                finding,
                source_path=f"{source_path}.validation_findings[{index}]",
                envelope_id=envelope_id,
            )

        for index, event in enumerate(history):
            event_path = f"{source_path}.history[{index}]"
            if cls._looks_like_repair(event):
                cls._record_repair_attempt(accumulator, event, event_path, envelope_id=envelope_id)
            if cls._looks_like_curator_edit(event):
                cls._record_curator_edit(accumulator, event, event_path, envelope_id=envelope_id)

    @classmethod
    def _record_object(
        cls,
        accumulator: dict[str, Any],
        obj: Mapping[str, Any],
        *,
        source_path: str,
        envelope_id: Optional[str],
    ) -> None:
        object_id, pending_ref_id, object_type = cls._object_reference(obj)
        if object_id is None and pending_ref_id is None:
            return

        cls._add_unique(accumulator, "object_ids", object_id)
        cls._add_unique(accumulator, "pending_ref_ids", pending_ref_id)

        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), Mapping) else {}
        validation_state = _state_value(obj.get("validation_state")) or _state_value(
            metadata.get("validation_state") if isinstance(metadata, Mapping) else None
        )
        if validation_state:
            cls._record_validation_state(accumulator, validation_state)

        definition_state = _state_value(obj.get("definition_state"))
        if definition_state:
            cls._record_definition_state(
                accumulator,
                definition_state=definition_state,
                source_path=source_path,
                envelope_id=envelope_id,
                object_id=object_id,
                pending_ref_id=pending_ref_id,
                object_type=object_type,
                field_path=None,
                source="domain_envelope.object",
                message=None,
            )

        schema_ref = obj.get("schema_ref")
        if isinstance(schema_ref, Mapping):
            schema_definition_state = _state_value(schema_ref.get("definition_state"))
            if schema_definition_state:
                cls._record_definition_state(
                    accumulator,
                    definition_state=schema_definition_state,
                    source_path=f"{source_path}.schema_ref",
                    envelope_id=envelope_id,
                    object_id=object_id,
                    pending_ref_id=pending_ref_id,
                    object_type=object_type,
                    field_path=None,
                    source="domain_envelope.object.schema_ref",
                    message=None,
                )

        for field_ref in _iter_mappings(obj.get("field_refs")):
            field_path = cls._field_path(field_ref)
            cls._add_unique(accumulator, "field_paths", field_path)

        cls._add_detail(
            accumulator,
            "objects",
            (envelope_id, object_id, pending_ref_id, source_path),
            {
                "envelope_id": envelope_id,
                "object_id": object_id,
                "pending_ref_id": pending_ref_id,
                "object_type": object_type,
                "object_role": _as_string(obj.get("object_role")),
                "status": _state_value(obj.get("status")),
                "validation_state": validation_state,
                "definition_state": definition_state,
                "model_ref": _as_string(obj.get("model_ref")),
                "source_path": source_path,
            },
        )

    @classmethod
    def _record_finding(
        cls,
        accumulator: dict[str, Any],
        finding: Mapping[str, Any],
        *,
        source_path: str,
        envelope_id: Optional[str],
    ) -> None:
        finding_id = _as_string(finding.get("finding_id"))
        object_id, pending_ref_id, object_type = cls._object_reference(finding)
        field_path = cls._field_path(finding)
        severity = _state_value(finding.get("severity"))
        status = _state_value(finding.get("status"))

        cls._add_unique(accumulator, "finding_ids", finding_id)
        cls._add_unique(accumulator, "object_ids", object_id)
        cls._add_unique(accumulator, "pending_ref_ids", pending_ref_id)
        cls._add_unique(accumulator, "field_paths", field_path)

        detail = {
            "envelope_id": envelope_id or _as_string(finding.get("envelope_id")),
            "finding_id": finding_id,
            "severity": severity,
            "status": status,
            "code": _as_string(finding.get("code")),
            "message": _as_string(finding.get("message")),
            "object_id": object_id,
            "pending_ref_id": pending_ref_id,
            "object_type": object_type,
            "field_path": field_path,
            "source_path": source_path,
        }
        cls._add_detail(
            accumulator,
            "validation_findings",
            (detail["envelope_id"], finding_id, detail["code"], object_id, pending_ref_id, field_path, source_path),
            detail,
        )

        if severity in _DOMAIN_BLOCKER_SEVERITIES and status != "resolved":
            cls._record_blocker(accumulator, finding, source_path, envelope_id=detail["envelope_id"])

    @classmethod
    def _record_repair_attempt(
        cls,
        accumulator: dict[str, Any],
        payload: Mapping[str, Any],
        source_path: str,
        envelope_id: Optional[str] = None,
    ) -> None:
        action = _as_string(payload.get("repair_action")) or _state_value(payload.get("event_type")) or "repair"
        details = payload.get("details") if isinstance(payload.get("details"), Mapping) else {}
        target_items = list(_iter_mappings(payload.get("targets")))
        operation_items = list(_iter_mappings(payload.get("operations")))

        field_paths = cls._extract_field_paths(payload)
        finding_ids = cls._extract_finding_ids(payload)
        object_refs = cls._extract_object_refs(payload)

        for target in target_items:
            field_paths.extend(cls._extract_field_paths(target))
            finding_ids.extend(cls._extract_finding_ids(target))
            object_refs.extend(cls._extract_object_refs(target))

        for operation in operation_items:
            field_paths.extend(cls._extract_field_paths(operation))
            finding_ids.extend(cls._extract_finding_ids(operation))
            object_refs.extend(cls._extract_object_refs(operation))

        if isinstance(details, Mapping):
            field_paths.extend(cls._extract_field_paths(details))
            finding_ids.extend(cls._extract_finding_ids(details))
            object_refs.extend(cls._extract_object_refs(details))

        field_paths = cls._dedupe(field_paths)
        finding_ids = cls._dedupe(finding_ids)
        for path_value in field_paths:
            cls._add_unique(accumulator, "field_paths", path_value)
        for finding_id in finding_ids:
            cls._add_unique(accumulator, "finding_ids", finding_id)

        object_ids = cls._dedupe(ref[0] for ref in object_refs if ref[0])
        pending_ref_ids = cls._dedupe(ref[1] for ref in object_refs if ref[1])
        for object_id in object_ids:
            cls._add_unique(accumulator, "object_ids", object_id)
        for pending_ref_id in pending_ref_ids:
            cls._add_unique(accumulator, "pending_ref_ids", pending_ref_id)

        retry_budget = payload.get("retry_budget")
        if not isinstance(retry_budget, Mapping):
            retry_budget = None
            for target in target_items:
                if isinstance(target.get("retry_budget"), Mapping):
                    retry_budget = target.get("retry_budget")
                    break

        detail = {
            "repair_action": action,
            "envelope_id": envelope_id or _as_string(payload.get("envelope_id")),
            "expected_revision": _as_string(payload.get("expected_revision")),
            "patch_id": _as_string(payload.get("patch_id")),
            "event_id": _as_string(payload.get("event_id")),
            "status": _state_value(payload.get("status")),
            "classification": _state_value(payload.get("classification"))
            or _state_value(details.get("classification") if isinstance(details, Mapping) else None),
            "finding_ids": finding_ids,
            "object_ids": object_ids,
            "pending_ref_ids": pending_ref_ids,
            "field_paths": field_paths,
            "operation_count": len(operation_items),
            "target_count": len(target_items),
            "retry_budget": _short_value(retry_budget) if retry_budget else None,
            "message": _as_string(payload.get("message")),
            "source_path": source_path,
        }
        cls._add_detail(
            accumulator,
            "repair_attempts",
            (
                detail["repair_action"],
                detail["envelope_id"],
                detail["patch_id"],
                tuple(detail["finding_ids"]),
                tuple(detail["field_paths"]),
                source_path,
            ),
            detail,
        )

    @classmethod
    def _record_definition_state(
        cls,
        accumulator: dict[str, Any],
        *,
        definition_state: str,
        source_path: str,
        envelope_id: Optional[str],
        object_id: Optional[str],
        pending_ref_id: Optional[str],
        object_type: Optional[str],
        field_path: Optional[str],
        source: str,
        message: Optional[str],
    ) -> None:
        accumulator["_definition_state_counter"][definition_state] += 1
        if definition_state not in _NON_STABLE_DEFINITION_STATES:
            return

        cls._add_detail(
            accumulator,
            "definition_state_flags",
            (source, definition_state, envelope_id, object_id, pending_ref_id, field_path, source_path),
            {
                "source": source,
                "definition_state": definition_state,
                "envelope_id": envelope_id,
                "object_id": object_id,
                "pending_ref_id": pending_ref_id,
                "object_type": object_type,
                "field_path": field_path,
                "message": message,
                "source_path": source_path,
            },
        )

    @classmethod
    def _record_blocker(
        cls,
        accumulator: dict[str, Any],
        blocker: Mapping[str, Any],
        source_path: str,
        envelope_id: Optional[str] = None,
    ) -> None:
        object_id, pending_ref_id, object_type = cls._object_reference(blocker)
        field_path = cls._field_path(blocker)
        details = blocker.get("details") if isinstance(blocker.get("details"), Mapping) else {}
        projection_ref = blocker.get("projection_ref") if isinstance(blocker.get("projection_ref"), Mapping) else {}
        detail = {
            "envelope_id": envelope_id or _as_string(blocker.get("envelope_id")),
            "object_id": object_id,
            "pending_ref_id": pending_ref_id,
            "object_type": object_type,
            "field_path": field_path,
            "severity": _state_value(blocker.get("severity")),
            "status": _state_value(blocker.get("status")),
            "code": _as_string(blocker.get("code")),
            "message": _as_string(blocker.get("message")),
            "finding_id": _as_string(blocker.get("finding_id")) or _as_string(details.get("finding_id")),
            "projection_ref": _short_value(projection_ref) if projection_ref else {},
            "details": _short_value(details) if details else {},
            "source_path": source_path,
        }
        cls._add_unique(accumulator, "field_paths", field_path)
        cls._add_unique(accumulator, "finding_ids", detail["finding_id"])
        cls._add_detail(
            accumulator,
            "blockers",
            (
                detail["envelope_id"],
                detail["object_id"],
                detail["pending_ref_id"],
                detail["field_path"],
                detail["code"],
                detail["message"],
                source_path,
            ),
            detail,
        )

    @classmethod
    def _record_curator_edit(
        cls,
        accumulator: dict[str, Any],
        payload: Mapping[str, Any],
        source_path: str,
        envelope_id: Optional[str] = None,
    ) -> None:
        object_id, pending_ref_id, object_type = cls._object_reference(payload)
        field_path = cls._field_path(payload)
        detail = {
            "event_id": _as_string(payload.get("event_id")),
            "event_type": _state_value(payload.get("event_type")),
            "actor_type": _state_value(payload.get("actor_type")),
            "actor_id": _as_string(payload.get("actor_id")),
            "envelope_id": envelope_id or _as_string(payload.get("envelope_id")),
            "object_id": object_id,
            "pending_ref_id": pending_ref_id,
            "object_type": object_type,
            "field_path": field_path,
            "message": _as_string(payload.get("message")),
            "details": _short_value(payload.get("details")) if isinstance(payload.get("details"), Mapping) else {},
            "source_path": source_path,
        }
        cls._add_unique(accumulator, "field_paths", field_path)
        cls._add_detail(
            accumulator,
            "curator_edits",
            (
                detail["event_id"],
                detail["event_type"],
                detail["envelope_id"],
                detail["object_id"],
                detail["pending_ref_id"],
                detail["field_path"],
                source_path,
            ),
            detail,
        )

    @classmethod
    def _record_projection(
        cls,
        accumulator: dict[str, Any],
        payload: Mapping[str, Any],
        source_path: str,
    ) -> None:
        projection_ref = payload.get("projection_ref") if isinstance(payload.get("projection_ref"), Mapping) else payload
        envelope_id = _as_string(projection_ref.get("envelope_id")) or _as_string(payload.get("envelope_id"))
        object_id = _as_string(projection_ref.get("object_id")) or _as_string(payload.get("object_id"))
        field_path = _as_string(projection_ref.get("field_path")) or cls._field_path(payload)

        cls._add_unique(accumulator, "envelope_ids", envelope_id)
        cls._add_unique(accumulator, "object_ids", object_id)
        cls._add_unique(accumulator, "field_paths", field_path)

        detail = {
            "envelope_id": envelope_id,
            "object_id": object_id,
            "field_path": field_path,
            "envelope_revision": _as_string(projection_ref.get("envelope_revision"))
            or _as_string(payload.get("envelope_revision")),
            "projection_type": _as_string(payload.get("projection_type"))
            or ("review_rows" if "review_row_count" in payload else None),
            "projection_key": _as_string(payload.get("projection_key")),
            "projection_status": _state_value(payload.get("projection_status")),
            "candidate_id": _as_string(payload.get("candidate_id")),
            "review_row_count": _as_string(payload.get("review_row_count")),
            "source_path": source_path,
        }
        cls._add_detail(
            accumulator,
            "projections",
            (
                detail["envelope_id"],
                detail["object_id"],
                detail["field_path"],
                detail["envelope_revision"],
                detail["projection_type"],
                detail["projection_key"],
                source_path,
            ),
            detail,
        )

    @classmethod
    def _record_submission_state(
        cls,
        accumulator: dict[str, Any],
        payload: Mapping[str, Any],
        source_path: str,
    ) -> None:
        state_payload = payload.get("submission_state")
        if isinstance(state_payload, Mapping):
            state_data = state_payload
            state_source_path = f"{source_path}.submission_state"
        else:
            state_data = payload
            state_source_path = source_path

        detail = {
            "envelope_id": _as_string(payload.get("envelope_id")),
            "object_id": _as_string(payload.get("object_id")),
            "status": _state_value(payload.get("status")),
            "state": _short_value(state_data),
            "source_path": state_source_path,
        }
        cls._add_unique(accumulator, "envelope_ids", detail["envelope_id"])
        cls._add_unique(accumulator, "object_ids", detail["object_id"])
        cls._add_detail(
            accumulator,
            "submission_states",
            (detail["envelope_id"], detail["object_id"], state_source_path),
            detail,
        )

    @staticmethod
    def _record_validation_state(accumulator: dict[str, Any], validation_state: str) -> None:
        accumulator["_validation_state_counter"][validation_state] += 1
        DomainEnvelopeTraceAnalyzer._add_unique(accumulator, "validation_states", validation_state)

    @staticmethod
    def _object_reference(payload: Mapping[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        ref_source: Mapping[str, Any] = payload
        field_ref = payload.get("field_ref")
        if isinstance(field_ref, Mapping) and isinstance(field_ref.get("object_ref"), Mapping):
            ref_source = field_ref["object_ref"]
        elif isinstance(payload.get("object_ref"), Mapping):
            ref_source = payload["object_ref"]

        return (
            _as_string(ref_source.get("object_id")),
            _as_string(ref_source.get("pending_ref_id")),
            _as_string(ref_source.get("object_type")) or _as_string(payload.get("object_type")),
        )

    @staticmethod
    def _field_path(payload: Mapping[str, Any]) -> Optional[str]:
        direct = _as_string(payload.get("field_path"))
        if direct:
            return direct
        field_ref = payload.get("field_ref")
        if isinstance(field_ref, Mapping):
            return _as_string(field_ref.get("field_path"))
        return None

    @classmethod
    def _extract_field_paths(cls, payload: Mapping[str, Any]) -> list[str]:
        paths: list[str] = []
        field_path = cls._field_path(payload)
        if field_path:
            paths.append(field_path)
        for field in ("field_paths", "target_field_paths"):
            values = payload.get(field)
            if isinstance(values, list):
                paths.extend(value for value in (_as_string(item) for item in values) if value)
        return paths

    @classmethod
    def _extract_finding_ids(cls, payload: Mapping[str, Any]) -> list[str]:
        finding_ids: list[str] = []
        for key in ("finding_id",):
            value = _as_string(payload.get(key))
            if value:
                finding_ids.append(value)
        for key in ("finding_ids", "source_finding_ids"):
            values = payload.get(key)
            if isinstance(values, list):
                finding_ids.extend(value for value in (_as_string(item) for item in values) if value)
        return finding_ids

    @classmethod
    def _extract_object_refs(cls, payload: Mapping[str, Any]) -> list[tuple[Optional[str], Optional[str], Optional[str]]]:
        refs = [cls._object_reference(payload)]
        object_ref = payload.get("object_ref")
        if isinstance(object_ref, Mapping):
            refs.append(cls._object_reference({"object_ref": object_ref}))
        return [ref for ref in refs if ref[0] or ref[1]]

    @staticmethod
    def _add_unique(accumulator: dict[str, Any], key: str, value: Optional[str]) -> None:
        if value is None:
            return
        seen = accumulator["_seen"].setdefault(key, set())
        if value in seen:
            return
        seen.add(value)
        accumulator[key].append(value)

    @staticmethod
    def _add_detail(
        accumulator: dict[str, Any],
        key: str,
        identity: tuple[Any, ...],
        detail: dict[str, Any],
    ) -> None:
        seen_key = f"{key}:details"
        seen = accumulator["_seen"].setdefault(seen_key, set())
        if identity in seen:
            return
        seen.add(identity)
        accumulator[key].append(detail)

    @staticmethod
    def _dedupe(values: Iterable[Optional[str]]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value is None or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    @staticmethod
    def _finalize(accumulator: dict[str, Any]) -> dict[str, Any]:
        accumulator["definition_state_counts"] = dict(accumulator["_definition_state_counter"])
        accumulator["validation_state_counts"] = dict(accumulator["_validation_state_counter"])
        accumulator["summary"] = {
            "envelope_count": len(accumulator["envelope_ids"]),
            "object_count": len(accumulator["objects"]),
            "finding_count": len(accumulator["validation_findings"]),
            "field_path_count": len(accumulator["field_paths"]),
            "repair_attempt_count": len(accumulator["repair_attempts"]),
            "definition_state_flag_count": len(accumulator["definition_state_flags"]),
            "blocker_count": len(accumulator["blockers"]),
            "curator_edit_count": len(accumulator["curator_edits"]),
            "projection_count": len(accumulator["projections"]),
            "submission_state_count": len(accumulator["submission_states"]),
        }
        accumulator["found"] = any(
            accumulator[key]
            for key in (
                "envelope_ids",
                "objects",
                "validation_findings",
                "repair_attempts",
                "blockers",
                "projections",
                "submission_states",
            )
        )
        accumulator.pop("_seen", None)
        accumulator.pop("_definition_state_counter", None)
        accumulator.pop("_validation_state_counter", None)
        return accumulator
