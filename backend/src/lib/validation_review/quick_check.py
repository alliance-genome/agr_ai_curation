"""Shadow Decisions quick check beside supervisor review.

When a supervisor review pins a fresh review packet, the Decisions model answers
fixed questions about each record: is it within the request, is an identity
value missing from or contradicted by its quotes, is it outside this study's own
work, what should happen to it, and does each validator answer name the same
entity and species. The packet is asked twice at the same time, with records in
forward and in reversed order, because order shifts the answers. Code averages
the two orders, applies each calibrated rule to say which route every record
would take (skip review, remove, or supervisor review) and logs everything for
later calibration.

This is an observer. It changes no review state, packet content, reviewer
prompt or curator-visible result, and review never waits for it or depends on
it. A failed or refused check is logged and nothing else happens.

The model owns every judgment of meaning. Code only builds the input from the
pinned packet, averages and compares returned probabilities with fixed
thresholds, and decides from declared record type and role which records a rule
may remove.
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
FORWARD, REVERSED = "forward", "reversed"


@dataclass(frozen=True)
class Question:
    """One question asked about every record, or about every resolved validator answer."""

    key: str
    kind: str
    text: str
    # A yes answer counts against the record; rules compare 1 - p.
    negated: bool = False
    per_validator_answer: bool = False
    choices: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Rule:
    """A would-be route. Scores are order-averaged and oriented (1 - p for negated questions)."""

    name: str
    # A top-level object record is removed when its scope score is below this.
    remove_scope_below: float
    # Skip the supervisor when every listed score is at least its minimum; None never skips.
    skip_at_least: tuple[tuple[str, float], ...] | None


@dataclass(frozen=True)
class Definition:
    """Everything one calibration measured: wording, input format, orders and rules."""

    wording_id: str
    questions: tuple[Question, ...]
    orders: tuple[str, ...]
    scope_key: str
    validator_key: str
    rules: tuple[Rule, ...]
    request_max_chars: int
    quote_max_chars: int
    value_max_chars: int
    validator_detail_max_chars: int


# Texts are copied verbatim from the 2026-11-07 Decisions trial question bank
# (configs.py) and the rules from its final report. Changing any text, cap,
# order or threshold makes a new wording_id that needs its own calibration.
DEFINITION = Definition(
    wording_id="v6-shadow",
    questions=(
        Question("scope_lenient", "predicate", (
            "Record {r} could reasonably be part of what the curator's request asks for. Answer no only when it is "
            "clearly outside the request: a different kind of thing than requested, or a control, comparison or "
            "background item the request does not cover. If the request could fairly be read to include it, "
            "answer yes.")),
        Question("sup_neg", "predicate", (
            "Some identity value of record {r} (the entity it names, its type, or an identifier or source code it "
            "gives) is missing from its quoted evidence or contradicts it. Ignore rationale, notes and other "
            "interpretive text."), negated=True),
        Question("own_neg", "predicate", (
            "The evidence suggests record {r} was not part of this study's own experiments: it is only cited from "
            "earlier work, taken from another group's data, images or collections, or mentioned as background."),
            negated=True),
        Question("val_entity", "predicate", (
            "Validator answer {v} names the same entity that record {r} and its quoted evidence describe (the same "
            "gene, term, chemical or reagent, in the same species), rather than a different one."),
            per_validator_answer=True),
        Question("choice3", "choice", "What should happen to record {r} before it is saved for the curator?",
                 choices=(
                     {"value": "accept", "description": (
                         "Save it as-is: it is part of what the curator asked for, it comes from this study, and its "
                         "quoted evidence supports the entity, type and identifiers it gives.")},
                     {"value": "remove_out_of_scope", "description": (
                         "Drop it: it is clearly not what the curator asked for, such as a control, comparator, "
                         "background item or a different kind of thing.")},
                     {"value": "needs_review", "description": (
                         "A reviewer should look at the paper first: the evidence is thin, the identity or role is "
                         "unclear, or it may come from other work rather than this study.")},
                 )),
    ),
    orders=(FORWARD, REVERSED),
    scope_key="scope_lenient",
    validator_key="val_entity",
    rules=(
        Rule("removal_only", 0.5, None),
        Rule("balanced", 0.3, (("scope_lenient", 0.8), ("sup_neg", 0.8), ("own_neg", 0.9), ("val_entity", 0.8))),
        Rule("strict", 0.3, (("scope_lenient", 0.8), ("sup_neg", 0.8), ("own_neg", 0.9), ("val_entity", 0.8),
                             ("choice3:accept", 0.5))),
    ),
    request_max_chars=3000,
    quote_max_chars=1500,
    value_max_chars=600,
    validator_detail_max_chars=400,
)

# The platform's reference roles (as in evidence_summary.py): records that
# support another record rather than answer the request. They always stay with
# the supervisor. Every other role, or none, is a top-level record.
SUB_RECORD_ROLES = frozenset({"metadata_only", "supporting_reference", "validated_reference"})
# Claim records are top-level but never removed by a rule.
CLAIM_TYPES = ("generic_claim",)
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


def packet_pinned(context, page):
    """Start the shadow check for the packet a reviewer read just pinned; never raises or waits."""
    try:
        scope = _SCOPE.get()
        if scope is None:
            return
        from src.lib.openai_agents.config import get_validation_review_quick_check_mode

        if get_validation_review_quick_check_mode() != "shadow":
            return
        packet_id = page["packet_id"]
        if packet_id in scope.checked:
            return
        scope.checked.add(packet_id)
        task = asyncio.get_running_loop().create_task(_observe(context, packet_id, scope))
        _RUNNING.add(task)
        task.add_done_callback(_RUNNING.discard)
    except Exception as error:
        _report_unexpected(error)


def review_request(curator_request, execution_context, definition=DEFINITION):
    """The request text the input quotes, chosen as in the calibration trial.

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
    if len(text) > definition.request_max_chars:
        text = text[:definition.request_max_chars] + " [truncated]"
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


