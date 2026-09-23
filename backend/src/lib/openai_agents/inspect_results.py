"""Read-only supervisor inspection for persisted extraction results.

Every response fits the shared tool result budget (TOOL_RESULT_MAX_BYTES,
ALL-1287). The supervisor starts from a counts-only ``summary``, then filters
object, finding and validator-result pages, and reads any value too large for
a page exactly in chunks. Pages and chunks carry ``next_call`` arguments and a
``result_sha256``, so a result that changes between calls is reported instead
of mixed. Values are never shortened: a value too long to show inline is
replaced by a descriptor (size, hash, a short prefix preview) whose ``read``
call returns it exactly. Every read first resolves the result through the
curator's authorized records (session, document or flow run).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

from pydantic import ValidationError

from src.lib.observability.runtime import report_runtime_exception
from src.lib.chat_state import document_state
from src.lib.context import get_current_session_id, get_current_user_id
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.domain_packs.resolvable_values import (
    LEGACY_EXPLANATION,
    LOOKUP_OUTCOME_KEY,
    OUTCOME_LEGACY_UNVERIFIED,
    RESOLUTION_STATE_KEY,
    UNRESOLVED,
    VALIDATOR_EXPLANATION_KEY,
    declared_resolvable_fields,
    has_resolution_state,
    holds_resolution,
    unresolved_header_text,
)
from src.lib.domain_packs.supervisor_manifest import (
    SupervisorManifestPolicy,
    supervisor_manifest_policy_for_object,
)
from src.lib.openai_agents.config import (
    get_inspect_results_evidence_page_size,
    get_inspect_results_evidence_text_limit,
    get_inspect_results_list_page_size,
    get_inspect_results_object_max_page_size,
    get_inspect_results_object_page_size,
    get_inspect_results_validation_page_size,
    get_supervisor_field_text_limit,
    get_supervisor_max_list_limit,
    get_supervisor_text_preview_limit,
)
from src.lib.openai_agents.extraction_manifest import (
    supervisor_manifest_objects,
    validator_result_entries,
)
from src.lib.openai_agents.tool_result_bounds import (
    INVALID_RESULT_CURSOR,
    STALE_RESULT_CURSOR,
    ToolResultBudgetError,
    budget_failure,
    canonical_json,
    clamp_page_limit,
    content_sha256,
    fit_page,
    fit_text_window,
    report_budget_failure_result,
    resolve_path,
    serialized_size,
    tool_result_budget,
)
from src.schemas.curation_workspace import CurationExtractionSourceKind
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
    parse_field_path,
)
from src.schemas.domain_pack_metadata import DomainPackMetadata


_TOOL_NAME = "inspect_results"
_COMPONENT = "supervisor_inspect_results"
_RESULT_REF_PREFIX = "extraction-result:"
_FINDING_INDEX_PREFIX = "finding-index:"
_ACTIONS = frozenset(
    {
        "help",
        "list",
        "search",
        "summary",
        "objects",
        "object",
        "field",
        "details",
        "evidence",
        "validation",
        "validator_results",
    }
)
_TARGETS = frozenset(
    {"latest", "this_chat", "current_document", "flow_run", "all_authorized"}
)
_OBJECT_STATUSES = tuple(status.value for status in CuratableObjectStatus)
_SEVERITIES = tuple(severity.value for severity in ValidationFindingSeverity)
# Object filter: any open finding / findings but none open / no findings / any.
_OBJECT_VALIDATION_STATES = ("open", "resolved", "none", "any")
# Finding filter: open, or closed (resolved or waived).
_FINDING_VALIDATION_STATES = ("open", "resolved")
# Env-configurable; see config.py getters and .env.example. Page sizes are row
# ceilings: every page also ends at TOOL_RESULT_MAX_BYTES.
_MAX_LIST_LIMIT = get_supervisor_max_list_limit()
_EVIDENCE_PAGE_SIZE = get_inspect_results_evidence_page_size()
_SNIPPET_LIMIT = get_inspect_results_evidence_text_limit()
_RESULT_LIST_PAGE_SIZE = get_inspect_results_list_page_size()
_VALIDATION_PAGE_SIZE = get_inspect_results_validation_page_size()
# Values longer than this are shown as descriptors with an exact read call.
_FIELD_TEXT_LIMIT = get_supervisor_field_text_limit()
_TEXT_PREVIEW_LIMIT = get_supervisor_text_preview_limit()
_EVIDENCE_PATH_PARTS = frozenset(
    {
        "evidence",
        "evidence_records",
        "evidence_items",
        "evidence_anchors",
        "verified_quote",
        "quote",
        "evidence_quote",
        "source_quote",
        "snippet",
        "source_chunk_id",
        "chunk_id",
        "chunk_text",
        "source_text",
    }
)
_EVIDENCE_TEXT_KEYS = (
    "verified_quote",
    "quote",
    "evidence_quote",
    "source_quote",
    "snippet",
)
_EVIDENCE_CONTEXT_KEYS = (
    "page",
    "page_number",
    "section",
    "source_section",
    "subsection",
    "figure_reference",
    "chunk_id",
    "source_chunk_id",
    "status",
    "confidence",
)
# View arguments each action honors; any other one is rejected, never ignored.
# result_ref, target, adapter_keys and flow_run_id are accepted by every action;
# cursor, limit and result_sha256 only by views that continue (see
# _continuation_arguments).
_ACTION_ARGUMENTS: dict[str, frozenset[str]] = {
    "list": frozenset(),
    "search": frozenset({"query"}),
    "summary": frozenset(),
    "objects": frozenset(
        {"object_type", "status", "validation_state", "severity", "query", "field_path", "fields"}
    ),
    "object": frozenset({"object_ref"}),
    "field": frozenset({"object_ref", "field_path"}),
    "details": frozenset({"object_ref", "field_path"}),
    "evidence": frozenset({"object_ref", "detail_path"}),
    "validation": frozenset(
        {"object_ref", "field_path", "object_type", "validation_state", "severity",
         "query", "finding_ref", "detail_path"}
    ),
    "validator_results": frozenset(
        {"object_ref", "field_path", "object_type", "status", "validation_state",
         "validator_result_key", "detail_path"}
    ),
}
_PAGE_ARGUMENTS = frozenset({"cursor", "limit", "result_sha256"})
_CHUNK_ARGUMENTS = frozenset({"cursor", "result_sha256"})
_FINDING_IDENTITY_KEYS = frozenset(
    {"finding_ref", "finding_id", "severity", "status", "code", "object_ref",
     "object_type", "field_path"}
)


class _RequestError(Exception):
    """An explicit caller-facing error; never a budget escape."""

    def __init__(self, error_code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.response = _error(error_code, message, **extra)


@dataclass(frozen=True)
class _Result:
    """One authorized result plus the arguments that reach it again."""

    record: Any
    envelope: DomainEnvelope
    result_ref: str
    extraction_result_id: str
    content_sha256: str
    carry: Mapping[str, Any]

    def head(self, action: str) -> dict[str, Any]:
        return {
            "action": action,
            "result_ref": self.result_ref,
            "extraction_result_id": self.extraction_result_id,
        }

    def call(self, action: str, **args: Any) -> dict[str, Any]:
        return {
            "action": action,
            **self.carry,
            **{key: value for key, value in args.items() if value is not None},
        }

    def sha(self, **view: Any) -> str:
        """Fingerprint of this result's content plus the view being paged."""

        return content_sha256({"result": self.content_sha256, "view": view})


async def inspect_results(
    *,
    action: str = "help",
    query: str | None = None,
    result_ref: str | None = None,
    target: str = "latest",
    object_ref: str | None = None,
    field_path: str | None = None,
    adapter_keys: list[str] | None = None,
    flow_run_id: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    object_type: str | None = None,
    status: str | None = None,
    validation_state: str | None = None,
    severity: str | None = None,
    fields: list[str] | None = None,
    finding_ref: str | None = None,
    validator_result_key: str | None = None,
    detail_path: str | None = None,
    result_sha256: str | None = None,
) -> str:
    """Inspect persisted canonical extraction results through bounded actions.

    Every response fits TOOL_RESULT_MAX_BYTES; an unmeetable budget returns a
    compact ``tool_result_budget_unmet`` failure that is reported once.
    """

    normalized_action = _normalize_action(action)
    try:
        response = _inspect(
            action=normalized_action,
            query=query,
            result_ref=result_ref,
            target=target,
            object_ref=object_ref,
            field_path=field_path,
            adapter_keys=adapter_keys,
            flow_run_id=flow_run_id,
            cursor=cursor,
            limit=limit,
            object_type=object_type,
            status=status,
            validation_state=validation_state,
            severity=severity,
            fields=fields,
            finding_ref=finding_ref,
            validator_result_key=validator_result_key,
            detail_path=detail_path,
            result_sha256=result_sha256,
        )
    except _RequestError as exc:
        response = exc.response
    except ToolResultBudgetError as exc:
        return _budget_failure(exc, action=normalized_action)
    budget = tool_result_budget()
    measured = serialized_size(response)
    if measured > budget:
        return _budget_failure(
            ToolResultBudgetError(measured=measured, limit=budget),
            action=normalized_action,
        )
    return json.dumps(response, ensure_ascii=True, default=str)


