"""Shadow Decisions quick check beside supervisor review.

When a supervisor review pins a fresh review packet, the Decisions model answers
fixed yes/no questions about each record: is it within the request, does its
quoted evidence support what it identifies, does it come from this study, and
does each validator answer name the same entity. Code then applies calibrated
thresholds to say which route each record would take (skip review, remove, or
supervisor review) and logs the answers and routes for later calibration.

This is an observer. It changes no review state, packet content, reviewer
prompt or curator-visible result, and review never waits for it or depends on
it. A failed or refused check is logged and nothing else happens.

The model owns every judgment of meaning. Code only builds the input from the
pinned packet, compares returned probabilities with fixed thresholds and keeps
sub-records with the supervisor because of their declared role.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

logger = logging.getLogger(__name__)

EVENT_TYPE = "review_quick_check.shadow"
MODEL = "gpt-6-luna"
DECISIONS_PATH = "/decisions"
# Provider request contract: one Decisions request carries 1 to 400 questions.
MAX_QUESTIONS_PER_REQUEST = 400

# The question wording and input format below were measured against supervisor
# review decisions on 2026-11-07. Changing any wording, input cap or threshold
# makes a new version that needs its own calibration.
WORDING_VERSION = "W5"
REQUEST_MAX_CHARS = 3000
QUOTE_MAX_CHARS = 1500
VALUE_MAX_CHARS = 600
VALIDATOR_DETAIL_MAX_CHARS = 400
REMOVE_IN_SCOPE_AT_MOST = 0.02
SKIP_IN_SCOPE_AT_LEAST = 0.8
SKIP_SUPPORTED_AT_LEAST = 0.8
SKIP_OWN_STUDY_AT_LEAST = 0.5
SKIP_VALIDATOR_AT_LEAST = 0.8
THRESHOLDS = {
    "remove_in_scope_at_most": REMOVE_IN_SCOPE_AT_MOST,
    "skip_in_scope_at_least": SKIP_IN_SCOPE_AT_LEAST,
    "skip_supported_at_least": SKIP_SUPPORTED_AT_LEAST,
    "skip_own_study_at_least": SKIP_OWN_STUDY_AT_LEAST,
    "skip_validator_at_least": SKIP_VALIDATOR_AT_LEAST,
}

# A packet record shows a role only when the extraction declared one that
# differs from its type. Records without one, and curatable units, are what a
# curator acts on. Any other declared role (validated references, metadata,
# evidence quotes) is a sub-record and always stays with the supervisor.
TOP_LEVEL_ROLES = (None, "curatable_unit")
# Validator states that carry no resolved answer for the model to compare.
UNANSWERED_VALIDATOR_STATES = (None, "failed", "not_validated", "no_completion")


@dataclass
class _ReviewScope:
    curator_request: str | None
    record: Any
    checked: set[str] = field(default_factory=set)


_SCOPE: ContextVar[_ReviewScope | None] = ContextVar("validation_review_quick_check", default=None)
# Strong references keep fire-and-forget checks alive until they finish.
_RUNNING: set[asyncio.Task] = set()


@contextmanager
def review_scope(curator_request, record):
    """Bind one supervisor review's request and extraction record for packets it pins.

    Nothing is read here; the record is inspected only inside the guarded check.
    """
    token = _SCOPE.set(_ReviewScope(curator_request, record))
    try:
        yield
    finally:
        _SCOPE.reset(token)


def packet_pinned(context, packet_id):
    """Start the shadow check for a freshly pinned packet; never raises or waits."""
    try:
        scope = _SCOPE.get()
        if scope is None or packet_id in scope.checked:
            return
        from src.lib.openai_agents.config import get_validation_review_quick_check_mode

        if get_validation_review_quick_check_mode() != "shadow":
            return
        scope.checked.add(packet_id)
        task = asyncio.get_running_loop().create_task(_observe(context, packet_id, scope))
        _RUNNING.add(task)
        task.add_done_callback(_RUNNING.discard)
    except Exception as error:
        _report_unexpected(error)


def review_request(curator_request, execution_context):
    """The request text the questions quote, chosen as in the calibration trial.

    A chat review quotes the curator's own question. A flow step, or a chat
    turn without a question, quotes the request the extractor actually ran.
    """
    context = execution_context or {}
    delegation = context.get("executed_query")
    if context.get("source_kind", "chat") == "chat" and curator_request:
        text, source = curator_request, "curator_request"
    elif delegation:
        text, source = delegation, "delegation"
    elif curator_request:
        text, source = curator_request, "curator_request"
    else:
        return None, "none"
    if len(text) > REQUEST_MAX_CHARS:
        text = text[:REQUEST_MAX_CHARS] + " [truncated]"
    return text, source


def _short(value, cap):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= cap else text[:cap] + '…"'


def _quote(evidence, handle):
    from .record_source import packet_quote

    seen = set()
    while handle is not None and handle not in seen:
        seen.add(handle)
        item = evidence.get(handle) or {}
        quote = packet_quote(item)
        if quote is not None:
            return quote
        handle = item.get("same_quote_as")
    return None


def packet_records(body):
    """Each numbered packet record with its quotes and decidable validator items."""
    evidence = body["evidence"]
    records = []
    for item in body["records"]:
        if item["record"] is None:
            continue
        role = item.get("role")
        records.append({
            "record": item["record"],
            "target_id": item.get("target_id"),
            "type": item.get("type"),
            "role": role,
            "top_level": role in TOP_LEVEL_ROLES,
            "source_available": "source_unavailable" not in item,
            "values": {key: value for key, value in (item.get("values") or {}).items() if key != "class_key"},
            "evidence": [
                {"id": handle, "page": (evidence.get(handle) or {}).get("page"),
                 "section": (evidence.get(handle) or {}).get("section"),
                 "quote": (_quote(evidence, handle) or "")[:QUOTE_MAX_CHARS]}
                for handle in item.get("evidence") or []
            ],
            "validators": [
                {"validator": entry.get("validator"), "field": entry.get("field"),
                 "status": entry.get("status") or entry.get("outcome"),
                 "values": entry.get("values"), "explanation": entry.get("explanation")}
                for entry in item.get("review") or []
                if entry.get("review") != "record" and "handle" in entry
            ],
        })
    return records


def asked_records(records):
    """Records the model is asked about; a record without its saved source is not."""
    return [record for record in records if record["source_available"]]


def build_input(request, records):
    lines = [
        "A curator asked an AI curation assistant this request about one publication:",
        f'"{request or "(no request text recorded)"}"',
        "",
        "The assistant extracted the records below. Each record lists its saved values, the exact sentences quoted "
        "from the paper as evidence, and, where a database validator was run, the validator's answer.",
        "",
    ]
    for record in records:
        rid = record["record"]
        role = f", role {record['role']}" if record["role"] else ""
        lines.append(f"Record {rid} ({record['type']}{role}):")
        for key, value in record["values"].items():
            lines.append(f"  {key}: {_short(value, VALUE_MAX_CHARS)}")
        if not record["evidence"]:
            lines.append("  (no evidence quotes)")
        for item in record["evidence"]:
            lines.append(f"  evidence {item['id']} (page {item['page']}, {item['section'] or ''}): \"{item['quote']}\"")
        for index, validator in enumerate(record["validators"]):
            parts = [f"validator answer {rid}V{index + 1} ({validator['validator']}, field {validator['field']}): "
                     f"status {validator['status']}"]
            if validator["values"]:
                parts.append(f"resolved values {_short(validator['values'], VALIDATOR_DETAIL_MAX_CHARS)}")
            if validator["explanation"]:
                parts.append(f"explanation {_short(validator['explanation'], VALIDATOR_DETAIL_MAX_CHARS)}")
            lines.append("  " + "; ".join(parts))
        lines.append("")
    return "\n".join(lines)


def _predicate(name, instructions):
    return {"type": "predicate", "name": name, "instructions": instructions}


def validator_question_ids(record):
    rid = record["record"]
    return [f"{rid}V{index + 1}" for index, validator in enumerate(record["validators"])
            if validator["status"] not in UNANSWERED_VALIDATOR_STATES]


def record_questions(record):
    rid = record["record"]
    questions = [
        _predicate(f"in_scope__{rid}", (
            f"Record {rid} could reasonably be part of what the curator's request asks for. Answer no only when it "
            "is clearly outside the request: a different kind of thing than requested, or a control, comparison "
            "or background item the request does not cover. If the request could fairly be read to include it, "
            "answer yes.")),
        _predicate(f"supported__{rid}", (
            f"The quoted evidence for record {rid} supports what the record identifies: the entity it names "
            "(label, symbol or name as printed), its type, and any identifiers or source codes it gives. Judge "
            "only these identity values; ignore rationale, notes, classification notes and other interpretive "
            "text.")),
        _predicate(f"own_study__{rid}", (
            f"The quoted evidence shows record {rid} comes from this publication's own work: it was used, "
            "generated or observed in this study, not only cited from earlier work, taken from another group's "
            "data or images, or mentioned as background.")),
    ]
    for vid in validator_question_ids(record):
        questions.append(_predicate(f"validator__{vid}", (
            f"Validator answer {vid} names the same entity that record {rid} and its quoted evidence describe "
            "(the same gene, term, chemical or reagent, in the same species), rather than a different one.")))
    return questions


def chunks(questions):
    return [questions[start:start + MAX_QUESTIONS_PER_REQUEST]
            for start in range(0, len(questions), MAX_QUESTIONS_PER_REQUEST)]


def route(record, probabilities):
    """The route the thresholds give one record, and why when it is structural."""
    if not record["source_available"]:
        return "sol", "source_unavailable"
    if not record["top_level"]:
        return "sol", "sub_record"
    rid = record["record"]
    in_scope = probabilities.get(f"in_scope__{rid}")
    if in_scope is not None and in_scope <= REMOVE_IN_SCOPE_AT_MOST:
        return "remove", None
    required = {
        f"in_scope__{rid}": SKIP_IN_SCOPE_AT_LEAST,
        f"supported__{rid}": SKIP_SUPPORTED_AT_LEAST,
        f"own_study__{rid}": SKIP_OWN_STUDY_AT_LEAST,
        **{f"validator__{vid}": SKIP_VALIDATOR_AT_LEAST for vid in validator_question_ids(record)},
    }
    if any(name not in probabilities for name in required):
        return "sol", "unanswered"
    if all(probabilities[name] >= floor for name, floor in required.items()):
        return "skip", None
    return "sol", None


class QuickCheckUnavailable(RuntimeError):
    """The deployment has no usable OpenAI credentials for the check."""


class QuickCheckResponseError(RuntimeError):
    """The provider returned a body without the Decisions answer structure."""


def _decisions_client():
    from openai import AsyncOpenAI
    from src.lib.openai_agents.config import (
        get_api_key,
        get_base_url,
        get_validation_review_quick_check_max_retries,
        get_validation_review_quick_check_timeout_seconds,
    )

    api_key = get_api_key("openai")
    if not str(api_key or "").strip():
        raise QuickCheckUnavailable("OPENAI_API_KEY is not configured")
    kwargs = {
        "api_key": api_key,
        "timeout": get_validation_review_quick_check_timeout_seconds(),
        "max_retries": get_validation_review_quick_check_max_retries(),
    }
    base_url = get_base_url("openai")
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


def _usage(payloads):
    """Summed token counts, or None when any response omitted usage.

    Keys avoid the word token, which log redaction treats as a secret name.
    """
    reported = [payload.get("usage") for payload in payloads]
    if not all(isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int) for usage in reported):
        return None
    totals = {"input": 0, "cached_input": 0, "cache_write_input": 0, "output": 0}
    for usage in reported:
        # Cache details are reported only when caching applied.
        details = usage.get("input_tokens_details") or {}
        totals["input"] += usage["input_tokens"]
        totals["cached_input"] += details.get("cached_tokens", 0)
        totals["cache_write_input"] += details.get("cache_write_tokens", 0)
        totals["output"] += usage.get("output_tokens", 0)
    return totals


async def check_packet(body, *, request, cache_key):
    """Ask the W5 questions about one pinned packet and apply the thresholds."""
    started = perf_counter()
    records = packet_records(body)
    asked = asked_records(records)
    questions = [question for record in asked for question in record_questions(record)]
    result = {"record_count": len(records), "question_count": len(questions), "thresholds": THRESHOLDS}
    if not questions:
        return {**result, "status": "no_questions", "request_count": 0, "requests": [],
                "latency_ms": round((perf_counter() - started) * 1000)}
    request_input = build_input(request, asked)
    payloads, requests = [], []
    async with _decisions_client() as client:
        for part in chunks(questions):
            sent = perf_counter()
            payload = await client.post(DECISIONS_PATH, cast_to=object, body={
                "model": MODEL, "input": request_input, "questions": part, "prompt_cache_key": cache_key})
            if not isinstance(payload, dict) or not isinstance(payload.get("answers"), list):
                raise QuickCheckResponseError("Decisions response has no answers list")
            payloads.append(payload)
            requests.append({"questions": len(part), "latency_ms": round((perf_counter() - sent) * 1000)})
    probabilities, refused = {}, []
    for payload in payloads:
        for answer in payload["answers"]:
            if not isinstance(answer, dict) or not isinstance(answer.get("name"), str):
                raise QuickCheckResponseError("Decisions answer has no name")
            if answer.get("type") == "refusal":
                refused.append(answer["name"])
            elif isinstance(answer.get("probability"), (int, float)):
                probabilities[answer["name"]] = float(answer["probability"])
            else:
                raise QuickCheckResponseError("Decisions predicate answer has no probability")
    missing = [question["name"] for question in questions
               if question["name"] not in probabilities and question["name"] not in refused]
    rows, routes = [], {"skip": 0, "remove": 0, "sol": 0}
    for record in records:
        rid = record["record"]
        chosen, reason = route(record, probabilities)
        routes[chosen] += 1
        row = {"record": rid, "target_id": record["target_id"], "type": record["type"], "role": record["role"],
               "top_level": record["top_level"], "route": chosen}
        if reason is not None:
            row["route_reason"] = reason
        for name in ("in_scope", "supported", "own_study"):
            row[name] = probabilities.get(f"{name}__{rid}")
        validators = {vid: probabilities.get(f"validator__{vid}") for vid in validator_question_ids(record)}
        if validators:
            row["validators"] = validators
        rows.append(row)
    return {
        **result,
        "status": "partial" if refused or missing else "completed",
        "request_count": len(requests),
        "requests": requests,
        "latency_ms": round((perf_counter() - started) * 1000),
        "usage": _usage(payloads),
        "routes": routes,
        "records": rows,
        "refused": refused,
        "missing": missing,
    }


def _load_packet(history_scope, episode_id, packet_id):
    """Read the pinned packet body without locking the episode or counting a reviewer read."""
    from src.lib.validation_history import store
    from src.models.sql.validation_review import ValidationReviewEpisode
    from .reads import PACKET_KIND

    with store.SessionLocal() as db:
        episode = db.get(ValidationReviewEpisode, episode_id)
        if episode is None:
            raise store.HistoryAccessError("Admitted review episode unavailable")
        store._artifact(db, episode.artifact_id, history_scope)
        fact = store._fact(db, episode.artifact_id, packet_id, kind=PACKET_KIND)
        if fact.parent_id != episode_id:
            raise store.HistoryConflict("Review packet belongs to another review round")
        return store._content(db, fact)


def _is_provider_failure(error):
    from openai import APIError

    return isinstance(error, (APIError, QuickCheckUnavailable, QuickCheckResponseError))


def _error_summary(error):
    summary = {"type": type(error).__name__}
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        summary["status_code"] = status_code
    code = getattr(error, "code", None)
    if isinstance(code, str):
        summary["code"] = code
    return summary


def _report_unexpected(error):
    """A defect in this observer is reported once, without content, and never raised."""
    try:
        from src.lib.observability.runtime import report_runtime_exception

        try:
            raise RuntimeError(
                "Validation review quick check failed (" + type(error).__name__ + ")"
            ) from None
        except RuntimeError as safe:
            safe.__context__ = None
            report_runtime_exception(
                safe, component="validation_review", operation="quick_check_failed", level="warning"
            )
    except Exception:
        pass  # Reporting a shadow observer's failure must never reach review.
    logger.warning(
        "Validation review quick check failed",
        extra={"event": "review_quick_check_failed", "error_type": type(error).__name__,
               "sentry_skip_event": True},
    )


_LOG_RECORD_FIELDS = ("record", "route", "route_reason", "in_scope", "supported", "own_study", "validators")


def _emit(payload):
    """One trace event (durable file and Langfuse) and one structured log line.

    The log line keeps each record's probabilities and route but leaves the
    record identity details to the trace event, so a large packet stays one
    reasonably sized line.
    """
    from src.lib.openai_agents.extraction_trace_events import write_extraction_trace_event

    write_extraction_trace_event(event_type=EVENT_TYPE, output_summary=payload)
    line = {**payload}
    if "records" in payload:
        line["records"] = [{key: row[key] for key in _LOG_RECORD_FIELDS if key in row} for row in payload["records"]]
    log = logger.info if payload["status"] in ("completed", "no_questions") else logger.warning
    log("Validation review quick check " + payload["status"],
        extra={"event": "review_quick_check", "quick_check": line, "sentry_skip_event": True})


async def _observe(context, packet_id, scope):
    from .provider import ACTIVE_BUDGET

    # This task runs in its own copy of the reviewer's context. Clearing the
    # review budget there keeps the check from ever being charged to the review.
    ACTIVE_BUDGET.set(None)
    started = perf_counter()
    payload = {"packet_id": packet_id, "episode_id": context.episode_id, "wording_version": WORDING_VERSION,
               "model": MODEL, "mode": "shadow"}
    try:
        try:
            body = await asyncio.to_thread(_load_packet, context.history_scope, context.episode_id, packet_id)
            execution_context = scope.record.metadata.get("execution_context")
            request, source = review_request(scope.curator_request, execution_context)
            payload.update(request_source=source,
                           **await check_packet(body, request=request, cache_key=packet_id))
        except Exception as error:
            payload.update(status="failed", latency_ms=round((perf_counter() - started) * 1000),
                           error=_error_summary(error))
            if not _is_provider_failure(error):
                _report_unexpected(error)
        _emit(payload)
    except Exception as error:
        _report_unexpected(error)