def record_kind(record):
    """object, claim or sub_record, from the record's declared role and type."""
    if record["role"] in SUB_RECORD_ROLES:
        return "sub_record"
    if record["type"] in CLAIM_TYPES or record["role"] in CLAIM_TYPES:
        return "claim"
    return "object"


def packet_records(body, definition=DEFINITION):
    """Each numbered packet record with its quotes and decidable validator items."""
    evidence = body["evidence"]
    records = []
    for item in body["records"]:
        if item["record"] is None:
            continue
        record = {
            "record": item["record"],
            "target_id": item.get("target_id"),
            "type": item.get("type"),
            "role": item.get("role"),
            "source_available": "source_unavailable" not in item,
            "values": {key: value for key, value in (item.get("values") or {}).items() if key != "class_key"},
            "evidence": [
                {"id": handle, "page": (evidence.get(handle) or {}).get("page"),
                 "section": (evidence.get(handle) or {}).get("section"),
                 "quote": (_quote(evidence, handle) or "")[:definition.quote_max_chars]}
                for handle in item.get("evidence") or []
            ],
            "validators": [
                {"validator": entry.get("validator"), "field": entry.get("field"),
                 "status": entry.get("status") or entry.get("outcome"),
                 "values": entry.get("values"), "explanation": entry.get("explanation")}
                for entry in item.get("review") or []
                if entry.get("review") != "record" and "handle" in entry
            ],
        }
        record["kind"] = record_kind(record)
        records.append(record)
    return records


def asked_records(records):
    """Records the model is asked about; a record without its saved source is not."""
    return [record for record in records if record["source_available"]]


def ordered(records, order):
    """Records in one call's order, each with the label it carries in that call."""
    sequence = records if order == FORWARD else list(reversed(records))
    return [(f"R{index + 1}", record) for index, record in enumerate(sequence)]


def _record_block(rid, record, definition):
    role = f", role {record['role']}" if record["role"] else ""
    lines = [f"Record {rid} ({record['type']}{role}):"]
    for key, value in record["values"].items():
        lines.append(f"  {key}: {_short(value, definition.value_max_chars)}")
    if not record["evidence"]:
        lines.append("  (no evidence quotes)")
    for item in record["evidence"]:
        lines.append(f"  evidence {item['id']} (page {item['page']}, {item['section'] or ''}): \"{item['quote']}\"")
    for index, validator in enumerate(record["validators"]):
        parts = [f"validator answer {rid}V{index + 1} ({validator['validator']}, field {validator['field']}): "
                 f"status {validator['status']}"]
        if validator["values"]:
            parts.append(f"resolved values {_short(validator['values'], definition.validator_detail_max_chars)}")
        if validator["explanation"]:
            parts.append(f"explanation {_short(validator['explanation'], definition.validator_detail_max_chars)}")
        lines.append("  " + "; ".join(parts))
    return "\n".join(lines)