def _inspect(
    *,
    action: str,
    query: str | None,
    result_ref: str | None,
    target: str,
    object_ref: str | None,
    field_path: str | None,
    adapter_keys: list[str] | None,
    flow_run_id: str | None,
    cursor: str | None,
    limit: int | None,
    object_type: str | None,
    status: str | None,
    validation_state: str | None,
    severity: str | None,
    fields: list[str] | None,
    finding_ref: str | None,
    validator_result_key: str | None,
    detail_path: str | None,
    result_sha256: str | None,
) -> dict[str, Any]:
    if action == "help":
        return _help_response()
    if action not in _ACTIONS:
        raise _RequestError(
            "invalid_action",
            "Unsupported inspect_results action. Use action=\"help\" for supported actions.",
            action=_echo(action),
        )

    supplied = {
        name
        for name, value in {
            "query": query,
            "object_ref": object_ref,
            "field_path": field_path,
            "object_type": object_type,
            "status": status,
            "validation_state": validation_state,
            "severity": severity,
            "fields": fields,
            "finding_ref": finding_ref,
            "validator_result_key": validator_result_key,
            "detail_path": detail_path,
        }.items()
        if value not in (None, "", [])
    }
    unsupported = sorted(supplied - _ACTION_ARGUMENTS[action])
    if unsupported:
        raise _RequestError(
            "invalid_request",
            f"action=\"{action}\" does not use: {', '.join(unsupported)}. Use "
            "action=\"help\" to see which filters each action supports.",
            action=action,
            supported_arguments=sorted(_ACTION_ARGUMENTS[action]),
        )
    continuation = _continuation_arguments(
        action,
        object_ref=_optional_text(object_ref),
        finding_ref=_optional_text(finding_ref),
        validator_result_key=_optional_text(validator_result_key),
        detail_path=_optional_text(detail_path),
    )
    unused = sorted(
        name
        for name, value in {
            "cursor": cursor,
            "limit": limit,
            "result_sha256": result_sha256,
        }.items()
        if value not in (None, "") and name not in continuation
    )
    if unused:
        raise _RequestError(
            "invalid_request",
            f"This action=\"{action}\" view does not use: {', '.join(unused)}. "
            "Single records (summary, object, finding_ref, validator_result_key) take "
            "no continuation arguments and exact chunk reads take no limit; pass "
            "cursor, limit and result_sha256 only from a next_call.",
            action=action,
            supported_continuation_arguments=sorted(continuation),
        )
    if action in {"validation", "validator_results"} and _optional_text(detail_path):
        single = "finding_ref" if action == "validation" else "validator_result_key"
        if not _optional_text(finding_ref if action == "validation" else validator_result_key):
            raise _RequestError(
                "invalid_request",
                f"detail_path requires {single} for action=\"{action}\"; it reads one "
                "value inside that single record.",
                action=action,
            )

    session_id = get_current_session_id()
    user_id = get_current_user_id()
    if not session_id or not user_id:
        raise _RequestError(
            "unavailable",
            "inspect_results is only available inside an active chat session.",
            action=action,
        )

    parsed_ref, ref_error = _parse_result_ref(result_ref)
    if ref_error:
        raise _RequestError(
            ref_error,
            "result_ref must use the canonical extraction-result:<uuid> form.",
            action=action,
            result_ref=_echo(result_ref),
        )

    normalized_target = _normalize_target(target)
    if normalized_target not in _TARGETS:
        raise _RequestError(
            "invalid_target",
            "Unsupported target. Use latest, this_chat, current_document, flow_run, or all_authorized.",
            action=action,
            target=_echo(normalized_target),
        )
    # Model-supplied text is echoed into filters, next_call or head: an over-long
    # value is a caller error, never a tool result budget escape.
    _check_argument_lengths(
        action,
        {
            "query": query,
            "object_ref": object_ref,
            "field_path": field_path,
            "object_type": object_type,
            "status": status,
            "validation_state": validation_state,
            "severity": severity,
            "finding_ref": finding_ref,
            "validator_result_key": validator_result_key,
            "detail_path": detail_path,
            "flow_run_id": flow_run_id,
        },
        lists={"fields": fields, "adapter_keys": adapter_keys},
    )
    normalized_query = _optional_text(query)

    records, resolve_error = _authorized_records(
        result_id=parsed_ref,
        target=normalized_target,
        session_id=session_id,
        user_id=user_id,
        flow_run_id=flow_run_id,
        adapter_keys=adapter_keys,
    )
    if resolve_error:
        raise _RequestError(
            resolve_error,
            _resolve_error_message(resolve_error),
            action=action,
            target=normalized_target,
        )

    listing_carry = {
        key: value
        for key, value in {
            "target": normalized_target if normalized_target != "latest" else None,
            "flow_run_id": _optional_text(flow_run_id),
            "adapter_keys": list(adapter_keys) if adapter_keys else None,
            "result_ref": _record_result_ref(records[0]) if parsed_ref and records else None,
        }.items()
        if value is not None
    }
    if action == "list":
        return _list_response(
            records,
            carry=listing_carry,
            cursor=cursor,
            limit=limit,
            expected_sha=result_sha256,
        )
    if action == "search":
        search_records = (
            records[:1]
            if normalized_target == "latest" and parsed_ref is None
            else records
        )
        return _search_response(
            search_records,
            target=normalized_target,
            carry=listing_carry,
            query=normalized_query,
            cursor=cursor,
            limit=limit,
            expected_sha=result_sha256,
        )

    if not records:
        raise _RequestError(
            "no_context",
            "No authorized persisted extraction results matched this request.",
            action=action,
            target=normalized_target,
        )

    record = records[0]
    try:
        envelope = _canonical_envelope_for_record(record)
    except (TypeError, ValueError, ValidationError) as exc:
        raise _RequestError(
            "unsupported_payload",
            "inspect_results only reads canonical domain-envelope extraction results.",
            action=action,
            result_ref=_record_result_ref(record),
            extraction_result_id=_record_id(record),
            detail=_echo(exc),
        ) from exc
    result = _Result(
        record=record,
        envelope=envelope,
        result_ref=_record_result_ref(record),
        extraction_result_id=_record_id(record),
        content_sha256=content_sha256(dict(_record_payload_mapping(record))),
        carry=_result_carry(record, target=normalized_target, flow_run_id=flow_run_id),
    )
    normalized_ref = _optional_text(object_ref)
    normalized_path = _optional_text(field_path)
    normalized_detail = _optional_text(detail_path)

    if action == "summary":
        return _summary_response(result)
    if action == "objects":
        return _objects_response(
            result,
            filters=_ObjectFilters.build(
                object_type=object_type,
                status=status,
                validation_state=validation_state,
                severity=severity,
                query=normalized_query,
                field_path=normalized_path,
                fields=fields,
            ),
            cursor=cursor,
            limit=limit,
            expected_sha=result_sha256,
        )
    if action == "object":
        return _object_response(result, object_ref=normalized_ref)
    if action == "field":
        return _field_response(
            result,
            object_ref=normalized_ref,
            field_path=normalized_path,
            cursor=cursor,
            expected_sha=result_sha256,
        )
    if action == "details":
        return _details_response(
            result,
            object_ref=normalized_ref,
            field_path=normalized_path,
            cursor=cursor,
            limit=limit,
            expected_sha=result_sha256,
        )
    if action == "evidence":
        return _evidence_response(
            result,
            object_ref=normalized_ref,
            detail_path=normalized_detail,
            cursor=cursor,
            limit=limit,
            expected_sha=result_sha256,
        )
    if action == "validation":
        return _validation_response(
            result,
            object_ref=normalized_ref,
            field_path=normalized_path,
            object_type=_optional_text(object_type),
            validation_state=_choice(
                validation_state, _FINDING_VALIDATION_STATES, "validation_state"
            ),
            severity=_choice(severity, _SEVERITIES, "severity"),
            query=normalized_query,
            finding_ref=_optional_text(finding_ref),
            detail_path=normalized_detail,
            cursor=cursor,
            limit=limit,
            expected_sha=result_sha256,
        )
    return _validator_results_response(
        result,
        object_ref=normalized_ref,
        field_path=normalized_path,
        object_type=_optional_text(object_type),
        decision_status=_optional_text(status),
        validation_state=_choice(
            validation_state, _FINDING_VALIDATION_STATES, "validation_state"
        ),
        key=_optional_text(validator_result_key),
        detail_path=normalized_detail,
        cursor=cursor,
        limit=limit,
        expected_sha=result_sha256,
    )


def _continuation_arguments(
    action: str,
    *,
    object_ref: str | None,
    finding_ref: str | None,
    validator_result_key: str | None,
    detail_path: str | None,
) -> frozenset[str]:
    """Continuation arguments the requested view uses; others are rejected.

    Pages take cursor, limit and result_sha256; exact chunk reads take cursor
    and result_sha256; single-record views take none.
    """

    if action in {"summary", "object"}:
        return frozenset()
    if action == "field":
        return _CHUNK_ARGUMENTS
    if (action == "validation" and finding_ref) or (
        action == "validator_results" and validator_result_key
    ):
        return _CHUNK_ARGUMENTS if detail_path else frozenset()
    if action == "evidence" and object_ref and detail_path:
        return _CHUNK_ARGUMENTS
    return _PAGE_ARGUMENTS


def _check_argument_lengths(
    action: str,
    texts: Mapping[str, Any],
    *,
    lists: Mapping[str, Sequence[Any] | None],
) -> None:
    """Reject over-long model-supplied text as a caller error, without echoing it."""

    too_long = [
        name
        for name, value in texts.items()
        if value is not None and len(str(value).strip()) > _FIELD_TEXT_LIMIT
    ]
    too_long.extend(
        name
        for name, values in lists.items()
        if values and any(len(str(item).strip()) > _FIELD_TEXT_LIMIT for item in values)
    )
    if too_long:
        raise _RequestError(
            "invalid_request",
            f"{', '.join(too_long)} longer than {_FIELD_TEXT_LIMIT} characters. Pass "
            "exact refs, field paths and filter values from earlier responses, or search "
            "for a shorter distinctive phrase.",
            action=action,
            arguments_too_long=too_long,
        )


def _echo_list(values: Sequence[Any], name: str) -> dict[str, Any]:
    """Echo a caller-supplied list: first entries within the preview limit plus counts."""

    shown: list[str] = []
    used = 0
    for value in values:
        text = _echo(value)
        if shown and used + len(text) > _TEXT_PREVIEW_LIMIT:
            break
        shown.append(text)
        used += len(text)
    return {
        name: shown,
        f"{name}_count": len(values),
        f"{name}_omitted_count": len(values) - len(shown),
    }


def _help_response() -> dict[str, Any]:
    return {
        "status": "ok",
        "message": (
            "inspect_results browses persisted canonical extraction results without "
            "rerunning specialists. Start with summary (counts), then filter pages."
        ),
        "action": "help",
        "actions": sorted(_ACTIONS),
        "targets": sorted(_TARGETS),
        "result_ref_format": "extraction-result:<uuid>",
        "filters": {
            "objects": "object_type, status, validation_state (open|resolved|none|any), "
            "severity, query (all terms), field_path (scope query or require a value), "
            "fields (select summary fields)",
            "validation": "object_ref, object_type, field_path, validation_state "
            "(open|resolved), severity, query; finding_ref reads one finding and "
            "detail_path (with finding_ref) one exact value inside it",
            "validator_results": "status (validator decision), validation_state, "
            "object_ref, object_type, field_path; validator_result_key reads one "
            "result and detail_path (with validator_result_key) one exact value inside it",
            "evidence": "object_ref for its evidence records; detail_path "
            "(<index>.<key>, with object_ref) reads one exact evidence value",
        },
        "boundaries": [
            "Every response fits the tool result budget. Continue pages and chunks by "
            "passing next_call exactly; a changed result returns stale_result_cursor.",
            "cursor, limit and result_sha256 come from next_call: pages take all three, "
            "exact detail_path/field chunks take cursor and result_sha256, and single "
            "records (summary, object, finding_ref, validator_result_key) take none.",
            "Values too long to show are withheld with total_chars, value_sha256 and a "
            "read call that returns them exactly; nothing is shortened silently.",
            "Object views use only domain-pack YAML supervisor_manifest fields.",
            "Evidence text is read with action=\"evidence\" and object_ref.",
            "Use details with object_ref to browse saved generic/custom attributes.",
            "Raw UUIDs and transient lookup refs are rejected as result_ref values.",
            "Export and curation prep are separate explicit supervisor actions.",
        ],
        "examples": [
            'inspect_results(action="summary")',
            'inspect_results(action="objects", result_ref="extraction-result:<uuid>", validation_state="open")',
            'inspect_results(action="objects", result_ref="extraction-result:<uuid>", query="<terms>", fields=["<field>"])',
            'inspect_results(action="object", result_ref="extraction-result:<uuid>", object_ref="<object_ref>")',
            'inspect_results(action="validation", result_ref="extraction-result:<uuid>", validation_state="open", severity="error")',
            'inspect_results(action="validation", result_ref="extraction-result:<uuid>", finding_ref="<finding_ref>")',
            'inspect_results(action="validation", result_ref="extraction-result:<uuid>", finding_ref="<finding_ref>", detail_path="details")',
            'inspect_results(action="validator_results", result_ref="extraction-result:<uuid>")',
            'inspect_results(action="validator_results", result_ref="extraction-result:<uuid>", validator_result_key="<validator_result_key>", detail_path="resolved_values")',
            'inspect_results(action="evidence", result_ref="extraction-result:<uuid>", object_ref="<object_ref>")',
            'inspect_results(action="search", target="current_document", query="<terms>")',
        ],
    }


# ---------------------------------------------------------------------------
# Paging, exact reads and descriptors
# ---------------------------------------------------------------------------


def _budget_failure(exc: ToolResultBudgetError, *, action: str) -> str:
    failure = budget_failure(
        tool_name=_TOOL_NAME,
        measured=exc.measured,
        limit=exc.limit,
        field=action,
    )
    report_budget_failure_result(failure, tool_name=_TOOL_NAME, component=_COMPONENT)
    return json.dumps(failure, ensure_ascii=True, default=str)


def _check_sha(expected: str | None, actual: str) -> None:
    if expected and expected != actual:
        raise _RequestError(
            STALE_RESULT_CURSOR,
            "This result or view changed since the earlier page was read. Repeat the "
            "call without cursor and result_sha256 to start again.",
            result_sha256=actual,
        )


def _parse_cursor(cursor: Any, *, total: int, unit: str = "items") -> int:
    if cursor is None or (isinstance(cursor, str) and not cursor.strip()):
        return 0
    text = str(cursor).strip()
    # ASCII digits only: isdigit() admits superscripts that int() rejects, and
    # int() rejects digit strings longer than Python's conversion limit.
    try:
        if isinstance(cursor, bool) or not (text.isascii() and text.isdecimal()):
            raise ValueError(text)
        value = int(text)
    except ValueError:
        raise _RequestError(
            INVALID_RESULT_CURSOR,
            "cursor must be the next_cursor (or next_call cursor) from a previous response.",
            cursor=_echo(cursor),
        ) from None
    if value > total:
        raise _RequestError(
            INVALID_RESULT_CURSOR,
            f"cursor {value} is past the end of the {total} available {unit}; the "
            "result may have changed. Restart without a cursor.",
        )
    return value