def build_input(request, labelled, definition=DEFINITION):
    head = [
        "A curator asked an AI curation assistant this request about one publication:",
        f'"{request or "(no request text recorded)"}"',
        "",
        "The assistant extracted the records below. Each record lists its saved values, the exact sentences quoted "
        "from the paper as evidence, and, where a database validator was run, the validator's answer.",
        "",
    ]
    return "\n".join(head) + "\n" + "\n\n".join(
        _record_block(rid, record, definition) for rid, record in labelled)


def validator_indexes(record):
    """1-based positions of the validator items that carry a resolved answer to compare."""
    return [index + 1 for index, validator in enumerate(record["validators"])
            if validator["status"] not in UNANSWERED_VALIDATOR_STATES]


def record_questions(rid, record, definition=DEFINITION):
    """The questions for one record, each with what it asks about: (question, key, validator index)."""
    asked = []
    for question in definition.questions:
        if question.per_validator_answer:
            for index in validator_indexes(record):
                vid = f"{rid}V{index}"
                asked.append(({"type": "predicate", "name": f"{question.key}__{vid}",
                               "instructions": question.text.format(r=rid, v=vid)}, question.key, index))
            continue
        item = {"type": question.kind, "name": f"{question.key}__{rid}", "instructions": question.text.format(r=rid)}
        if question.kind == "choice":
            item["choices"] = [dict(choice) for choice in question.choices]
        asked.append((item, question.key, None))
    return asked


def chunks(questions):
    return [questions[start:start + MAX_QUESTIONS_PER_REQUEST]
            for start in range(0, len(questions), MAX_QUESTIONS_PER_REQUEST)]


def _mean(values):
    return sum(values) / len(values)


def oriented_scores(answers, record, definition=DEFINITION):
    """Order-averaged scores the rules compare, from each order's answers about one record.

    A score is present only when every order answered it. Negated questions are
    turned around (1 - p) so that, as in the trial's rules, higher is better.
    """
    scores = {}
    for question in definition.questions:
        if question.per_validator_answer:
            indexes = validator_indexes(record)
            per_answer = [[order_answers.get(question.key, {}).get(index) for order_answers in answers.values()]
                          for index in indexes]
            if indexes and all(None not in values for values in per_answer):
                scores[question.key] = min(_mean(values) for values in per_answer)
            continue
        values = [order_answers.get(question.key) for order_answers in answers.values()]
        if None in values:
            continue
        if question.kind == "choice":
            for choice in question.choices:
                scores[f"{question.key}:{choice['value']}"] = _mean([value[choice["value"]] for value in values])
        else:
            mean = _mean(values)
            scores[question.key] = 1 - mean if question.negated else mean
    return scores


def route(record, scores, rule, definition=DEFINITION):
    """The route one rule gives one record, and why when the reason is structural or missing answers."""
    if not record["source_available"]:
        return "sol", "source_unavailable"
    if record["kind"] == "sub_record":
        return "sol", "sub_record"
    scope = scores.get(definition.scope_key)
    if record["kind"] == "object" and scope is not None and scope < rule.remove_scope_below:
        return "remove", None
    if rule.skip_at_least is None:
        return "sol", "unanswered" if record["kind"] == "object" and scope is None else None
    needed = [(key, floor) for key, floor in rule.skip_at_least
              if not (key == definition.validator_key and not validator_indexes(record))]
    if any(key not in scores for key, _ in needed):
        return "sol", "unanswered"
    if all(scores[key] >= floor for key, floor in needed):
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


def _sum_usage(parts):
    if any(part is None for part in parts):
        return None
    return {key: sum(part[key] for part in parts) for key in parts[0]} if parts else None


@dataclass(frozen=True)
class OrderCall:
    """One order's request: its shared input and every question, with what each asks about."""

    order: str
    input: str
    # (question, packet record label, question key, validator index or None)
    asked: tuple


@dataclass(frozen=True)
class PreparedCheck:
    """Everything built from the pinned packet before any request is sent."""

    records: tuple
    calls: tuple[OrderCall, ...]


def prepare_check(body, *, request, definition=DEFINITION):
    """Records, inputs and questions for every order; no calls when no record can be asked."""
    records = packet_records(body, definition)
    asked = asked_records(records)
    calls = []
    if asked:
        for order in definition.orders:
            labelled = ordered(asked, order)
            calls.append(OrderCall(order, build_input(request, labelled, definition), tuple(
                (question, record["record"], key, index)
                for rid, record in labelled for question, key, index in record_questions(rid, record, definition))))
    return PreparedCheck(tuple(records), tuple(calls))