def _page(
    items: Sequence[Any],
    *,
    view: Callable[[Any, int], Any],
    rows_key: str,
    head: Mapping[str, Any],
    call: Mapping[str, Any],
    sha: str,
    expected_sha: str | None,
    cursor: Any,
    limit: Any,
    default_limit: int,
    max_limit: int,
    message: str,
    oversized: Callable[[Any, int], Any] | None = None,
    overrides: Callable[[list[Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """One forward page of ``items`` fitted to the byte budget.

    Only the requested window is rendered. The page ends at the row limit or
    where the next row would overflow; a first row too large alone is replaced
    by ``oversized(row_view, index)`` (identity plus a read call).
    """

    _check_sha(expected_sha, sha)
    try:
        effective, limit_info = clamp_page_limit(
            limit, default=default_limit, maximum=max_limit
        )
    except ValueError as exc:
        raise _RequestError("invalid_request", str(exc)) from exc
    total = len(items)
    start = _parse_cursor(cursor, total=total)
    window = [view(item, start + offset) for offset, item in enumerate(items[start:start + effective])]
    next_call_base = dict(call)
    if limit not in (None, 0):
        next_call_base["limit"] = effective

    def render(page: list[Any], returned: int) -> dict[str, Any]:
        next_offset = start + returned
        more = next_offset < total
        ended_by = "end" if not more else ("limit" if returned >= effective else "size_budget")
        withheld = sum(1 for row in page if isinstance(row, Mapping) and row.get("withheld"))
        note = ""
        if ended_by == "size_budget":
            note += " The page ended at the tool result size budget; continue with next_call."
        if withheld:
            note += f" {withheld} row(s) were too large to show and are withheld; read each with its read call."
        body = {
            "status": "ok",
            "message": message + note,
            **head,
            rows_key: page,
            "total_count": total,
            "offset": start,
            "returned_count": returned,
            "truncated": more,
            "next_cursor": str(next_offset) if more else None,
            "page_ended_by": ended_by,
            "limit": limit_info,
            "result_sha256": sha,
            "next_call": (
                {**next_call_base, "cursor": str(next_offset), "result_sha256": sha}
                if more
                else None
            ),
        }
        if overrides is not None:
            extra = dict(overrides(page))
            if "message" in extra:
                # An override replaces the lead message, never the continuation
                # and withheld-row notes.
                extra["message"] += note
            body.update(extra)
        return body

    response, _ = fit_page(
        window,
        start=0,
        limit=len(window),
        render=render,
        budget=tool_result_budget(),
        oversized=(
            (lambda row, offset: oversized(row, start + offset)) if oversized else None
        ),
    )
    return response


def _exact_value(
    value: Any,
    *,
    head: Mapping[str, Any],
    call: Mapping[str, Any],
    sha: str,
    expected_sha: str | None,
    cursor: Any,
    message: str,
) -> dict[str, Any]:
    """Return a value whole when it fits, otherwise exact contiguous chunks.

    Strings are chunked as text and anything else as canonical JSON; joining
    every chunk's content reproduces the value whose ``value_sha256`` is shown.
    """

    _check_sha(expected_sha, sha)
    is_text = isinstance(value, str)
    text = value if is_text else canonical_json(value)
    start = _parse_cursor(cursor, total=len(text), unit="characters")
    digest = content_sha256(value)
    budget = tool_result_budget()
    if start == 0:
        whole = {
            "status": "ok",
            "message": message,
            **head,
            "value": value,
            "complete": True,
            "total_chars": len(text),
            "value_sha256": digest,
            "result_sha256": sha,
        }
        if serialized_size(whole) <= budget:
            return whole

    def render(content: str, end: int) -> dict[str, Any]:
        complete = end >= len(text)
        return {
            "status": "ok",
            "message": message + (
                "" if complete else " This is an exact chunk; continue with next_call."
            ),
            **head,
            "detail": {
                "encoding": "text" if is_text else "canonical_json",
                "content": content,
                "returned_range": {"start": start, "end": end},
                "total_chars": len(text),
                "value_sha256": digest,
                "complete": complete,
            },
            "complete": complete,
            "result_sha256": sha,
            "next_call": (
                None if complete else {**call, "cursor": str(end), "result_sha256": sha}
            ),
        }

    return fit_text_window(text, cursor=start, render=render, budget=budget)


def _fit_record(
    record: Mapping[str, Any],
    *,
    render: Callable[[dict[str, Any]], dict[str, Any]],
    read_for: Callable[[str], dict[str, Any]],
    protected: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Render ``record`` whole, withholding its largest values only as needed."""

    budget = tool_result_budget()
    shown = dict(record)
    candidates = sorted(
        (key for key in record if key not in protected),
        key=lambda key: serialized_size(record[key]),
        reverse=True,
    )
    for key in candidates:
        if serialized_size(render(shown)) <= budget:
            break
        if _is_descriptor(record[key]):
            continue
        shown[key] = _descriptor(record[key], read=read_for(key))
    response = render(shown)
    measured = serialized_size(response)
    if measured > budget:
        raise ToolResultBudgetError(measured=measured, limit=budget)
    return response


def _is_descriptor(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get("withheld") is True


def _descriptor(value: Any, *, read: Mapping[str, Any]) -> dict[str, Any]:
    """Describe a withheld value and name the call that reads it exactly."""

    text = value if isinstance(value, str) else canonical_json(value)
    descriptor: dict[str, Any] = {
        "withheld": True,
        "type": "text" if isinstance(value, str) else type(value).__name__,
        "total_chars": len(text),
        "value_sha256": content_sha256(value),
    }
    if isinstance(value, str):
        # An exact prefix, marked partial by withheld/total_chars.
        descriptor["preview"] = value[:_TEXT_PREVIEW_LIMIT]
    elif isinstance(value, Mapping):
        descriptor["key_count"] = len(value)
        keys = sorted(str(key) for key in value)
        if len(canonical_json(keys)) <= _FIELD_TEXT_LIMIT:
            descriptor["keys"] = keys
    elif isinstance(value, list):
        descriptor["item_count"] = len(value)
    descriptor["read"] = dict(read)
    return descriptor


def _with_resolution_states(value: Any) -> Any:
    """Every resolvable value carries its state, so a mention never reads as the item.

    A value stored before ALL-1283 (no contract state) is marked unresolved
    with lookup outcome legacy_unverified; nothing here can verify it.
    """

    if isinstance(value, list):
        return [_with_resolution_states(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    annotated = {key: _with_resolution_states(item) for key, item in value.items()}
    if holds_resolution(value) and not has_resolution_state(value):
        annotated[RESOLUTION_STATE_KEY] = UNRESOLVED
        annotated[LOOKUP_OUTCOME_KEY] = OUTCOME_LEGACY_UNVERIFIED
        annotated[VALIDATOR_EXPLANATION_KEY] = LEGACY_EXPLANATION
    return annotated


def _value_view(value: Any, *, read: Mapping[str, Any], payload_value: bool = False) -> Any:
    """Inline a value up to the per-field allowance; otherwise describe it.

    ``payload_value`` marks a curation object's payload value, whose
    resolvable values are shown with their state.
    """

    if payload_value:
        value = _with_resolution_states(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _FIELD_TEXT_LIMIT else _descriptor(value, read=read)
    if len(canonical_json(value)) <= _FIELD_TEXT_LIMIT:
        return value
    return _descriptor(value, read=read)


def _resolve_detail(root: Any, detail_path: str) -> Any:
    try:
        return resolve_path(root, detail_path)
    except ValueError as exc:
        raise _RequestError(
            "invalid_detail_path",
            f"{exc}. Use a detail_path from a read call.",
            detail_path=_echo(detail_path),
        ) from exc


def _choice(value: Any, allowed: Sequence[str], name: str) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = text.lower()
    if normalized not in allowed:
        raise _RequestError(
            "invalid_request",
            f"{name} must be one of: {', '.join(allowed)}.",
            **{name: _echo(text)},
        )
    return normalized


def _snippet(text: str) -> tuple[str, bool]:
    if len(text) <= _SNIPPET_LIMIT:
        return text, True
    return text[:_SNIPPET_LIMIT], False


def _value_text(value: Any) -> str:
    return value if isinstance(value, str) else canonical_json(value)


# ---------------------------------------------------------------------------
# Result listings
# ---------------------------------------------------------------------------


def _list_response(
    records: Sequence[Any],
    *,
    carry: Mapping[str, Any],
    cursor: Any,
    limit: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    return _page(
        records,
        view=lambda record, _index: _record_summary(record),
        rows_key="results",
        head={"action": "list", "target": carry.get("target", "latest")},
        call={**carry, "action": "list"},
        sha=content_sha256({"list": [_record_id(record) for record in records], "view": dict(carry)}),
        expected_sha=expected_sha,
        cursor=cursor,
        limit=limit,
        default_limit=_RESULT_LIST_PAGE_SIZE,
        max_limit=_MAX_LIST_LIMIT,
        message=(
            f"{len(records)} authorized persisted extraction result(s) matched. Use "
            "action=\"summary\" with a result_ref for its counts, then filter objects."
        ),
    )


def _search_response(
    records: Sequence[Any],
    *,
    target: str,
    carry: Mapping[str, Any],
    query: str | None,
    cursor: Any,
    limit: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    unsupported_count = 0
    for record in records:
        try:
            envelope = _canonical_envelope_for_record(record)
        except (TypeError, ValueError, ValidationError):
            unsupported_count += 1
            continue
        matches.extend(
            _search_matches_for_record(
                record,
                envelope=envelope,
                query=query,
                read_carry=_result_carry(record, target=target, flow_run_id=carry.get("flow_run_id")),
            )
        )
    identities = [
        [match["result_ref"], match["object_ref"], match["match_type"],
         match.get("field_path"), match.get("evidence_record_id")]
        for match in matches
    ]
    return _page(
        matches,
        view=lambda match, _index: match,
        rows_key="matches",
        head={
            "action": "search",
            "target": target,
            "query": query,
            "unsupported_result_count": unsupported_count,
        },
        call={**carry, "action": "search", **({"query": query} if query else {})},
        sha=content_sha256({"search": identities, "view": {**carry, "query": query}}),
        expected_sha=expected_sha,
        cursor=cursor,
        limit=limit,
        default_limit=_RESULT_LIST_PAGE_SIZE,
        max_limit=_MAX_LIST_LIMIT,
        message=(
            f"{len(matches)} search match(es). Snippets marked snippet_complete=false "
            "are partial; use each match's read call for the complete value. To export a "
            "selected result, pass its source_ref=\"<result_ref>\" to the formatter."
        ),
    )


# ---------------------------------------------------------------------------
# One result: summary and objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ObjectFilters:
    object_type: str | None
    status: str | None
    validation_state: str | None
    severity: str | None
    query: str | None
    field_path: str | None
    fields: tuple[str, ...] | None

    @classmethod
    def build(cls, *, object_type: Any, status: Any, validation_state: Any,
              severity: Any, query: str | None, field_path: str | None,
              fields: Sequence[Any] | None) -> "_ObjectFilters":
        state = _choice(validation_state, _OBJECT_VALIDATION_STATES, "validation_state")
        chosen_severity = _choice(severity, _SEVERITIES, "severity")
        if chosen_severity and state == "none":
            raise _RequestError(
                "invalid_request",
                "severity cannot be combined with validation_state=\"none\".",
            )
        selected = None
        if fields:
            selected = tuple(dict.fromkeys(str(item).strip() for item in fields if str(item).strip()))
        return cls(
            object_type=_optional_text(object_type),
            status=_choice(status, _OBJECT_STATUSES, "status"),
            validation_state=state,
            severity=chosen_severity,
            query=query,
            field_path=field_path,
            fields=selected or None,
        )

    def as_args(self) -> dict[str, Any]:
        args = {
            "object_type": self.object_type,
            "status": self.status,
            "validation_state": self.validation_state,
            "severity": self.severity,
            "query": self.query,
            "field_path": self.field_path,
            "fields": list(self.fields) if self.fields else None,
        }
        return {key: value for key, value in args.items() if value is not None}


def _visible_objects(result: _Result) -> tuple[DomainPackMetadata, list[CuratableObjectEnvelope]]:
    try:
        metadata = _domain_pack_metadata(result.envelope.domain_pack_id)
    except ValueError as exc:
        raise _RequestError(
            "manifest_unavailable",
            str(exc),
            result_ref=result.result_ref,
            extraction_result_id=result.extraction_result_id,
        ) from exc
    return metadata, supervisor_manifest_objects(result.envelope, metadata)


def _policies(
    metadata: DomainPackMetadata,
    object_types: Sequence[str],
) -> dict[str, SupervisorManifestPolicy]:
    try:
        return {
            object_type: supervisor_manifest_policy_for_object(metadata, object_type)
            for object_type in dict.fromkeys(object_types)
        }
    except ValueError as exc:
        raise _RequestError("manifest_unavailable", str(exc)) from exc


def _findings_by_object(envelope: DomainEnvelope) -> dict[tuple[str, str], list[ValidationFinding]]:
    grouped: dict[tuple[str, str], list[ValidationFinding]] = {}
    for finding in envelope.validation_findings:
        key = _finding_object_key(finding)
        if key is not None:
            grouped.setdefault(key, []).append(finding)
    return grouped


def _object_findings(
    obj: CuratableObjectEnvelope,
    grouped: Mapping[tuple[str, str], list[ValidationFinding]],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for key in obj.ref_keys():
        findings.extend(grouped.get(key, []))
    return findings


def _object_validation_state(findings: Sequence[ValidationFinding]) -> str:
    if not findings:
        return "none"
    if any(finding.status is ValidationFindingStatus.OPEN for finding in findings):
        return "open"
    return "resolved"


def _object_validation_counts(findings: Sequence[ValidationFinding]) -> dict[str, int]:
    warning_count, error_count = _validation_severity_counts(findings)
    return {
        "total": len(findings),
        "open": sum(1 for f in findings if f.status is ValidationFindingStatus.OPEN),
        "error_count": error_count,
        "warning_count": warning_count,
    }


def _summary_response(result: _Result) -> dict[str, Any]:
    metadata, visible = _visible_objects(result)
    policies = _policies(metadata, [obj.object_type for obj in visible])
    grouped = _findings_by_object(result.envelope)
    by_type: dict[str, dict[str, Any]] = {}
    by_status: dict[str, int] = {}
    by_state = {"open": 0, "resolved": 0, "none": 0}
    evidence_objects = 0
    evidence_refs = 0
    for obj in visible:
        findings = _object_findings(obj, grouped)
        state = _object_validation_state(findings)
        entry = by_type.setdefault(
            obj.object_type, {"count": 0, "by_status": {}, "with_open_findings": 0}
        )
        entry["count"] += 1
        entry["by_status"][obj.status.value] = entry["by_status"].get(obj.status.value, 0) + 1
        entry["with_open_findings"] += state == "open"
        by_status[obj.status.value] = by_status.get(obj.status.value, 0) + 1
        by_state[state] += 1
        evidence_objects += bool(obj.evidence_record_ids)
        evidence_refs += len(obj.evidence_record_ids)
    findings = result.envelope.validation_findings
    finding_status: dict[str, int] = {}
    finding_severity: dict[str, int] = {}
    open_severity: dict[str, int] = {}
    for finding in findings:
        finding_status[finding.status.value] = finding_status.get(finding.status.value, 0) + 1
        finding_severity[finding.severity.value] = finding_severity.get(finding.severity.value, 0) + 1
        if finding.status is ValidationFindingStatus.OPEN:
            open_severity[finding.severity.value] = open_severity.get(finding.severity.value, 0) + 1
    decisions = validator_result_entries(findings)
    decision_status: dict[str, int] = {}
    for _key, entry in decisions:
        decision_status[str(entry["status"])] = decision_status.get(str(entry["status"]), 0) + 1
    inventory = {
        "result_status": "non_empty_extraction_ready" if visible else "empty_extraction",
        "object_count": len(visible),
        "other_object_count": len(result.envelope.extracted_objects) - len(visible),
        "objects_by_type": dict(sorted(by_type.items())),
        "objects_by_status": dict(sorted(by_status.items())),
        "objects_by_validation_state": by_state,
        "findings": {
            "total": len(findings),
            "open": finding_status.get(ValidationFindingStatus.OPEN.value, 0),
            "by_status": dict(sorted(finding_status.items())),
            "by_severity": dict(sorted(finding_severity.items())),
            "open_by_severity": dict(sorted(open_severity.items())),
        },
        "validator_results": {
            "total": len(decisions),
            "by_status": dict(sorted(decision_status.items())),
            "with_open_finding": sum(1 for _key, entry in decisions if entry.get("open_finding")),
            "writeback_rejected": sum(1 for _key, entry in decisions if entry.get("writeback_rejected")),
        },
        "evidence": {
            "objects_with_evidence": evidence_objects,
            "evidence_reference_count": evidence_refs,
        },
    }
    return {
        "status": "ok",
        "message": (
            "Counts for this result. Page objects, findings or validator results with "
            "filters, and read one object, field or finding exactly."
        ),
        **result.head("summary"),
        "summary": _record_summary(result.record),
        "domain_pack_id": result.envelope.domain_pack_id,
        "inventory": inventory,
        "filterable_fields": {
            object_type: list(policy.field_paths)
            for object_type, policy in sorted(policies.items())
        },
        "result_sha256": result.sha(action="summary"),
        "next_calls": [
            result.call("objects"),
            result.call("objects", validation_state="open"),
            result.call("validation", validation_state="open"),
            result.call("validator_results"),
        ],
    }


def _object_matches(
    obj: CuratableObjectEnvelope,
    *,
    policy: SupervisorManifestPolicy,
    findings: Sequence[ValidationFinding],
    filters: _ObjectFilters,
    query_terms: Sequence[str],
) -> bool:
    if filters.object_type and obj.object_type != filters.object_type:
        return False
    if filters.status and obj.status.value != filters.status:
        return False
    state = filters.validation_state or ("any" if filters.severity else None)
    if state == "none" and findings:
        return False
    if state == "resolved" and _object_validation_state(findings) != "resolved":
        return False
    if state in {"open", "resolved", "any"}:
        # At least one finding in the requested state (and severity, if given).
        relevant = [
            finding
            for finding in findings
            if (state != "open" or finding.status is ValidationFindingStatus.OPEN)
            and (not filters.severity or finding.severity.value == filters.severity)
        ]
        if not relevant:
            return False
    if filters.field_path is not None:
        if filters.field_path not in policy.field_paths:
            return False
        value = _payload_path_value(obj.payload, filters.field_path)
        if value is None or value == "":
            return False
        if query_terms and not _query_matches(_value_text(value), query_terms):
            return False
        return True
    if query_terms:
        haystack = " ".join(
            [_canonical_object_ref(obj)]
            + [
                _value_text(value)
                for path in policy.field_paths
                if (value := _payload_path_value(obj.payload, path)) is not None
            ]
        )
        return _query_matches(haystack, query_terms)
    return True


def _validate_field_selection(
    policies: Mapping[str, SupervisorManifestPolicy],
    filters: _ObjectFilters,
) -> None:
    scoped = (
        {filters.object_type: policies[filters.object_type]}
        if filters.object_type in policies
        else dict(policies)
    )
    visible = {path for policy in scoped.values() for path in policy.field_paths}
    requested = list(filters.fields or ())
    if filters.field_path is not None:
        requested.append(filters.field_path)
    if not scoped:
        if requested:
            raise _RequestError(
                "field_not_supervisor_visible",
                "This result has no supervisor-visible objects, so it has no fields to "
                "select or filter.",
                **_echo_list(requested, "requested"),
            )
        return
    hidden = [path for path in requested if path not in visible]
    if hidden:
        raise _RequestError(
            "field_not_supervisor_visible",
            "fields and field_path must be domain-pack YAML supervisor_manifest fields "
            "of the objects being listed.",
            **_echo_list(hidden, "requested"),
            visible_field_paths={
                object_type: list(policy.field_paths)
                for object_type, policy in sorted(scoped.items())
            },
        )


def _object_row(
    result: _Result,
    obj: CuratableObjectEnvelope,
    *,
    metadata: DomainPackMetadata,
    policy: SupervisorManifestPolicy,
    findings: Sequence[ValidationFinding],
    selected: Sequence[str],
) -> dict[str, Any]:
    object_ref = _canonical_object_ref(obj)

    def read(path: str) -> dict[str, Any]:
        return result.call("field", object_ref=object_ref, field_path=path)

    row: dict[str, Any] = {
        "object_ref": object_ref,
        "object_type": obj.object_type,
        "status": obj.status.value,
    }
    # One declared field each; an unresolved value reads as its paper wording (ALL-1283).
    for key, field in (
        ("display_label", policy.primary_label_field),
        ("secondary_label", policy.secondary_label_field),
    ):
        if field is None:
            continue
        paper_wording = unresolved_header_text(
            obj.payload,
            field.path,
            object_metadata=obj.metadata,
            resolvable_fields=declared_resolvable_fields(metadata, obj.object_type),
        )
        if paper_wording is not None:
            row[key] = paper_wording
            continue
        value = _payload_path_value(obj.payload, field.path)
        if value not in (None, ""):
            row[key] = _value_view(value, read=read(field.path), payload_value=True)
    row["fields"] = {
        path: _value_view(value, read=read(path), payload_value=True)
        for path in selected
        if path in policy.field_paths
        and (value := _payload_path_value(obj.payload, path)) is not None
    }
    row["validation"] = _object_validation_counts(findings)
    row["evidence_count"] = len(obj.evidence_record_ids)
    return row


def _objects_response(
    result: _Result,
    *,
    filters: _ObjectFilters,
    cursor: Any,
    limit: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    metadata, visible = _visible_objects(result)
    known_types = {definition.object_type for definition in metadata.object_definitions}
    if filters.object_type and filters.object_type not in known_types:
        raise _RequestError(
            "invalid_request",
            "object_type is not defined by this result's domain pack.",
            object_type=_echo(filters.object_type),
            object_types=sorted({obj.object_type for obj in visible}),
        )
    policies = _policies(metadata, [obj.object_type for obj in visible])
    _validate_field_selection(policies, filters)
    grouped = _findings_by_object(result.envelope)
    query_terms = _query_terms(filters.query)
    matched: list[tuple[CuratableObjectEnvelope, list[ValidationFinding]]] = []
    for obj in visible:
        findings = _object_findings(obj, grouped)
        if _object_matches(
            obj,
            policy=policies[obj.object_type],
            findings=findings,
            filters=filters,
            query_terms=query_terms,
        ):
            matched.append((obj, findings))

    def selected_paths(policy: SupervisorManifestPolicy) -> Sequence[str]:
        if filters.fields:
            return filters.fields
        return [field.path for field in policy.summary_fields]

    field_labels: dict[str, str] = {}
    for object_type in dict.fromkeys(obj.object_type for obj, _findings in matched):
        policy = policies[object_type]
        labels = {
            field.path: field.label
            for field in (
                *((policy.primary_label_field,) if policy.primary_label_field else ()),
                *((policy.secondary_label_field,) if policy.secondary_label_field else ()),
                *policy.summary_fields,
            )
        }
        for path in selected_paths(policy):
            if path in labels:
                field_labels.setdefault(path, labels[path])

    def view(item: tuple[CuratableObjectEnvelope, list[ValidationFinding]], _index: int) -> dict[str, Any]:
        obj, findings = item
        policy = policies[obj.object_type]
        return _object_row(
            result, obj, metadata=metadata, policy=policy, findings=findings, selected=selected_paths(policy)
        )

    def oversized(row: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            "object_ref": row["object_ref"],
            "object_type": row["object_type"],
            "status": row["status"],
            "withheld": True,
            "total_chars": len(canonical_json(row)),
            "read": result.call("object", object_ref=row["object_ref"]),
        }

    args = filters.as_args()
    return _page(
        matched,
        view=view,
        rows_key="objects",
        head={
            **result.head("objects"),
            "domain_pack_id": result.envelope.domain_pack_id,
            "object_count": len(visible),
            "filters": args,
            "field_labels": field_labels,
        },
        call=result.call("objects", **args),
        sha=result.sha(action="objects", **args),
        expected_sha=expected_sha,
        cursor=cursor,
        limit=limit,
        default_limit=get_inspect_results_object_page_size(),
        max_limit=get_inspect_results_object_max_page_size(),
        message=(
            f"{len(matched)} of {len(visible)} supervisor-visible object(s) matched. "
            "Values shown withheld are read exactly with their read call."
        ),
        oversized=oversized,
    )


def _object_response(result: _Result, *, object_ref: str | None) -> dict[str, Any]:
    if not object_ref:
        raise _RequestError(
            "invalid_request",
            "object_ref is required for action=\"object\".",
            action="object",
            result_ref=result.result_ref,
        )
    metadata, visible = _visible_objects(result)
    obj = next((item for item in visible if _canonical_object_ref(item) == object_ref), None)
    if obj is None:
        raise _RequestError(
            "object_not_found",
            "No supervisor-visible object matched object_ref.",
            action="object",
            result_ref=result.result_ref,
            object_ref=_echo(object_ref),
        )
    policy = _policies(metadata, [obj.object_type])[obj.object_type]
    findings = _object_findings(obj, _findings_by_object(result.envelope))
    labels = {field.path: field.label for field in (
        *((policy.primary_label_field,) if policy.primary_label_field else ()),
        *((policy.secondary_label_field,) if policy.secondary_label_field else ()),
        *policy.summary_fields,
    )}

    def read(path: str) -> dict[str, Any]:
        return result.call("field", object_ref=object_ref, field_path=path)

    values = {
        path: _value_view(value, read=read(path), payload_value=True)
        for path in policy.field_paths
        if (value := _payload_path_value(obj.payload, path)) is not None
    }

    def render(shown: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "message": (
                "Supervisor-visible extraction object is ready. Values shown withheld "
                "are read exactly with their read call."
            ),
            **result.head("object"),
            "object_ref": object_ref,
            "object": {
                "object_ref": object_ref,
                "object_type": obj.object_type,
                "object_role": obj.object_role,
                "status": obj.status.value,
                "fields": shown,
                "field_labels": {path: labels[path] for path in shown if path in labels},
                "validation": _object_validation_counts(findings),
                "evidence_count": len(obj.evidence_record_ids),
            },
            "result_sha256": result.sha(action="object", object_ref=object_ref),
            "next_calls": [
                result.call("validation", object_ref=object_ref),
                result.call("evidence", object_ref=object_ref),
            ],
        }

    return _fit_record(values, render=render, read_for=read)


def _field_response(
    result: _Result,
    *,
    object_ref: str | None,
    field_path: str | None,
    cursor: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    if not object_ref:
        raise _RequestError(
            "invalid_request",
            "object_ref is required for action=\"field\".",
            action="field",
            result_ref=result.result_ref,
        )
    if not field_path:
        raise _RequestError(
            "invalid_request",
            "field_path is required for action=\"field\".",
            action="field",
            result_ref=result.result_ref,
            object_ref=_echo(object_ref),
        )
    if _is_evidence_path(field_path):
        raise _RequestError(
            "evidence_path_requires_evidence_action",
            "Evidence text paths are only available through action=\"evidence\".",
            action="field",
            result_ref=result.result_ref,
            object_ref=_echo(object_ref),
            field_path=_echo(field_path),
        )
    try:
        obj = _resolve_object(result.envelope, object_ref)
        visible_paths = _supervisor_visible_field_paths(
            result.envelope.domain_pack_id, obj.object_type
        )
    except ValueError as exc:
        raise _RequestError(
            "object_not_found",
            "No object matched object_ref.",
            action="field",
            result_ref=result.result_ref,
            object_ref=_echo(object_ref),
        ) from exc
    if field_path not in visible_paths:
        raise _RequestError(
            "field_not_supervisor_visible",
            "field_path must be one of this object's domain-pack YAML supervisor_manifest fields.",
            action="field",
            result_ref=result.result_ref,
            object_ref=_echo(object_ref),
            field_path=_echo(field_path),
            visible_field_paths=sorted(visible_paths),
        )
    try:
        value = _payload_path_value(obj.payload, field_path)
    except ValueError as exc:
        raise _RequestError(
            "invalid_field_path",
            str(exc),
            action="field",
            result_ref=result.result_ref,
            field_path=_echo(field_path),
        ) from exc
    if value is None:
        raise _RequestError(
            "field_not_found",
            "field_path did not resolve on this object payload.",
            action="field",
            result_ref=result.result_ref,
            object_ref=_echo(object_ref),
            field_path=_echo(field_path),
        )
    return _exact_value(
        value,
        head={**result.head("field"), "object_ref": object_ref, "field_path": field_path},
        call=result.call("field", object_ref=object_ref, field_path=field_path),
        sha=result.sha(action="field", object_ref=object_ref, field_path=field_path),
        expected_sha=expected_sha,
        cursor=cursor,
        message="Supervisor-visible field value is ready.",
    )


def _details_response(
    result: _Result,
    *,
    object_ref: str | None,
    field_path: str | None,
    cursor: Any,
    limit: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    """Browse one level of saved custom attributes with explicit continuation."""

    path = field_path or "attributes"
    try:
        obj = _resolve_object(result.envelope, object_ref or "")
        parts = parse_field_path(path)
    except ValueError as exc:
        raise _RequestError(
            "invalid_request",
            "Choose a saved object and a valid detail path.",
            action="details",
        ) from exc
    # Custom attributes are an explicit data surface, not an escape from other
    # domain packs' field visibility policies or a route to internal metadata.
    if result.envelope.domain_pack_id != "generic" or not parts or parts[0] != "attributes":
        raise _RequestError(
            "field_not_supervisor_visible",
            "Details browses generic/custom attributes only.",
            action="details",
        )
    value: Any = obj.payload
    for part in parts:
        if isinstance(part, str) and isinstance(value, Mapping) and part in value:
            value = value[part]
        elif isinstance(part, int) and isinstance(value, list) and 0 <= part < len(value):
            value = value[part]
        else:
            raise _RequestError(
                "field_not_found",
                "This saved detail path does not exist.",
                action="details",
                field_path=_echo(path),
            )
    head = {**result.head("details"), "object_ref": object_ref, "field_path": path}
    call = result.call("details", object_ref=object_ref, field_path=path)
    sha = result.sha(action="details", object_ref=object_ref, field_path=path)
    if not isinstance(value, (Mapping, list)):
        # Text can be long: exact chunks keep the saved value retrievable.
        return _exact_value(
            value,
            head=head,
            call=call,
            sha=sha,
            expected_sha=expected_sha,
            cursor=cursor,
            message="Saved detail value is ready.",
        )
    if holds_resolution(value):
        # A resolvable value states whether it is resolved, so its mention is
        # never read as the item (ALL-1283).
        state = _with_resolution_states(value)
        head.update(
            resolution_state=state[RESOLUTION_STATE_KEY],
            lookup_outcome=state[LOOKUP_OUTCOME_KEY],
        )
    children = list(value.items()) if isinstance(value, Mapping) else list(enumerate(value))

    def view(child: tuple[Any, Any], _index: int) -> dict[str, Any]:
        key, child_value = child
        child_path = f"{path}[{key}]" if isinstance(key, int) else f"{path}.{key}"
        read = result.call("details", object_ref=object_ref, field_path=child_path)
        entry: dict[str, Any] = {"name": str(key), "field_path": child_path}
        if isinstance(child_value, Mapping):
            entry.update(kind="object", key_count=len(child_value), read=read)
        elif isinstance(child_value, list):
            entry.update(kind="list", item_count=len(child_value), read=read)
        else:
            entry.update(kind="value", value=_value_view(child_value, read=read))
        return entry

    return _page(
        children,
        view=view,
        rows_key="entries",
        head=head,
        call=call,
        sha=sha,
        expected_sha=expected_sha,
        cursor=cursor,
        limit=limit,
        default_limit=_RESULT_LIST_PAGE_SIZE,
        max_limit=_MAX_LIST_LIMIT,
        message="Saved details are ready. Open a child path for its complete value or parts.",
    )


# ---------------------------------------------------------------------------
# One result: evidence, findings and validator results
# ---------------------------------------------------------------------------


def _evidence_response(
    result: _Result,
    *,
    object_ref: str | None,
    detail_path: str | None,
    cursor: Any,
    limit: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    if not object_ref:
        if detail_path:
            raise _RequestError(
                "invalid_request",
                "detail_path requires object_ref for action=\"evidence\".",
                action="evidence",
            )
        return _evidence_inventory_response(
            result, cursor=cursor, limit=limit, expected_sha=expected_sha
        )
    try:
        obj = _resolve_object(result.envelope, object_ref)
    except ValueError as exc:
        raise _RequestError(
            "object_not_found",
            "No object matched object_ref.",
            action="evidence",
            result_ref=result.result_ref,
            object_ref=_echo(object_ref),
        ) from exc
    evidence = [
        _compact_evidence_record(item)
        for item in _object_evidence_records(result.envelope, obj)
    ]
    head = {**result.head("evidence"), "object_ref": object_ref}
    if detail_path:
        return _exact_value(
            _resolve_detail(evidence, detail_path),
            head={**head, "detail_path": detail_path},
            call=result.call("evidence", object_ref=object_ref, detail_path=detail_path),
            sha=result.sha(action="evidence", object_ref=object_ref, detail_path=detail_path),
            expected_sha=expected_sha,
            cursor=cursor,
            message="Exact evidence value is ready.",
        )

    def view(record: dict[str, Any], index: int) -> dict[str, Any]:
        return {"index": index, **record}

    def oversized(row: dict[str, Any], index: int) -> dict[str, Any]:
        record = evidence[index]
        identity = ("evidence_record_id", "id")
        # Every withheld key gets an exact read, largest first.
        withheld_keys = sorted(
            (key for key in record if key not in identity),
            key=lambda key: len(_value_text(record[key])),
            reverse=True,
        )
        return {
            "index": index,
            **{key: record[key] for key in identity if key in record},
            "withheld": True,
            "total_chars": len(canonical_json(record)),
            "reads": [
                result.call("evidence", object_ref=object_ref, detail_path=f"{index}.{key}")
                for key in withheld_keys
            ] or [result.call("evidence", object_ref=object_ref, detail_path=str(index))],
        }

    def overrides(page: list[Any]) -> dict[str, Any]:
        missing = [
            row for row in page
            if isinstance(row, Mapping) and not row.get("withheld")
            and not _record_has_evidence_text(row)
        ]
        if not missing:
            return {}
        return {
            "status": "error",
            "error_code": "evidence_unavailable",
            "missing_evidence_count": len(missing),
            "message": (
                "Some saved evidence could not be loaded. Available evidence is included; "
                "do not rerun extraction to recover it or treat reference IDs as quotes."
            ),
        }

    response = _page(
        evidence,
        view=view,
        rows_key="evidence",
        head={**head, "evidence_count": len(evidence)},
        call=result.call("evidence", object_ref=object_ref),
        sha=result.sha(action="evidence", object_ref=object_ref),
        expected_sha=expected_sha,
        cursor=cursor,
        limit=limit,
        default_limit=_EVIDENCE_PAGE_SIZE,
        max_limit=_MAX_LIST_LIMIT,
        message="Evidence text is ready.",
        oversized=oversized,
        overrides=overrides,
    )
    if response.get("error_code") == "evidence_unavailable":
        report_runtime_exception(
            RuntimeError("Saved extraction evidence references could not be resolved to text"),
            component="extraction_result_inspection",
            operation="evidence_resolution_failed",
            tags={"tool_name": _TOOL_NAME},
            context={
                "extraction_result_id": result.extraction_result_id,
                "requested_evidence_count": response["returned_count"],
                "missing_evidence_count": response["missing_evidence_count"],
            },
        )
    return response


def _evidence_inventory_response(
    result: _Result,
    *,
    cursor: Any,
    limit: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    _metadata, visible = _visible_objects(result)
    return _page(
        visible,
        view=lambda obj, _index: {
            "object_ref": _canonical_object_ref(obj),
            "object_type": obj.object_type,
            "status": obj.status.value,
            "evidence_count": len(obj.evidence_record_ids),
        },
        rows_key="evidence_inventory",
        head={**result.head("evidence"), "object_count": len(visible)},
        call=result.call("evidence"),
        sha=result.sha(action="evidence"),
        expected_sha=expected_sha,
        cursor=cursor,
        limit=limit,
        default_limit=get_inspect_results_object_page_size(),
        max_limit=get_inspect_results_object_max_page_size(),
        message="Evidence inventory is ready. Pass object_ref to read evidence text.",
    )


def _finding_ref(index: int, finding: ValidationFinding) -> str:
    return finding.finding_id or f"{_FINDING_INDEX_PREFIX}{index}"


def _finding_view(index: int, finding: ValidationFinding) -> dict[str, Any]:
    object_ref = None
    object_type = None
    field_path = None
    target = finding.field_ref.object_ref if finding.field_ref is not None else finding.object_ref
    if target is not None:
        object_ref = _object_ref_text(target)
        object_type = getattr(target, "object_type", None)
    if finding.field_ref is not None:
        field_path = finding.field_ref.field_path
    return {
        "finding_ref": _finding_ref(index, finding),
        "finding_id": finding.finding_id,
        "severity": finding.severity.value,
        "status": finding.status.value,
        "code": finding.code,
        "message": finding.message,
        "object_ref": object_ref,
        "object_type": object_type,
        "field_path": field_path,
        "details": finding.details,
    }


def _validation_response(
    result: _Result,
    *,
    object_ref: str | None,
    field_path: str | None,
    object_type: str | None,
    validation_state: str | None,
    severity: str | None,
    query: str | None,
    finding_ref: str | None,
    detail_path: str | None,
    cursor: Any,
    limit: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    findings = list(enumerate(result.envelope.validation_findings))
    if finding_ref:
        match = next(
            ((index, finding) for index, finding in findings
             if _finding_ref(index, finding) == finding_ref),
            None,
        )
        if match is None:
            raise _RequestError(
                "finding_not_found",
                "No validation finding matched finding_ref in this result.",
                action="validation",
                result_ref=result.result_ref,
                finding_ref=_echo(finding_ref),
            )
        full = _finding_view(*match)
        head = {**result.head("validation"), "finding_ref": finding_ref}
        sha = result.sha(action="validation", finding_ref=finding_ref, detail_path=detail_path)
        if detail_path:
            return _exact_value(
                _resolve_detail(full, detail_path),
                head={**head, "detail_path": detail_path},
                call=result.call("validation", finding_ref=finding_ref, detail_path=detail_path),
                sha=sha,
                expected_sha=expected_sha,
                cursor=cursor,
                message="Exact validation finding value is ready.",
            )
        return _fit_record(
            full,
            render=lambda shown: {
                "status": "ok",
                "message": "Validation finding is ready. Withheld values are read with their read call.",
                **head,
                "finding": shown,
                "result_sha256": sha,
            },
            read_for=lambda key: result.call(
                "validation", finding_ref=finding_ref, detail_path=key
            ),
            protected=_FINDING_IDENTITY_KEYS,
        )

    object_keys: set[tuple[str, str]] | None = None
    if object_ref:
        try:
            object_keys = set(_resolve_object(result.envelope, object_ref).ref_keys())
        except ValueError as exc:
            raise _RequestError(
                "object_not_found",
                "No object matched object_ref.",
                action="validation",
                result_ref=result.result_ref,
                object_ref=_echo(object_ref),
            ) from exc
    query_terms = _query_terms(query)
    matched = [
        (index, finding)
        for index, finding in findings
        if _finding_matches(finding, object_keys=object_keys, field_path=field_path)
        and (not object_type or _finding_object_type(finding) == object_type)
        and (
            validation_state is None
            or (validation_state == "open") == (finding.status is ValidationFindingStatus.OPEN)
        )
        and (not severity or finding.severity.value == severity)
        and (
            not query_terms
            or _query_matches(f"{finding.code or ''} {finding.message}", query_terms)
        )
    ]

    def view(item: tuple[int, ValidationFinding], _index: int) -> dict[str, Any]:
        index, finding = item
        full = _finding_view(index, finding)
        ref = full["finding_ref"]
        for key in ("message", "details"):
            full[key] = _value_view(
                full[key],
                read=result.call("validation", finding_ref=ref, detail_path=key),
            )
        return full

    def oversized(row: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            **{key: row[key] for key in _FINDING_IDENTITY_KEYS if key in row},
            "withheld": True,
            "total_chars": len(canonical_json(row)),
            "read": result.call("validation", finding_ref=row["finding_ref"]),
        }

    args = {
        key: value
        for key, value in {
            "object_ref": object_ref,
            "field_path": field_path,
            "object_type": object_type,
            "validation_state": validation_state,
            "severity": severity,
            "query": query,
        }.items()
        if value is not None
    }
    return _page(
        matched,
        view=view,
        rows_key="validation_findings",
        head={**result.head("validation"), "filters": args, "finding_count": len(matched)},
        call=result.call("validation", **args),
        sha=result.sha(action="validation", **args),
        expected_sha=expected_sha,
        cursor=cursor,
        limit=limit,
        default_limit=_VALIDATION_PAGE_SIZE,
        max_limit=_MAX_LIST_LIMIT,
        message="Validation findings are ready. Read one finding with its finding_ref.",
        oversized=oversized,
    )


def _validator_results_response(
    result: _Result,
    *,
    object_ref: str | None,
    field_path: str | None,
    object_type: str | None,
    decision_status: str | None,
    validation_state: str | None,
    key: str | None,
    detail_path: str | None,
    cursor: Any,
    limit: Any,
    expected_sha: str | None,
) -> dict[str, Any]:
    entries = validator_result_entries(result.envelope.validation_findings)
    if key:
        entry = next((value for entry_key, value in entries if entry_key == key), None)
        if entry is None:
            raise _RequestError(
                "validator_result_not_found",
                "No validator result matched validator_result_key in this result.",
                action="validator_results",
                result_ref=result.result_ref,
                validator_result_key=_echo(key),
            )
        full = {"validator_result_key": key, **entry}
        head = {**result.head("validator_results"), "validator_result_key": key}
        sha = result.sha(action="validator_results", key=key, detail_path=detail_path)
        if detail_path:
            return _exact_value(
                _resolve_detail(full, detail_path),
                head={**head, "detail_path": detail_path},
                call=result.call(
                    "validator_results", validator_result_key=key, detail_path=detail_path
                ),
                sha=sha,
                expected_sha=expected_sha,
                cursor=cursor,
                message="Exact validator result value is ready.",
            )
        return _fit_record(
            full,
            render=lambda shown: {
                "status": "ok",
                "message": "Validator result is ready. Withheld values are read with their read call.",
                **head,
                "validator_result": shown,
                "result_sha256": sha,
            },
            read_for=lambda name: result.call(
                "validator_results", validator_result_key=key, detail_path=name
            ),
            protected=frozenset({"validator_result_key", "status", "open_finding", "writeback_rejected"}),
        )

    def target_value(entry: Mapping[str, Any], name: str) -> Any:
        target = entry.get("target")
        return target.get(name) if isinstance(target, Mapping) else None

    object_ids: set[str] | None = None
    if object_ref:
        try:
            # Targets name an object by object_id or, before one exists, by its
            # pending ref; match either identity of the requested object.
            object_ids = {
                value for _kind, value in _resolve_object(result.envelope, object_ref).ref_keys()
            }
        except ValueError as exc:
            raise _RequestError(
                "object_not_found",
                "No object matched object_ref.",
                action="validator_results",
                result_ref=result.result_ref,
                object_ref=_echo(object_ref),
            ) from exc

    matched = [
        (entry_key, entry)
        for entry_key, entry in entries
        if (not decision_status or entry.get("status") == decision_status)
        and (
            validation_state is None
            or (validation_state == "open") == bool(entry.get("open_finding"))
        )
        and (
            object_ids is None
            or target_value(entry, "object_id") in object_ids
            or target_value(entry, "pending_ref_id") in object_ids
        )
        and (not object_type or target_value(entry, "object_type") == object_type)
        and (not field_path or target_value(entry, "field_path") == field_path)
    ]

    def view(item: tuple[str, dict[str, Any]], _index: int) -> dict[str, Any]:
        entry_key, entry = item
        return {
            "validator_result_key": entry_key,
            **{
                name: _value_view(
                    value,
                    read=result.call(
                        "validator_results", validator_result_key=entry_key, detail_path=name
                    ),
                )
                for name, value in entry.items()
            },
        }

    def oversized(row: dict[str, Any], _index: int) -> dict[str, Any]:
        return {
            **{
                name: row[name]
                for name in ("validator_result_key", "status", "open_finding", "writeback_rejected")
                if name in row
            },
            "withheld": True,
            "total_chars": len(canonical_json(row)),
            "read": result.call(
                "validator_results", validator_result_key=row["validator_result_key"]
            ),
        }

    args = {
        name: value
        for name, value in {
            "status": decision_status,
            "validation_state": validation_state,
            "object_ref": object_ref,
            "object_type": object_type,
            "field_path": field_path,
        }.items()
        if value is not None
    }
    return _page(
        matched,
        view=view,
        rows_key="validator_results",
        head={**result.head("validator_results"), "filters": args},
        call=result.call("validator_results", **args),
        sha=result.sha(action="validator_results", **args),
        expected_sha=expected_sha,
        cursor=cursor,
        limit=limit,
        default_limit=get_inspect_results_object_page_size(),
        max_limit=get_inspect_results_object_max_page_size(),
        message=(
            f"{len(matched)} automatic validator result(s). These are validator decisions, "
            "not proof of accepted writeback or export readiness; writeback_rejected "
            "results are not accepted identities and open findings stay open."
        ),
        oversized=oversized,
    )


# ---------------------------------------------------------------------------
# Record resolution and authorization
# ---------------------------------------------------------------------------


def _authorized_records(
    *,
    result_id: str | None,
    target: str,
    session_id: str,
    user_id: str,
    flow_run_id: str | None,
    adapter_keys: Sequence[str] | None,
) -> tuple[list[Any], str | None]:
    document_id = _active_document_id(user_id)
    if result_id:
        if target in {"latest", "this_chat"}:
            records = _session_records(session_id=session_id, user_id=user_id)
        elif target == "current_document":
            if not document_id:
                return [], "document_required"
            records = _document_records(document_id=document_id, user_id=user_id)
        elif target == "flow_run":
            records = (
                list_extraction_results(user_id=user_id, flow_run_id=flow_run_id)
                if _optional_text(flow_run_id)
                else _session_records(session_id=session_id, user_id=user_id)
            )
        else:
            records = list_extraction_results(user_id=user_id)
        records = _dedupe_records(records)
        records = [record for record in records if _record_id(record) == result_id]
    elif target in {"latest", "this_chat"}:
        records = _session_records(session_id=session_id, user_id=user_id)
    elif target == "current_document":
        if not document_id:
            return [], "document_required"
        records = _document_records(document_id=document_id, user_id=user_id)
    elif target == "flow_run":
        if not _optional_text(flow_run_id):
            return [], "flow_run_required"
        records = list_extraction_results(user_id=user_id, flow_run_id=flow_run_id)
    else:
        records = list_extraction_results(user_id=user_id)

    records = _filter_by_adapter(records, adapter_keys)
    return _sort_records_newest(records), None


def _result_carry(record: Any, *, target: str, flow_run_id: str | None) -> dict[str, Any]:
    """Arguments that authorize the same result again on a follow-up call."""

    carry: dict[str, Any] = {"result_ref": _record_result_ref(record)}
    if target not in {"latest", "this_chat"}:
        carry["target"] = target
    if target == "flow_run" and _optional_text(flow_run_id):
        carry["flow_run_id"] = _optional_text(flow_run_id)
    return carry


def _session_records(*, session_id: str, user_id: str) -> list[Any]:
    return _dedupe_records(
        [
            *list_extraction_results(
                origin_session_id=session_id,
                user_id=user_id,
                source_kind=CurationExtractionSourceKind.CHAT,
            ),
            *list_extraction_results(
                origin_session_id=session_id,
                user_id=user_id,
                source_kind=CurationExtractionSourceKind.FLOW,
            ),
        ]
    )


def _document_records(*, document_id: str, user_id: str) -> list[Any]:
    return list_extraction_results(document_id=document_id, user_id=user_id)


def _filter_by_adapter(records: Sequence[Any], adapter_keys: Sequence[str] | None) -> list[Any]:
    allowed = {str(key).strip() for key in adapter_keys or [] if str(key).strip()}
    if not allowed:
        return list(records)
    return [
        record
        for record in records
        if str(_record_attr(record, "adapter_key") or "") in allowed
    ]


def _dedupe_records(records: Sequence[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for record in records:
        record_id = _record_id(record)
        key = record_id or str(id(record))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _sort_records_newest(records: Sequence[Any]) -> list[Any]:
    return sorted(
        records,
        key=lambda record: (_record_created_timestamp(record), _record_id(record)),
        reverse=True,
    )


def _record_summary(record: Any) -> dict[str, Any]:
    try:
        envelope = _canonical_envelope_for_record(record)
        canonical_status = "canonical_domain_envelope"
        object_count: int | None = len(envelope.extracted_objects)
        warning_count, error_count = _validation_severity_counts(
            envelope.validation_findings
        )
        validation_warning_count: int | None = warning_count
        validation_error_count: int | None = error_count
        domain_pack_id = envelope.domain_pack_id
        result_status = envelope.status.value
    except (TypeError, ValueError, ValidationError):
        canonical_status = "unsupported_payload"
        object_count = None
        validation_warning_count = None
        validation_error_count = None
        domain_pack_id = None
        result_status = None
    return {
        "result_ref": _record_result_ref(record),
        "extraction_result_id": _record_id(record),
        "document_id": _record_attr(record, "document_id"),
        "adapter_key": _record_attr(record, "adapter_key"),
        "agent_key": _record_attr(record, "agent_key"),
        "source_kind": _record_source_kind(record),
        "origin_session_id": _record_attr(record, "origin_session_id"),
        "flow_run_id": _record_attr(record, "flow_run_id"),
        "trace_id": _record_attr(record, "trace_id"),
        "created_at": _record_created_at_text(record),
        "domain_pack_id": domain_pack_id,
        "result_status": result_status,
        "canonical_status": canonical_status,
        "object_count": object_count,
        "validation_warning_count": validation_warning_count,
        "validation_error_count": validation_error_count,
    }


# ---------------------------------------------------------------------------
# Cross-result search
# ---------------------------------------------------------------------------


def _search_matches_for_record(
    record: Any,
    *,
    envelope: DomainEnvelope,
    query: str | None,
    read_carry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    query_terms = _query_terms(query)
    for obj in envelope.extracted_objects:
        object_ref = _canonical_object_ref(obj)
        if not query_terms:
            matches.append(
                _search_match_base(
                    record,
                    obj,
                    object_ref=object_ref,
                    match_type="object_preview",
                    text=_object_preview_text(envelope, obj),
                    read={"action": "object", **read_carry, "object_ref": object_ref},
                )
            )
            continue
        matches.extend(
            _field_search_matches(
                record,
                envelope=envelope,
                obj=obj,
                object_ref=object_ref,
                query_terms=query_terms,
                read_carry=read_carry,
            )
        )
        matches.extend(
            _evidence_search_matches(
                record,
                envelope=envelope,
                obj=obj,
                object_ref=object_ref,
                query_terms=query_terms,
                read_carry=read_carry,
            )
        )
    return matches


def _field_search_matches(
    record: Any,
    *,
    envelope: DomainEnvelope,
    obj: CuratableObjectEnvelope,
    object_ref: str,
    query_terms: Sequence[str],
    read_carry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    try:
        visible_paths = _supervisor_visible_field_paths(
            envelope.domain_pack_id,
            obj.object_type,
        )
    except ValueError:
        visible_paths = set()
    matches: list[dict[str, Any]] = []
    for field_path in sorted(visible_paths):
        if _is_evidence_path(field_path):
            continue
        value = _payload_path_value(obj.payload, field_path)
        if not _is_scalar(value):
            continue
        text = f"{field_path}: {value}"
        if _query_matches(text, query_terms):
            matches.append(
                _search_match_base(
                    record,
                    obj,
                    object_ref=object_ref,
                    match_type="manifest_field",
                    text=text,
                    field_path=field_path,
                    read={"action": "field", **read_carry, "object_ref": object_ref,
                          "field_path": field_path},
                )
            )
    return matches


def _evidence_search_matches(
    record: Any,
    *,
    envelope: DomainEnvelope,
    obj: CuratableObjectEnvelope,
    object_ref: str,
    query_terms: Sequence[str],
    read_carry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for index, evidence_record in enumerate(_object_evidence_records(envelope, obj)):
        haystack = " ".join(
            str(value)
            for value in evidence_record.values()
            if value is not None
        )
        if not _query_matches(haystack, query_terms):
            continue
        text_key = next((key for key in _EVIDENCE_TEXT_KEYS if evidence_record.get(key) is not None), None)
        read = {"action": "evidence", **read_carry, "object_ref": object_ref}
        if text_key is not None:
            read["detail_path"] = f"{index}.{text_key}"
        match = _search_match_base(
            record,
            obj,
            object_ref=object_ref,
            match_type="evidence_text",
            text=(
                str(evidence_record[text_key])
                if text_key is not None
                else canonical_json(_compact_evidence_record(evidence_record))
            ),
            read=read,
        )
        evidence_id = evidence_record.get("evidence_record_id") or evidence_record.get("id")
        if evidence_id is not None:
            match["evidence_record_id"] = str(evidence_id)
        matches.append(match)
    return matches


def _search_match_base(
    record: Any,
    obj: CuratableObjectEnvelope,
    *,
    object_ref: str,
    match_type: str,
    text: str,
    read: Mapping[str, Any],
    field_path: str | None = None,
) -> dict[str, Any]:
    snippet, complete = _snippet(text)
    match = {
        "result_ref": _record_result_ref(record),
        "extraction_result_id": _record_id(record),
        "object_ref": object_ref,
        "object_type": obj.object_type,
        "adapter_key": _record_attr(record, "adapter_key"),
        "agent_key": _record_attr(record, "agent_key"),
        "document_id": _record_attr(record, "document_id"),
        "source_kind": _record_source_kind(record),
        "origin_session_id": _record_attr(record, "origin_session_id"),
        "flow_run_id": _record_attr(record, "flow_run_id"),
        "created_at": _record_created_at_text(record),
        "snippet": snippet,
        "snippet_complete": complete,
        "match_type": match_type,
        "read": dict(read),
    }
    if field_path:
        match["field_path"] = field_path
    return match


def _object_preview_text(
    envelope: DomainEnvelope,
    obj: CuratableObjectEnvelope,
) -> str:
    try:
        visible_paths = _supervisor_visible_field_paths(
            envelope.domain_pack_id,
            obj.object_type,
        )
    except ValueError:
        visible_paths = set()
    parts = [
        f"{field_path}: {value}"
        for field_path in sorted(visible_paths)
        if not _is_evidence_path(field_path)
        and _is_scalar(value := _payload_path_value(obj.payload, field_path))
    ]
    if parts:
        return "; ".join(parts)
    return obj.object_type


def _query_terms(query: str | None) -> list[str]:
    return [term for term in str(query or "").lower().split() if term]


def _query_matches(value: Any, query_terms: Sequence[str]) -> bool:
    if not query_terms:
        return True
    haystack = " ".join(str(value or "").lower().split())
    return all(term in haystack for term in query_terms)


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def _validation_severity_counts(
    findings: Sequence[ValidationFinding],
) -> tuple[int, int]:
    """Split validation findings into (warning_count, error_count) by severity.

    Errors include both ``error`` and ``blocker`` severities, matching the manifest
    renderer's severity grouping.
    """

    warning_count = 0
    error_count = 0
    for finding in findings:
        if finding.severity in {
            ValidationFindingSeverity.ERROR,
            ValidationFindingSeverity.BLOCKER,
        }:
            error_count += 1
        elif finding.severity is ValidationFindingSeverity.WARNING:
            warning_count += 1
    return warning_count, error_count


def _canonical_envelope_for_record(record: Any) -> DomainEnvelope:
    return DomainEnvelope.model_validate(dict(_record_payload_mapping(record)))


def _record_payload_mapping(record: Any) -> Mapping[str, Any]:
    payload = _record_attr(record, "payload_json")
    if not isinstance(payload, Mapping):
        raise TypeError("extraction result payload_json must be a canonical object")
    return payload


def _resolve_object(
    envelope: DomainEnvelope,
    object_ref: str,
) -> CuratableObjectEnvelope:
    normalized_ref = str(object_ref or "").strip()
    for obj in envelope.extracted_objects:
        if _canonical_object_ref(obj) == normalized_ref:
            return obj
    raise ValueError(f"No object matched object_ref {normalized_ref!r}")


def _canonical_object_ref(obj: CuratableObjectEnvelope) -> str:
    if obj.object_id:
        return obj.object_id
    if obj.pending_ref_id:
        return obj.pending_ref_id
    return obj.object_type


def _supervisor_visible_field_paths(domain_pack_id: str, object_type: str) -> set[str]:
    metadata = _domain_pack_metadata(domain_pack_id)
    policy = supervisor_manifest_policy_for_object(metadata, object_type)
    return set(policy.field_paths)


def _domain_pack_metadata(domain_pack_id: str) -> DomainPackMetadata:
    from src.lib.curation_workspace.adapter_registry import (
        resolve_curation_domain_pack_by_id,
    )

    domain_pack = resolve_curation_domain_pack_by_id(domain_pack_id)
    if isinstance(domain_pack, DomainPackMetadata):
        return domain_pack
    metadata = getattr(domain_pack, "metadata", domain_pack)
    if isinstance(metadata, DomainPackMetadata):
        return metadata
    raise ValueError(f"Cannot resolve domain pack {domain_pack_id!r}")


def _payload_path_value(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for part in parse_field_path(field_path):
        if isinstance(part, str):
            if not isinstance(current, Mapping) or part not in current:
                return None
            current = current[part]
            continue
        if (
            not isinstance(current, Sequence)
            or isinstance(current, (str, bytes, bytearray))
            or part >= len(current)
        ):
            return None
        current = current[part]
    return current


def _is_evidence_path(field_path: str) -> bool:
    try:
        parts = parse_field_path(field_path)
    except ValueError:
        return False
    for part in parts:
        if not isinstance(part, str):
            continue
        lowered = part.lower()
        if lowered in _EVIDENCE_PATH_PARTS or lowered.endswith("_quote"):
            return True
    return False


def _object_evidence_records(
    envelope: DomainEnvelope,
    obj: CuratableObjectEnvelope,
) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    metadata_records = _metadata_evidence_records(envelope.metadata)
    metadata_by_id = {
        str(item.get("evidence_record_id") or item.get("id") or ""): item
        for item in metadata_records
        if isinstance(item, Mapping)
    }
    for evidence_id in obj.evidence_record_ids:
        metadata_record = metadata_by_id.get(str(evidence_id))
        if metadata_record is not None:
            records.append(metadata_record)
        else:
            records.append({"evidence_record_id": evidence_id})

    direct_evidence = _payload_evidence_record(obj)
    if direct_evidence:
        if records and not any(_record_has_evidence_text(item) for item in records):
            merged = dict(records[0])
            merged.update(direct_evidence)
            records[0] = merged
        else:
            records.append(direct_evidence)

    return _dedupe_evidence_records(records)


def _metadata_evidence_records(metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    # Canonical conversion retains extractor metadata under extraction_metadata.
    # Native domain envelopes can instead carry evidence directly in metadata.
    nested = metadata.get("extraction_metadata")
    sources = [metadata, nested] if isinstance(nested, Mapping) else [metadata]
    records = []
    for source in sources:
        raw = source.get("evidence_records")
        if isinstance(raw, list):
            records.extend(item for item in raw if isinstance(item, Mapping))
    return records


def _payload_evidence_record(obj: CuratableObjectEnvelope) -> dict[str, Any]:
    record: dict[str, Any] = {}
    if obj.evidence_record_ids:
        record["evidence_record_id"] = obj.evidence_record_ids[0]
    for key in (*_EVIDENCE_TEXT_KEYS, *_EVIDENCE_CONTEXT_KEYS):
        value = obj.payload.get(key)
        if value is not None:
            record[key] = value
    return record


def _record_has_evidence_text(record: Mapping[str, Any]) -> bool:
    return any(record.get(key) for key in _EVIDENCE_TEXT_KEYS)


def _dedupe_evidence_records(records: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    deduped: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = str(
            record.get("evidence_record_id")
            or record.get("id")
            or record.get("verified_quote")
            or record.get("quote")
            or id(record)
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _compact_evidence_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Identity, location and quote keys of one evidence record, values exact."""

    return {
        key: record[key]
        for key in ("evidence_record_id", "id", "field_path", *_EVIDENCE_CONTEXT_KEYS,
                    *_EVIDENCE_TEXT_KEYS)
        if record.get(key) is not None
    }


def _finding_object_key(finding: ValidationFinding) -> tuple[str, str] | None:
    if finding.field_ref is not None:
        return finding.field_ref.object_ref.ref_key()
    if finding.object_ref is not None:
        return finding.object_ref.ref_key()
    return None


def _finding_object_type(finding: ValidationFinding) -> str | None:
    target = finding.field_ref.object_ref if finding.field_ref is not None else finding.object_ref
    return getattr(target, "object_type", None) if target is not None else None


def _finding_matches(
    finding: ValidationFinding,
    *,
    object_keys: set[tuple[str, str]] | None,
    field_path: str | None,
) -> bool:
    if object_keys is not None and _finding_object_key(finding) not in object_keys:
        return False
    if field_path is not None:
        if finding.field_ref is None:
            return False
        return finding.field_ref.field_path == field_path
    return True


def _object_ref_text(object_ref: Any) -> str:
    if getattr(object_ref, "object_id", None):
        return str(object_ref.object_id)
    if getattr(object_ref, "pending_ref_id", None):
        return str(object_ref.pending_ref_id)
    return str(getattr(object_ref, "object_type", "") or "")


def _parse_result_ref(value: str | None) -> tuple[str | None, str | None]:
    text = _optional_text(value)
    if text is None:
        return None, None
    if not text.startswith(_RESULT_REF_PREFIX):
        try:
            UUID(text)
        except (TypeError, ValueError):
            return None, "invalid_result_ref"
        return None, "raw_uuid_result_ref"
    raw_uuid = text[len(_RESULT_REF_PREFIX) :].strip()
    try:
        return str(UUID(raw_uuid)), None
    except (TypeError, ValueError):
        return None, "invalid_result_ref"


def _normalize_action(action: str | None) -> str:
    return str(action or "help").strip().lower() or "help"


def _normalize_target(target: str | None) -> str:
    return str(target or "latest").strip().lower() or "latest"


def _resolve_error_message(error_code: str) -> str:
    if error_code == "document_required":
        return "target=\"current_document\" requires an active document in this chat."
    if error_code == "flow_run_required":
        return "target=\"flow_run\" requires flow_run_id unless result_ref is supplied."
    return "Could not resolve authorized extraction results."


def _active_document_id(user_id: str) -> str | None:
    active_document = document_state.get_document(user_id)
    if not isinstance(active_document, Mapping):
        return None
    return _optional_text(active_document.get("id"))


def _record_attr(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        if name == "extraction_result_id":
            return record.get("extraction_result_id") or record.get("id")
        if name == "metadata":
            return record.get("metadata") or record.get("extraction_metadata")
        return record.get(name)
    if name == "extraction_result_id":
        return getattr(record, "extraction_result_id", None) or getattr(record, "id", None)
    if name == "metadata":
        return getattr(record, "metadata", None) or getattr(record, "extraction_metadata", None)
    return getattr(record, name, None)


def _record_id(record: Any) -> str:
    return str(_record_attr(record, "extraction_result_id") or "").strip()


def _record_result_ref(record: Any) -> str:
    return f"{_RESULT_REF_PREFIX}{_record_id(record)}"


def _record_source_kind(record: Any) -> str | None:
    source_kind = _record_attr(record, "source_kind")
    value = getattr(source_kind, "value", source_kind)
    return str(value) if value is not None else None


def _record_created_timestamp(record: Any) -> float:
    created_at = _record_attr(record, "created_at")
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at.timestamp()
    if isinstance(created_at, str):
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return 0.0


def _record_created_at_text(record: Any) -> str | None:
    created_at = _record_attr(record, "created_at")
    if isinstance(created_at, datetime):
        return created_at.isoformat()
    if created_at is None:
        return None
    return str(created_at)


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _echo(value: Any) -> str:
    """Short preview of model-supplied input echoed back in a response."""

    text = " ".join(str(value if value is not None else "").split())
    if len(text) <= _TEXT_PREVIEW_LIMIT:
        return text
    return f"{text[: max(1, _TEXT_PREVIEW_LIMIT - 3)].rstrip()}..."


def _error(error_code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "error" if error_code not in {"unavailable", "no_context"} else error_code,
        "message": message,
        "error_code": error_code,
        **extra,
    }


__all__ = ["inspect_results"]