async def _ask_order(client, call, cache_key):
    """One order's call: every question about every record, chunked over one shared input."""
    started = perf_counter()
    payloads, requests = [], []
    for part in chunks([question for question, *_ in call.asked]):
        sent = perf_counter()
        payload = await client.post(DECISIONS_PATH, cast_to=object, body={
            "model": MODEL, "input": call.input, "questions": part,
            "prompt_cache_key": f"{cache_key}:{call.order}"})
        if not isinstance(payload, dict) or not isinstance(payload.get("answers"), list):
            raise QuickCheckResponseError("Decisions response has no answers list")
        payloads.append(payload)
        requests.append({"questions": len(part), "latency_ms": round((perf_counter() - sent) * 1000),
                         "usage": _usage([payload])})
    return {"order": call.order, "questions": len(call.asked), "requests": requests,
            "latency_ms": round((perf_counter() - started) * 1000), "usage": _usage(payloads)}, payloads


async def run_check(prepared, client, *, cache_key):
    """Send every order's call at the same time; each outcome is its result or its failure."""
    outcomes = await asyncio.gather(*(_ask_order(client, call, cache_key) for call in prepared.calls),
                                    return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
            raise outcome
    return outcomes


def _parse_answers(call, payloads):
    returned = {}
    for payload in payloads:
        for answer in payload["answers"]:
            if not isinstance(answer, dict) or not isinstance(answer.get("name"), str):
                raise QuickCheckResponseError("Decisions answer has no name")
            returned[answer["name"]] = answer
    answers, refused, missing = {}, [], []
    for question, label, key, index in call.asked:
        answer = returned.get(question["name"])
        unanswered = {"order": call.order, "record": label, "question": key, "validator": index}
        if answer is None:
            missing.append(unanswered)
            continue
        if answer.get("type") == "refusal":
            refused.append(unanswered)
            continue
        slot = answers.setdefault(label, {})
        if question["type"] == "choice":
            probabilities = {item.get("value"): item.get("probability") for item in answer.get("probabilities") or []}
            if not all(isinstance(probabilities.get(choice["value"]), (int, float)) for choice in question["choices"]):
                raise QuickCheckResponseError("Decisions choice answer has no probability for every choice")
            slot[key] = {choice["value"]: float(probabilities[choice["value"]]) for choice in question["choices"]}
        elif isinstance(answer.get("probability"), (int, float)):
            if index is None:
                slot[key] = float(answer["probability"])
            else:
                slot.setdefault(key, {})[index] = float(answer["probability"])
        else:
            raise QuickCheckResponseError("Decisions predicate answer has no probability")
    return answers, refused, missing


def _logged(order_answers, record):
    """One order's answers about one record, with validator answers named as in the packet."""
    view = {}
    for key, value in order_answers.items():
        if isinstance(value, dict) and all(isinstance(index, int) for index in value):
            view[key] = {f"{record['record']}V{index}": probability for index, probability in value.items()}
        else:
            view[key] = value
    return view


def _rounded(scores):
    return {key: round(value, 6) for key, value in scores.items()}


def _rules(definition):
    return {rule.name: {"remove_scope_below": rule.remove_scope_below,
                        "skip_at_least": dict(rule.skip_at_least) if rule.skip_at_least else None}
            for rule in definition.rules}


def summarize(prepared, outcomes, definition=DEFINITION):
    """Average the orders, apply every rule and shape the logged result.

    A failed order keeps the other order's call and answers in the result, but
    the check is failed and no record gets an averaged score or a skip or
    removal.
    """
    calls, by_order, refused, missing, failures = [], {}, [], [], []
    for call, outcome in zip(prepared.calls, outcomes):
        if isinstance(outcome, Exception):
            failures.append(outcome)
            calls.append({"order": call.order, "questions": len(call.asked), "error": _error_summary(outcome)})
            continue
        summary, payloads = outcome
        try:
            answers, order_refused, order_missing = _parse_answers(call, payloads)
        except QuickCheckResponseError as error:
            failures.append(error)
            calls.append({**summary, "error": _error_summary(error)})
            continue
        calls.append(summary)
        by_order[call.order] = answers
        refused += order_refused
        missing += order_missing
    rows = []
    routes = {rule.name: {"skip": 0, "remove": 0, "sol": 0} for rule in definition.rules}
    for record in prepared.records:
        label = record["record"]
        answers = {order: by_order[order].get(label, {}) for order in by_order}
        scores = (oriented_scores(answers, record, definition)
                  if record["source_available"] and not failures else {})
        row = {"record": label, "target_id": record["target_id"], "type": record["type"], "role": record["role"],
               "kind": record["kind"], "routes": {}}
        reasons = {}
        for rule in definition.rules:
            chosen, reason = route(record, scores, rule, definition)
            routes[rule.name][chosen] += 1
            row["routes"][rule.name] = chosen
            if reason is not None:
                reasons[rule.name] = reason
        if reasons:
            row["route_reasons"] = reasons
        if record["source_available"]:
            for order in by_order:
                row[order] = _logged(answers[order], record)
            if not failures:
                row["average"] = _rounded(scores)
        rows.append(row)
    result = {
        "status": "failed" if failures else "partial" if refused or missing else "completed",
        "record_count": len(prepared.records),
        "asked_record_count": sum(1 for record in prepared.records if record["source_available"]),
        "rules": _rules(definition),
        "calls": calls,
        "usage": _sum_usage([call["usage"] for call in calls if "usage" in call]),
        "routes": routes,
        "records": rows,
        "refused": refused,
        "missing": missing,
    }
    if failures:
        result["error"] = _error_summary(failures[0])
    return result


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
        logger.warning(
            "Validation review quick check failed",
            extra={"event": "review_quick_check_failed", "error_type": type(error).__name__,
                   "sentry_skip_event": True},
        )
    except Exception:
        pass  # Reporting a shadow observer's failure must never reach review.


_LOG_RECORD_FIELDS = ("record", "kind", "routes", "route_reasons", "average")


def _emit(payload):
    """One trace event (durable file and Langfuse) and one structured log line.

    The trace event keeps both orders' answers. The log line keeps each
    record's routes and averaged scores, so a large packet stays one
    reasonably sized line. Neither carries quotes, values or request text.
    """
    from src.lib.openai_agents.extraction_trace_events import write_extraction_trace_event

    write_extraction_trace_event(event_type=EVENT_TYPE, output_summary=payload)
    line = {**payload}
    if "records" in payload:
        line["records"] = [{key: row[key] for key in _LOG_RECORD_FIELDS if key in row} for row in payload["records"]]
    log = logger.info if payload["status"] in ("completed", "no_questions") else logger.warning
    log("Validation review quick check " + payload["status"],
        extra={"event": "review_quick_check", "quick_check": line, "sentry_skip_event": True})


def _prepare(context, packet_id, scope):
    """Off the event loop: read the pinned packet, then build every call and the client."""
    body = _load_packet(context.history_scope, context.episode_id, packet_id)
    request, source = review_request(scope.curator_request, scope.record.metadata.get("execution_context"))
    prepared = prepare_check(body, request=request)
    return source, prepared, _decisions_client() if prepared.calls else None


def _log_cancelled(packet_id):
    try:
        logger.warning("Validation review quick check cancelled", extra={
            "event": "review_quick_check",
            "quick_check": {"packet_id": packet_id, "wording_id": DEFINITION.wording_id, "status": "cancelled"},
            "sentry_skip_event": True})
    except Exception:
        pass  # Best effort while the task is being cancelled.


async def _observe(context, packet_id, scope):
    """Run one check to completion; a review that ends first does not stop it."""
    started = perf_counter()
    payload = {"packet_id": packet_id, "wording_id": DEFINITION.wording_id, "model": MODEL, "mode": "shadow"}
    try:
        from .provider import ACTIVE_BUDGET

        # This task runs in its own copy of the reviewer's context. Clearing the
        # review budget there keeps the check from ever being charged to the review.
        ACTIVE_BUDGET.set(None)
        payload["episode_id"] = context.episode_id
        try:
            source, prepared, client = await asyncio.to_thread(_prepare, context, packet_id, scope)
            payload["request_source"] = source
            if client is None:
                payload.update(status="no_questions", record_count=len(prepared.records), calls=[])
            else:
                async with client:
                    outcomes = await run_check(prepared, client, cache_key=packet_id)
                payload.update(await asyncio.to_thread(summarize, prepared, outcomes))
                for outcome in outcomes:
                    if isinstance(outcome, Exception) and not _is_provider_failure(outcome):
                        _report_unexpected(outcome)
        except Exception as error:
            payload.update(status="failed", error=_error_summary(error))
            if not _is_provider_failure(error):
                _report_unexpected(error)
        payload["latency_ms"] = round((perf_counter() - started) * 1000)
        await asyncio.to_thread(_emit, payload)
    except asyncio.CancelledError:
        _log_cancelled(packet_id)
        raise
    except Exception as error:
        _report_unexpected(error)
