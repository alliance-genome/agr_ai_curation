"""Fan-out builder extraction: plan, parallel writers, a tidy pass, then code finalize.

``EXTRACTION_FANOUT_MIN_ITEMS`` turns this on for builder agents (0, the default,
keeps today's single writer). Mechanics stay in code and meaning stays with the
model (AGENTS.md section 8):

- The planner is the agent itself. It looks at its record-type catalog first when it
  has one, reads the paper and submits a plan through a strict-schema tool: a label
  and the evidence chunks for each record, plus a few paper-wide notes. Staging is
  refused while it plans. What the plan holds is the model's call: only records that
  answer the request, with contrasts and exclusions left to its final explanation.
- Code counts the plan. No records: the planner explains the empty result. Fewer
  records than the fan-out threshold: the planner's own thread stages them. Otherwise
  code slices the plan in order and starts writers: agent clones with four tools,
  each with its own provider connection, a developer-role assignment, and the cited
  chunks prefetched through the agent's own ``read_chunk`` tool. On native OpenAI,
  writers send ``store=false`` unless ``EXTRACTION_FANOUT_WRITER_STORE_RESPONSES`` is true.
- Writers end themselves with a final reply. Code counts what each writer staged;
  it never matches labels or merges records.
- A writer whose run fails with a failure worth repeating (the typed curator-failure
  classification marks it retryable) has everything that attempt alone produced
  removed and runs again from its assignment, ``EXTRACTION_FANOUT_WRITER_RERUNS`` times
  (default 1), while the other writers keep going. This is ownership bookkeeping: code
  never judges a record. A failure that is not retryable, a cancelled writer, or a
  writer with no re-run left fails the step with the writer's first error.
- The planner thread then checks the staged listing (the tidy pass). Missing records,
  duplicates and wrong details are the model's call, including for any writer that
  staged fewer records than it was assigned.
- Code finalizes every retained record through the agent's own finalize tool.
  Validation problems go back to the planner within its correction budget. If finalization still
  fails, the step fails exactly as a single-writer run does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from copy import copy
from typing import Any, Callable, Sequence

from agents import FunctionTool, ItemHelpers, Runner
from agents.agent import ToolsToFinalOutputResult
from agents.models.openai_responses import ResponsesWebSocketError
from agents.stream_events import RunItemStreamEvent
from agents.tool_context import ToolContext
from openai.types.responses import ResponseFunctionToolCall

from agr_ai_curation_runtime.document_errors import DocumentReadError, files_changed_answer
from src.lib.context import get_current_user_id
from src.lib.document_files.readable import ReadableFile, ReadableFiles
from src.lib.observability import runtime
from src.lib.observability.sentry import exception_chain_already_captured
from src.lib.packages import tool_roles

from . import config
from .curator_errors import classify_failure
from .extraction_builder_workspace import (
    CANDIDATE_STATUS_DISCARDED,
    ExtractionBuilderWorkspace,
    reset_staging_owner,
    set_staging_owner,
)
from .evidence_summary import build_record_evidence_summary_record
from .extraction_trace_events import write_extraction_trace_event
from .model_request_measurement import install_model_request_measurement
from .run_history import exact_json
from .tools.evidence_workspace import find_evidence_record_in_records, remove_evidence_records
from .tool_surface import (
    ToolSurface,
    _agent_model_and_provider,
    apply_tool_surface,
    tool_surface_trace_attributes,
)

logger = logging.getLogger(__name__)

# The planner thread and the writers measure every model request (ALL-1279).
install_model_request_measurement()

SUBMIT_PLAN_TOOL_NAME = "submit_extraction_plan"
# Recorded tool times on replayed writer items; the specialist stream loop reads them.
REPLAYED_TOOL_STARTED_AT = "fanout_tool_started_at"
REPLAYED_TOOL_COMPLETED_AT = "fanout_tool_completed_at"
PREFETCH_TOOL_NAME = "read_chunk"
# Source tools a writer keeps besides the agent's stage tools.
WRITER_SOURCE_TOOL_NAMES = frozenset({"read_chunk", "search_document", "record_evidence"})
# Fixed reason recorded when a failed writer attempt's records are removed (ALL-1558).
WRITER_RERUN_CLEANUP_REASON = "extraction_fanout_writer_rerun_cleanup"
# Owner key for evidence already in the run's list before any writer recorded it.
_EARLIER_EVIDENCE_OWNER = (0, 0)

PLANNING_READ_FIRST = (
    "[Planning phase] Read what you need from the paper first, using the reading and "
    "search tools."
)
PLANNING_CATALOG_FIRST = (
    "[Planning phase] First call {tools} to see the record types you can stage, so "
    "you plan each item as one record of the type that fits. Then read what you need "
    "from the paper, using the reading and search tools."
)
PLANNING_INSTRUCTIONS = (
    " In this phase staging and finalizing are disabled, and you do not need to "
    "record evidence. When you have read enough, call submit_extraction_plan once, "
    "with:\n"
    "- notes: up to 5 short lines of paper-wide facts that writers need to fill fields "
    "correctly under your instructions (for example which items the study itself "
    "generated, where items came from, or naming conventions). No per-record field "
    "values.\n"
    "- items: one entry per record you would stage, in order, each with a short label "
    "that identifies the record and the chunk_index values of the passages that hold "
    "its evidence.\n"
    "Plan only records that answer the request under your instructions, include every "
    "one of them, and nothing else. Anything you will name only as a contrast or an "
    "exclusion, such as a control or other item outside the requested category, goes in "
    "your final explanation, not in items, even when you are asked to distinguish it. "
    "An item inside the requested category is planned even when the paper uses it as a "
    "control. Parallel writers will read the cited chunks and fill in every field. If your "
    "instructions call for no records from this paper, submit an empty items list."
)


def planning_message(catalog_tool_names: Sequence[str]) -> str:
    """The planning-phase message; a planner with a record-type catalog reads it first.

    Which tools are catalogs comes from package binding metadata
    (``builder_catalog: true``), never from the tool's name.
    """

    if not catalog_tool_names:
        return PLANNING_READ_FIRST + PLANNING_INSTRUCTIONS
    tools = " and ".join(catalog_tool_names)
    return PLANNING_CATALOG_FIRST.format(tools=tools) + PLANNING_INSTRUCTIONS


PLAN_REMINDER_MESSAGE = (
    "[Planning phase] You have not submitted a plan. Call submit_extraction_plan now "
    "with your notes and one item per record (an empty items list if there are none)."
)
NO_RECORDS_MESSAGE = (
    "[Plan received] Your plan lists no records, so nothing will be staged. Reply with "
    "your final explanation of the empty result and the exclusions, as your "
    "instructions describe. Do not stage or finalize; the system records the result."
)
CHECK_INTRO = (
    "[Check phase] Writers have staged the records below from your plan. Compare each "
    "against what you read and your instructions, and fix only clear errors or "
    "omissions, for example a missing or incomplete source, citation or identifier, a "
    "wrong category, or a missing name. Discard duplicates and stage any planned record "
    "that is missing. Make every fix in one response: call the correction and stage "
    "tools in parallel, one call per record that needs a change, rather than one record "
    "per turn. Do not "
    "review evidence records, do not list or find staged records (this listing is "
    "complete), and do not finalize; the system finalizes. Do not rewrite records that "
    "are already correct. Then reply with your final explanation (what was extracted, "
    "exclusions and uncertainty), as your instructions describe."
)
FINALIZE_REFUSAL = (
    "The system finalizes this extraction after you reply, so this tool is not "
    "available now. Finish your current step, then reply."
)
PLANNING_CLOSED = (
    "Planning is finished, so this tool is not available now. Continue with the "
    "current step."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", None) or "").strip()


def _records(count: int) -> str:
    return "1 record" if count == 1 else f"{count} records"


# ---------------------------------------------------------------------------
# Plan contract
# ---------------------------------------------------------------------------


class PlanFormatError(ValueError):
    """A submitted plan does not match the tool's structural contract."""


@dataclass(frozen=True)
class PlanItem:
    label: str
    chunks: tuple[int, ...]


@dataclass(frozen=True)
class ExtractionPlan:
    items: tuple[PlanItem, ...]
    notes: tuple[str, ...]


def submit_plan_parameters_schema() -> dict[str, Any]:
    """Strict JSON schema for ``submit_extraction_plan``; no field values, only a plan."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["notes", "items"],
        "properties": {
            "notes": {
                "type": "array",
                "description": (
                    "Up to 5 short lines of paper-wide facts that writers need to fill "
                    "fields correctly under your instructions. No per-record field values."
                ),
                "items": {"type": "string"},
            },
            "items": {
                "type": "array",
                "description": (
                    "One entry per record to stage, in order. Empty when your "
                    "instructions call for no records from this paper."
                ),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", "chunks"],
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Short label that identifies the record.",
                        },
                        "chunks": {
                            "type": "array",
                            "description": (
                                "chunk_index values of the passages that hold this "
                                "record's evidence."
                            ),
                            "items": {"type": "integer"},
                        },
                    },
                },
            },
        },
    }


SUBMIT_PLAN_DESCRIPTION = (
    "Planning phase only. Submit the records to stage: a short identifying label and the "
    "evidence chunk indexes for each record, plus up to 5 short paper-wide notes. Give no "
    "field values; parallel writers fill every field from the cited chunks."
)


def parse_plan_arguments(arguments: str) -> ExtractionPlan:
    """Check the plan's structure only; labels and notes are the model's words."""

    try:
        payload = json.loads(arguments or "")
    except (TypeError, ValueError) as exc:
        raise PlanFormatError("The plan arguments are not valid JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != {"notes", "items"}:
        raise PlanFormatError("The plan must have exactly the fields notes and items.")
    notes, items = payload["notes"], payload["items"]
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        raise PlanFormatError("notes must be a list of strings.")
    if not isinstance(items, list):
        raise PlanFormatError("items must be a list.")
    parsed: list[PlanItem] = []
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict) or set(item) != {"label", "chunks"}:
            raise PlanFormatError(f"Item {position} must have exactly the fields label and chunks.")
        label, chunks = item["label"], item["chunks"]
        if not isinstance(label, str) or not label.strip():
            raise PlanFormatError(f"Item {position} needs a non-empty label.")
        if not isinstance(chunks, list) or not all(
            type(chunk) is int and chunk >= 0 for chunk in chunks
        ):
            raise PlanFormatError(
                f"Item {position} chunks must be a list of non-negative chunk_index integers."
            )
        parsed.append(PlanItem(label=label.strip(), chunks=tuple(dict.fromkeys(chunks))))
    return ExtractionPlan(
        items=tuple(parsed),
        notes=tuple(note.strip() for note in notes if note.strip()),
    )


def writer_slices(
    item_count: int,
    *,
    items_per_writer: int,
    max_writers: int,
) -> list[tuple[int, ...]]:
    """Contiguous plan positions per writer, in plan order, sizes differing by at most one."""

    if item_count <= 0:
        return []
    writer_count = min(max_writers, math.ceil(item_count / items_per_writer))
    base, extra = divmod(item_count, writer_count)
    slices: list[tuple[int, ...]] = []
    start = 0
    for index in range(writer_count):
        size = base + (1 if index < extra else 0)
        slices.append(tuple(range(start, start + size)))
        start += size
    return slices


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriterOutcome:
    index: int
    positions: tuple[int, ...]
    staged_candidate_ids: tuple[str, ...]
    final_text: str | None
    new_items: tuple[Any, ...]

    @property
    def short(self) -> bool:
        return len(self.staged_candidate_ids) < len(self.positions)


_REPLAY_EVENT_NAMES = {
    "tool_call_item": "tool_called",
    "tool_call_output_item": "tool_output",
    "reasoning_item": "reasoning_item_created",
    "message_output_item": "message_output_created",
}


def _replay_event(item: Any, timings: dict[str, tuple[datetime, datetime]]) -> RunItemStreamEvent | None:
    item_type = getattr(item, "type", None)
    name = _REPLAY_EVENT_NAMES.get(item_type)
    if name is None:
        return None
    timing = timings.get(getattr(item, "call_id", None) or "")
    if timing is not None:
        if item_type == "tool_call_item":
            setattr(item, REPLAYED_TOOL_STARTED_AT, timing[0])
        elif item_type == "tool_call_output_item":
            setattr(item, REPLAYED_TOOL_COMPLETED_AT, timing[1])
    return RunItemStreamEvent(name=name, item=item)


def _writer_error_fields(error: BaseException) -> dict[str, Any]:
    """A writer failure's exception type, typed provider fields and curator-failure category.

    Never the message: only structured fields reach the trace.
    """

    fields: dict[str, Any] = {"error_type": type(error).__name__,
                              "provider_error_type": None, "provider_error_code": None}
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ResponsesWebSocketError):
            fields.update(provider_error_type=current.error_type, provider_error_code=current.code)
            break
        current = current.__cause__ or current.__context__
    fields["failure_category"] = classify_failure(error).category
    return fields


def _staging_owner(index: int, attempt: int) -> str:
    """One staging owner per writer attempt, so a removed attempt can be closed."""

    return f"writer-{index}/a{attempt}"


def prepare_fanout_extraction(
    runtime_agent: Any,
    *,
    builder_workspace: ExtractionBuilderWorkspace,
    specialist_name: str,
    input_text: str,
    emit_event: Callable[[dict[str, Any]], None],
    record_live_evidence: Callable[[dict[str, Any]], None],
    evidence_records: list[dict[str, Any]],
) -> "FanoutExtraction | None":
    """Return the fan-out run for a builder agent, or None when it does not apply.

    Call after run-state binding and before the tool surface is compiled. When fan-out
    applies, the agent gains the plan tool, its finalize tool answers that the system
    finalizes, and its tool-use behavior ends the planning run once a plan is accepted.
    """

    min_items = config.get_extraction_fanout_min_items()
    if min_items <= 0:
        return None
    tools = list(getattr(runtime_agent, "tools", None) or [])
    names = [_tool_name(tool) for tool in tools]
    finalize_names = tool_roles.builder_finalization_tool_names()
    stage_names = tool_roles.builder_stage_tool_names()
    catalog_names = tool_roles.builder_catalog_tool_names()
    finalize_tools = [tool for tool in tools if _tool_name(tool) in finalize_names]
    stage_tools = [tool for tool in tools if _tool_name(tool) in stage_names]
    if len(finalize_tools) != 1 or not stage_tools:
        return _not_applicable(
            builder_workspace, specialist_name, reason="builder_tools",
            finalize_tools=[_tool_name(tool) for tool in finalize_tools],
            stage_tools=[_tool_name(tool) for tool in stage_tools],
        )
    # The document and owner the agent's document tools were built for; plan chunks
    # are checked against this document when the plan is submitted.
    scope = getattr(runtime_agent, "cost_boundary", None) or {}
    document_id, user_id = scope.get("document_id"), scope.get("user_id")
    if not document_id or not user_id:
        return _not_applicable(builder_workspace, specialist_name, reason="no_document_scope")
    if SUBMIT_PLAN_TOOL_NAME in names:
        raise ValueError(f"Agent tool collides with the reserved {SUBMIT_PLAN_TOOL_NAME} tool")
    fanout = FanoutExtraction(
        workspace=builder_workspace,
        specialist_name=specialist_name,
        input_text=input_text,
        plain_tools=tools,
        finalize_tool=finalize_tools[0],
        stage_tools=stage_tools,
        catalog_tool_names=[name for name in names if name in catalog_names],
        document_id=str(document_id),
        user_id=str(user_id),
        min_items=min_items,
        items_per_writer=config.get_extraction_fanout_items_per_writer(),
        max_writers=config.get_extraction_fanout_max_writers(),
        writer_reruns=config.get_extraction_fanout_writer_reruns(),
        writer_store_responses=config.get_extraction_fanout_writer_store_responses(),
        emit_event=emit_event,
        record_live_evidence=record_live_evidence,
        evidence_records=evidence_records,
    )
    runtime_agent.tools = [
        fanout.refused_finalize_tool if tool is fanout.finalize_tool else tool for tool in tools
    ] + [fanout.submit_tool]
    runtime_agent.tool_use_behavior = fanout.tool_use_behavior
    return fanout


def _not_applicable(
    builder_workspace: ExtractionBuilderWorkspace,
    specialist_name: str,
    *,
    reason: str,
    **detail: Any,
) -> None:
    logger.info(
        "Fan-out extraction does not apply to %s (%s: %s)",
        specialist_name,
        reason,
        detail,
        extra={"specialist_name": specialist_name, "operation": "extraction_fanout_not_applicable"},
    )
    write_extraction_trace_event(
        event_type="extraction_fanout.not_applicable",
        output_summary={"reason": reason, **detail},
        metadata={"agent": specialist_name, "builder_run_id": builder_workspace.run_id},
    )
    return None


def _report_discarded_writer_failure(error: Exception, *, message: str, operation: str) -> None:
    try:
        if exception_chain_already_captured(error):
            return
        if runtime.report_runtime_exception(
            runtime.sanitized_runtime_error(message),
            component="extraction_fanout",
            operation=operation,
            context={"type": type(error).__name__},
            include_request=False,
        ):
            setattr(error, "_ai_curation_sentry_captured", True)
    except Exception:
        logger.warning("Could not report fan-out writer failure", extra={"sentry_skip_event": True})


class FanoutExtraction:
    """One fan-out extraction run, consumed by the specialist stream loop.

    ``stream_events`` yields the planner thread's live SDK events and, once the
    writers finish, their recorded tool items, so the existing loop audits every
    call exactly as it does for one writer. ``final_output`` is the planner thread's
    last reply, which is the specialist's explanation.
    """

    def __init__(
        self,
        *,
        workspace: ExtractionBuilderWorkspace,
        specialist_name: str,
        input_text: str,
        plain_tools: Sequence[Any],
        finalize_tool: Any,
        stage_tools: Sequence[Any],
        catalog_tool_names: Sequence[str],
        document_id: str,
        user_id: str,
        min_items: int,
        items_per_writer: int,
        max_writers: int,
        writer_reruns: int,
        writer_store_responses: bool,
        emit_event: Callable[[dict[str, Any]], None],
        record_live_evidence: Callable[[dict[str, Any]], None],
        evidence_records: list[dict[str, Any]],
    ) -> None:
        self.workspace = workspace
        self.document_id = document_id
        self.user_id = user_id
        self.specialist_name = specialist_name
        self.input_text = input_text
        self.plain_tools = list(plain_tools)
        self.finalize_tool = finalize_tool
        self.stage_tools = list(stage_tools)
        self.catalog_tool_names = tuple(catalog_tool_names)
        self.min_items = min_items
        self.items_per_writer = items_per_writer
        self.max_writers = max_writers
        self.writer_reruns = writer_reruns
        self.writer_store_responses = writer_store_responses
        # Whether writers send store=false; set in start() from the planner's provider.
        self.writer_store_off = False
        self.emit_event = emit_event
        self.record_live_evidence = record_live_evidence
        # The run's evidence list, which record_live_evidence adds to.
        self.evidence_records = evidence_records
        self.read_tool = next(
            (tool for tool in self.plain_tools if _tool_name(tool) == PREFETCH_TOOL_NAME), None
        )
        self.plan: ExtractionPlan | None = None
        self.phase = "planning"
        self.final_output: Any = None
        self.new_items: list[Any] = []
        self.writer_outcomes: list[WriterOutcome] = []
        self._planner_result: Any = None
        self._chunk_views: dict[tuple[int, tuple[str, int, int]], asyncio.Task[str]] = {}
        # Writers' tool start and end times by call ID, for the replayed audit.
        self._tool_timings: dict[str, tuple[datetime, datetime]] = {}
        # Ownership bookkeeping for writer re-runs, by (writer, attempt): the tool
        # call IDs of each attempt, and every attempt that recorded each evidence ID
        # (identical evidence gets the identical ID, so one record can have several).
        self._attempt_call_ids: dict[tuple[int, int], list[str]] = {}
        self._evidence_recorders: dict[str, set[tuple[int, int]]] = {}
        self._finalize_created: set[str] = set()
        # Every writer attempt's history invocation, discarded re-runs included: their
        # reads reached a model, so they count toward the result's files read (ALL-1460).
        # Held in memory for this run only; a writer built without a history makes
        # the writers' reads unknowable, so the result records no files read.
        self.writer_invocation_ids: list[str] = []
        self.writer_invocations_unknown = False
        self.submit_tool = FunctionTool(
            name=SUBMIT_PLAN_TOOL_NAME,
            description=SUBMIT_PLAN_DESCRIPTION,
            params_json_schema=submit_plan_parameters_schema(),
            on_invoke_tool=self._submit_plan,
            strict_json_schema=True,
        )

        async def refuse_finalize(_ctx: Any, _arguments: str) -> str:
            return FINALIZE_REFUSAL

        self.refused_finalize_tool = copy(finalize_tool)
        self.refused_finalize_tool.on_invoke_tool = refuse_finalize

    # -- wiring ---------------------------------------------------------------

    def start(
        self,
        *,
        planner_agent: Any,
        max_turns: int,
        run_config: Any,
        base_run_config: Any,
        surface: ToolSurface,
    ) -> "FanoutExtraction":
        """Bind the compiled planner agent and run configs; streaming starts lazily."""

        # The check pass fixes several records in one turn and writers stage all their
        # items in one turn, so the planner thread and its writer clones allow parallel
        # tool calls wherever the provider's capability gates permit them.
        settings = planner_agent.model_settings
        model_id, provider_id = _agent_model_and_provider(planner_agent)
        if settings.parallel_tool_calls is not True:
            if model_id and config.provider_allows_parallel_tool_calls(model_id, provider_id):
                planner_agent.model_settings = replace(settings, parallel_tool_calls=True)
        # Writers ask native OpenAI not to keep their responses unless the setting
        # keeps them; store is an OpenAI request field, so other providers never get it.
        self.writer_store_off = bool(
            not self.writer_store_responses
            and model_id
            and config.provider_is_openai_native(model_id, provider_id)
        )
        self.planner_agent = planner_agent
        self.max_turns = max_turns
        self.run_config = run_config
        self.base_run_config = base_run_config
        self.surface = surface
        self.base_instructions = getattr(
            planner_agent, "tool_surface_base_instructions", planner_agent.instructions
        )
        return self

    def to_input_list(self) -> list[Any]:
        if self._planner_result is None:
            return [{"role": "user", "content": self.input_text}]
        return self._planner_result.to_input_list()

    async def _submit_plan(self, _ctx: Any, arguments: str) -> str:
        if self.phase != "planning" or self.plan is not None:
            return PLANNING_CLOSED
        try:
            plan = parse_plan_arguments(arguments)
        except PlanFormatError as exc:
            return json.dumps({"status": "rejected", "message": str(exc)})
        cited = sorted({chunk for item in plan.items for chunk in item.chunks})
        if cited:
            # Structural reference check: every cited chunk must exist in a readable file
            # of this document. Query failures raise and fail the step; only absent
            # indexes are rejected.
            from src.lib.document_files.readable import resolve_readable_files
            from src.lib.weaviate_client.chunks import get_existing_chunk_indexes

            readable = await asyncio.to_thread(resolve_readable_files, self.document_id, self.user_id)
            existing = await get_existing_chunk_indexes(
                chunk_indexes=cited, user_id=self.user_id, document_id=self.document_id,
                readable=readable,
            )
            missing = [chunk for chunk in cited if chunk not in existing]
            if missing:
                return json.dumps({
                    "status": "rejected",
                    "missing_chunk_indexes": missing,
                    "message": (
                        "These chunk_index values do not exist in this document: "
                        f"{', '.join(str(chunk) for chunk in missing)}. Cite the chunk_index "
                        "values of passages you read, then submit the corrected plan."
                    ),
                })
        self.plan = plan
        return json.dumps({"status": "accepted", "planned_records": len(plan.items)})

    def tool_use_behavior(self, _context: Any, tool_results: list[Any]) -> ToolsToFinalOutputResult:
        """End the planning run once a plan is accepted; otherwise keep going."""

        if self.phase == "planning" and self.plan is not None:
            for result in tool_results:
                if _tool_name(getattr(result, "tool", None)) == SUBMIT_PLAN_TOOL_NAME:
                    return ToolsToFinalOutputResult(is_final_output=True, final_output=str(result.output))
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)

    # -- events -----------------------------------------------------------------

    def _progress(self, message: str) -> None:
        self.emit_event({
            "type": "AGENT_GENERATING",
            "timestamp": _now_iso(),
            "details": {
                "agentRole": self.specialist_name,
                "agentDisplayName": self.specialist_name,
                "message": message,
                "extractionPhase": self.phase,
            },
        })

    def _trace(self, event_type: str, summary: dict[str, Any]) -> None:
        write_extraction_trace_event(
            event_type=f"extraction_fanout.{event_type}",
            output_summary=summary,
            metadata={
                "agent": self.specialist_name,
                "builder_run_id": self.workspace.run_id,
                "builder_invocation_id": self.workspace.builder_invocation_id,
                "phase": self.phase,
            },
        )

    # -- run --------------------------------------------------------------------

    async def stream_events(self):
        self._progress("Reading the paper and planning the records")
        self.workspace.planning = True
        try:
            async for event in self._run_planner_thread(planning_message(self.catalog_tool_names)):
                yield event
            for _ in range(config.get_extraction_fanout_max_plan_reminders()):
                if self.plan is not None:
                    break
                async for event in self._run_planner_thread(PLAN_REMINDER_MESSAGE):
                    yield event
        finally:
            self.workspace.planning = False
        if self.plan is None:
            raise self._plan_missing_error()

        count = len(self.plan.items)
        slices = (
            writer_slices(count, items_per_writer=self.items_per_writer, max_writers=self.max_writers)
            if count >= self.min_items
            else []
        )
        path = "no_records" if count == 0 else ("writers" if slices else "planner_stages")
        self._trace("plan_submitted", {
            "path": path,
            "planned_records": count,
            "writer_count": len(slices),
            "min_items": self.min_items,
            "items_per_writer": self.items_per_writer,
            "max_writers": self.max_writers,
            "writer_reruns": self.writer_reruns,
            "notes": list(self.plan.notes),
            "items": [{"label": item.label, "chunks": list(item.chunks)} for item in self.plan.items],
        })

        if path == "no_records":
            self.phase = "explaining"
            # The plan holds no records, so staging stays closed.
            self.workspace.planning = True
            try:
                async for event in self._run_planner_thread(NO_RECORDS_MESSAGE):
                    yield event
            finally:
                self.workspace.planning = False
        elif path == "planner_stages":
            self.phase = "staging"
            self._progress(f"Writing {_records(count)}")
            async for event in self._run_planner_thread(self._stage_message(count)):
                yield event
        else:
            self.phase = "writing"
            self._progress(f"Writing {count} records in {len(slices)} parallel batches")
            self.writer_outcomes = await self._run_writers(slices)
            for outcome in self.writer_outcomes:
                self.new_items.extend(outcome.new_items)
                for item in outcome.new_items:
                    event = _replay_event(item, self._tool_timings)
                    if event is not None:
                        yield event
            self.phase = "checking"
            staged = sum(
                1 for candidate in self.workspace.candidates.values()
                if candidate.status != CANDIDATE_STATUS_DISCARDED
            )
            self._progress(
                "Checking the written record" if staged == 1 else f"Checking the {staged} written records"
            )
            async for event in self._run_planner_thread(self._check_message(self.writer_outcomes)):
                yield event

        async for event in self._finalize():
            yield event

    async def _run_planner_thread(self, message: str):
        """Run or resume the planner's own thread with one more user message."""

        if self._planner_result is None:
            run_input: list[Any] = [{"role": "user", "content": self.input_text}]
        else:
            run_input = list(self._planner_result.to_input_list())
        run_input.append({"role": "user", "content": message})
        with tool_surface_trace_attributes(self.surface):
            result = Runner.run_streamed(
                self.planner_agent,
                input=run_input,
                max_turns=self.max_turns,
                run_config=self.run_config,
            )
        self._planner_result = result
        try:
            async for event in result.stream_events():
                yield event
        finally:
            if not result.is_complete:
                result.cancel()
        self.final_output = result.final_output
        self.new_items.extend(result.new_items)

    def _stage_message(self, count: int) -> str:
        return (
            f"[Writing phase] Your plan has {_records(count)}. Staging is now enabled. For "
            "this step these rules override the evidence-review and extraction-completion "
            "steps in your instructions. Stage exactly the records in your plan, following "
            "all of your instructions for field values, sources and evidence: record the "
            "evidence for all of them in one turn, from the chunks you already read in this "
            "conversation (read again only a passage you have not read), then stage all of "
            "them in the next turn. Do not review evidence records, do not list or find "
            "staged records, and do not finalize; the system finalizes after you reply. When "
            "every planned record is staged, reply with your final explanation (what was "
            "extracted, exclusions and uncertainty), as your instructions describe."
        )

    def _check_message(self, outcomes: Sequence[WriterOutcome]) -> str:
        plan = self.plan
        assert plan is not None
        retained = [
            candidate
            for candidate in self.workspace.candidates.values()
            if candidate.status != CANDIDATE_STATUS_DISCARDED
        ]
        lines = [CHECK_INTRO, "", f"Planned records: {len(plan.items)}. Staged records: {len(retained)}."]
        short = [outcome for outcome in outcomes if outcome.short]
        if short:
            lines += [
                "",
                "Writers that staged fewer records than they were assigned. Decide whether "
                "any of their assigned records is missing, and stage it if so:",
            ]
            for outcome in short:
                assigned = "; ".join(f"P{position + 1}. {plan.items[position].label}" for position in outcome.positions)
                staged = ", ".join(outcome.staged_candidate_ids) or "none"
                lines.append(
                    f"- Writer {outcome.index} was assigned {_records(len(outcome.positions))} and staged "
                    f"{len(outcome.staged_candidate_ids)}. Assigned: {assigned}. Staged: {staged}. "
                    f"Its final reply: {outcome.final_text or '(none)'}"
                )
        lines += ["", "Staged records:"]
        lines += [f"{candidate.candidate_id}: {exact_json(candidate.staged_fields)}" for candidate in retained]
        return "\n".join(lines)

    # -- writers ----------------------------------------------------------------

    async def _run_writers(self, slices: Sequence[tuple[int, ...]]) -> list[WriterOutcome]:
        writer_count = len(slices)
        tasks = [
            asyncio.create_task(self._run_writer_with_reruns(index, writer_count, positions))
            for index, positions in enumerate(slices, start=1)
        ]
        primary = None
        failed_writer: int | None = None
        stop_reason = "writer_failed"
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for index, task in enumerate(tasks, start=1):
                if task in done and not task.cancelled():
                    primary = task.exception()
                    if primary is not None:
                        failed_writer = index
                        raise primary
        except BaseException as error:
            if failed_writer is None:
                stop_reason = "cancelled" if isinstance(error, asyncio.CancelledError) else "interrupted"
            raise
        finally:
            stop_started = asyncio.get_running_loop().time()
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            try:
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            except asyncio.CancelledError:
                # Cancellation during the drain supersedes the writer failure.
                primary = None
                raise
            finally:
                for view in self._chunk_views.values():
                    if not view.done():
                        view.cancel()
                seen = {id(primary)}
                for task in tasks:
                    if task.done() and not task.cancelled():
                        error = task.exception()
                        if isinstance(error, Exception) and id(error) not in seen:
                            seen.add(id(error))
                            _report_discarded_writer_failure(
                                error, message="Additional fan-out writer failed",
                                operation="extraction_fanout_additional_writer_failure",
                            )
                if pending or failed_writer is not None:
                    self._trace_writers_stopped(tasks, pending, stop_reason, failed_writer, stop_started)
        return [task.result() for task in tasks]

    def _trace_writers_stopped(
        self,
        tasks: Sequence[asyncio.Task],
        pending: Sequence[asyncio.Task],
        reason: str,
        failed_writer: int | None,
        stop_started: float,
    ) -> None:
        """Trace how the writers still running when the writing step ended early stopped.

        Read from task state only. ``reason`` is ``writer_failed`` (a writer failed for
        good), ``cancelled`` (the step was cancelled) or ``interrupted`` (any other
        exception ended the wait). ``stop_ms`` runs from that moment until every writer
        still running then had stopped. A writer that had already finished is not listed;
        one whose outcome is ``completed`` ignored the cancel and finished its work after
        the run had failed.
        """

        def outcome(task: asyncio.Task) -> str:
            if not task.done():
                return "running"
            if task.cancelled():
                return "cancelled"
            return "failed" if task.exception() is not None else "completed"

        self._trace("writers_stopped", {
            "reason": reason,
            "failed_writer": failed_writer,
            "stop_ms": round((asyncio.get_running_loop().time() - stop_started) * 1000),
            "writers": [
                {"writer": index, "outcome": outcome(task)}
                for index, task in enumerate(tasks, start=1)
                if task in pending
            ],
        })

    async def _run_writer_with_reruns(
        self, index: int, writer_count: int, positions: tuple[int, ...],
    ) -> WriterOutcome:
        """Run one writer; after a failure worth repeating, clear its attempt and run it again.

        Any exception from the writer's run counts, after its model-call retries are
        used up, when the typed curator-failure classification marks it retryable;
        an unreadable PDF, a context limit, an invalid result, a configuration or
        content-policy refusal is raised at once. Cancellation is never re-run, also
        when the SDK surfaces it as an ordinary tool error (the task is then still
        being cancelled). Once ``writer_reruns`` re-runs are used up, the writer's
        first error is raised, and ``_run_writers`` cancels the other writers as before.
        """

        first_error: Exception | None = None
        attempt = 1
        while True:
            try:
                outcome = await self._run_writer(index, writer_count, positions, attempt=attempt)
            except asyncio.CancelledError:
                if attempt > 1:
                    self._trace("writer_rerun_finished", {"writer": index, "attempt": attempt, "outcome": "cancelled"})
                raise
            except Exception as error:  # noqa: BLE001 - every writer failure is handled below
                failure = error
            else:
                if attempt > 1:
                    self._trace("writer_rerun_finished", {
                        "writer": index, "attempt": attempt, "outcome": "succeeded",
                    })
                return outcome
            if first_error is None:
                first_error = failure
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                if attempt > 1:
                    self._trace("writer_rerun_finished", {"writer": index, "attempt": attempt, "outcome": "cancelled"})
                raise first_error
            fields = _writer_error_fields(failure)
            rerun = classify_failure(failure).retryable and attempt <= self.writer_reruns
            self._trace("writer_failed", {
                "writer": index,
                "writer_count": writer_count,
                "attempt": attempt,
                "assigned_positions": [position + 1 for position in positions],
                **fields,
                "rerun": rerun,
                "store_off": self.writer_store_off,
            })
            if not rerun:
                break
            try:
                self._clear_failed_attempt(index, attempt, positions, fields)
            except Exception as cleanup_error:
                # Keep the writer's own error with the clean-up failure.
                raise cleanup_error from failure
            attempt += 1
        if attempt > 1:
            self._trace("writer_rerun_finished", {"writer": index, "attempt": attempt, "outcome": "failed"})
            if failure is not first_error:
                _report_discarded_writer_failure(
                    failure, message="Fan-out writer rerun failed",
                    operation="extraction_fanout_writer_rerun_failure",
                )
        # Raised outside the except block, so the first error keeps its own cause chain.
        raise first_error

    def _clear_failed_attempt(
        self, index: int, attempt: int, positions: tuple[int, ...], failure_fields: dict[str, Any],
    ) -> None:
        """Remove what one failed writer attempt alone produced, before the writer runs again.

        Removed, not discarded: evidence IDs are content hashes, so the re-run's
        identical evidence gets the identical ID, and a discarded record is never
        overwritten by a re-record (KANBAN-1775). Removal leaves a clean slate.
        - The attempt's staged records leave the workspace (each removal is an
          emitted candidate mutation) and its staging owner is closed, so a stage
          call of this attempt that lands later is refused.
        - Evidence that only this attempt recorded leaves the run's evidence list.
          Evidence another writer, an earlier attempt or the planner also recorded
          stays; only this attempt leaves its owner set.
        - The attempt's tool times are dropped. Its tool items were never replayed
          or streamed: a failed run returns no items, and writer records reach the
          UI only with the specialist's summary after every writer has finished.
        The trace event keeps the history as IDs and counts, never record content.
        """

        key = (index, attempt)
        removed_candidates = self.workspace.remove_candidates_staged_by(
            _staging_owner(index, attempt), reason=WRITER_RERUN_CLEANUP_REASON,
        )
        sole: list[str] = []
        shared = 0
        for evidence_id, recorders in list(self._evidence_recorders.items()):
            if key not in recorders:
                continue
            if recorders == {key}:
                sole.append(evidence_id)
                del self._evidence_recorders[evidence_id]
            else:
                recorders.discard(key)
                shared += 1
        removed_evidence = remove_evidence_records(self.evidence_records, sole, document_id=self.document_id)
        for call_id in self._attempt_call_ids.pop(key, ()):
            self._tool_timings.pop(call_id, None)
        self._trace("writer_rerun_started", {
            "writer": index,
            "attempt": attempt + 1,
            "assigned_positions": [position + 1 for position in positions],
            **failure_fields,
            "removed_candidate_count": len(removed_candidates),
            "removed_candidate_ids": removed_candidates,
            "removed_evidence_count": len(removed_evidence),
            "removed_evidence_ids": removed_evidence,
            "kept_shared_evidence_count": shared,
        })

    async def _run_writer(
        self, index: int, writer_count: int, positions: tuple[int, ...], *, attempt: int = 1,
    ) -> WriterOutcome:
        plan = self.plan
        assert plan is not None
        owner = _staging_owner(index, attempt)
        token = set_staging_owner(owner)
        try:
            chunk_indexes = list(dict.fromkeys(
                chunk for position in positions for chunk in plan.items[position].chunks
            ))
            writer = self._writer_agent((index, attempt))
            views = await self._writer_chunk_views(chunk_indexes)
            run_input = [
                {"role": "user", "content": self.input_text},
                {"role": "developer", "content": self._writer_assignment(index, writer_count, positions, views)},
            ]
            from .runner import run_agent_with_owned_openai_resources

            result = await run_agent_with_owned_openai_resources(
                writer,
                run_input,
                max_turns=self.max_turns,
                run_config=self.base_run_config,
            )
        finally:
            reset_staging_owner(token)
        staged = tuple(self.workspace.candidates_staged_by(owner))
        final_text = result.final_output if isinstance(result.final_output, str) else None
        outcome = WriterOutcome(
            index=index,
            positions=positions,
            staged_candidate_ids=staged,
            final_text=final_text,
            new_items=tuple(result.new_items),
        )
        self._trace("writer_completed", {
            "writer": index,
            "writer_count": writer_count,
            "attempt": attempt,
            "assigned_positions": [position + 1 for position in positions],
            "assigned_count": len(positions),
            "staged_count": len(staged),
            "staged_candidate_ids": list(staged),
            "prefetched_chunks": chunk_indexes if self.read_tool is not None else [],
            "final_reply": final_text,
            "store_off": self.writer_store_off,
        })
        return outcome

    def _writer_agent(self, attempt_key: tuple[int, int]) -> Any:
        stage_names = {_tool_name(tool) for tool in self.stage_tools}
        tools = [
            tool
            for tool in self.plain_tools
            if _tool_name(tool) in WRITER_SOURCE_TOOL_NAMES or _tool_name(tool) in stage_names
        ]
        writer = self.planner_agent.clone(
            instructions=self.base_instructions,
            tools=[self._writer_tool(tool, attempt_key) for tool in tools],
            tool_use_behavior="run_llm_again",
        )
        if self.writer_store_off:
            # New settings on the writer's copy only; the planner's object is untouched.
            writer.model_settings = replace(writer.model_settings, store=False)
        apply_tool_surface(writer, runtime=self.surface.runtime)
        history = getattr(writer, "run_history", None)
        if history is None:
            self.writer_invocations_unknown = True
        else:
            self.writer_invocation_ids.append(history.scope.invocation_id)
        return writer

    def _writer_tool(self, tool: Any, attempt_key: tuple[int, int]) -> Any:
        """Wrap one writer tool for the run's shared state.

        Records the call's real start and end, so its replayed audit keeps them. A
        verified record_evidence result joins the run's live evidence list as soon as
        the call returns, as the stream loop would add it for a single writer, so the
        writer can update that record by ID before the writers finish. The later replay
        merges the same record by ID. Each call ID and evidence ID is noted under the
        writer attempt that produced it, for a re-run's clean-up.
        """

        wrapped = copy(tool)
        invoke = tool.on_invoke_tool
        name = _tool_name(tool)

        async def on_invoke(ctx: Any, arguments: str) -> Any:
            started = datetime.now(timezone.utc)
            try:
                output = await invoke(ctx, arguments)
            finally:
                self._tool_timings[ctx.tool_call_id] = (started, datetime.now(timezone.utc))
                self._attempt_call_ids.setdefault(attempt_key, []).append(ctx.tool_call_id)
            record = build_record_evidence_summary_record(
                tool_name=name, tool_input=arguments, tool_output=output,
            )
            if record is not None:
                self._note_evidence_owner(record, attempt_key)
                self.record_live_evidence(record)
            return output

        wrapped.on_invoke_tool = on_invoke
        return wrapped

    def _note_evidence_owner(self, record: dict[str, Any], attempt_key: tuple[int, int]) -> None:
        evidence_id = str(record.get("evidence_record_id") or "").strip()
        if not evidence_id:
            return
        recorders = self._evidence_recorders.get(evidence_id)
        if recorders is None:
            recorders = self._evidence_recorders[evidence_id] = set()
            # A record already in the list before any writer recorded it (for example
            # one the planner recorded) is never a writer attempt's alone.
            if find_evidence_record_in_records(
                self.evidence_records, evidence_id, document_id=self.document_id,
            ) is not None:
                recorders.add(_EARLIER_EVIDENCE_OWNER)
        recorders.add(attempt_key)

    def _writer_assignment(
        self,
        index: int,
        writer_count: int,
        positions: Sequence[int],
        views: Sequence[tuple[int, str]],
    ) -> str:
        plan = self.plan
        assert plan is not None
        lines = [
            f"Writer phase: you are writer {index} of {writer_count}. For this run these "
            "rules override the contract-lookup, evidence-review and extraction-completion "
            "steps in your instructions. A planner has read the whole paper and assigned you "
            "the records below; other writers stage the other records in parallel in the same "
            "workspace, and the system checks and finalizes everything afterwards.",
            "- Stage only your items, one record per item, following all of your instructions "
            "for field values, sources and evidence. Fill every field from the paper's own "
            "wording, keeping details such as citation numbers, sources and identifiers "
            "whenever your instructions ask for them.",
        ]
        if views:
            lines.append(
                "- The chunks your items cite are already read below, with their evidence span "
                "indexes, exactly as read_chunk returns them. Use them directly in "
                "record_evidence (chunk_index and span_index). Call read_chunk or "
                "search_document only if these chunks lack something your instructions require."
            )
        lines += [
            "- Work in two turns: first call record_evidence for all of your items at once, "
            "then call the stage tool for all of your items at once.",
            "- Do not look up your contract, review evidence, list, patch or finalize.",
            "- When all of your items are staged, reply with one short line, for example 'done'.",
            "",
            "Planner notes:",
            *(plan.notes or ("(none)",)),
            "",
            "Whole plan, for context only (other writers own the other items):",
            *(f"P{number}. {item.label}" for number, item in enumerate(plan.items, start=1)),
            "",
            "Your items:",
            *(
                f"P{position + 1}. {plan.items[position].label} | chunks: "
                + (", ".join(str(chunk) for chunk in plan.items[position].chunks) or "none cited")
                for position in positions
            ),
        ]
        if views:
            lines += ["", "Prefetched chunks:"]
            lines += [f"read_chunk(chunk_index={chunk}) result:\n{view}" for chunk, view in views]
        return "\n".join(lines)

    async def _writer_chunk_views(self, chunk_indexes: Sequence[int]) -> list[tuple[int, str]]:
        """Check all prefetched passages together at handoff, after the last awaited read."""
        if self.read_tool is None or not chunk_indexes:
            return []
        readable = await self._prefetch_readable_files()
        views = []
        for chunk in chunk_indexes:
            source = readable.file_for(chunk)
            view = await self._chunk_view(chunk, source)
            views.append((chunk, source, view))

        current = await self._prefetch_readable_files()
        current_files = set(current.fingerprint)
        checked = []
        for chunk, source, view in views:
            if source is not None and source.fingerprint not in current_files:
                view = exact_json(files_changed_answer(PREFETCH_TOOL_NAME))
            checked.append((chunk, view))
        return checked

    async def _prefetch_readable_files(self) -> ReadableFiles:
        from src.lib.document_files.readable import resolve_readable_files

        try:
            return await asyncio.to_thread(resolve_readable_files, self.document_id, self.user_id)
        except Exception as exc:
            # A failed scope lookup is a failed source read, including for writer reruns.
            raise DocumentReadError(PREFETCH_TOOL_NAME) from exc

    async def _chunk_view(self, chunk_index: int, source: ReadableFile | None) -> str:
        if source is None:
            # Let the tool describe the current readable ranges. An out-of-range
            # answer can change as files come and go, so it is never cached.
            return await self._read_chunk_view(chunk_index, source)

        key = (chunk_index, source.fingerprint)
        view = self._chunk_views.get(key)
        if view is None:
            view = asyncio.create_task(self._read_chunk_view(chunk_index, source))
            self._chunk_views[key] = view
        return await asyncio.shield(view)

    async def _read_chunk_view(self, chunk_index: int, source: ReadableFile | None) -> str:
        """Render one chunk exactly as the model sees a read_chunk result."""

        from .document_references import project_model_source_items

        tool = self.read_tool
        assert tool is not None
        arguments = json.dumps({"chunk_index": chunk_index})
        call_id = f"fanout_prefetch_{chunk_index}"
        context = ToolContext(
            context=None,
            tool_name=_tool_name(tool),
            tool_call_id=call_id,
            tool_arguments=arguments,
        )
        # The prefetched text reaches the writers' models: read it under the
        # planner's history invocation, which the result's files read include
        # (ALL-1460). The prefetch runs outside any tool call, so no history is active.
        from .run_history import ACTIVE_HISTORY

        history = getattr(self.planner_agent, "run_history", None)
        token = ACTIVE_HISTORY.set(history) if history is not None else None
        try:
            raw = await tool.on_invoke_tool(context, arguments)
        finally:
            if token is not None:
                ACTIVE_HISTORY.reset(token)
        error_code = raw.get("error_code") if isinstance(raw, dict) else getattr(raw, "error_code", None)
        if source is None and not error_code:
            # This chunk acquired a readable owner after prefetch began.
            raw = files_changed_answer(PREFETCH_TOOL_NAME)
        call = ResponseFunctionToolCall(
            type="function_call", call_id=call_id, name=_tool_name(tool), arguments=arguments,
        )
        output_item = dict(ItemHelpers.tool_call_output_item(call, raw))
        projected = await project_model_source_items(
            [call.model_dump(exclude_none=True), output_item],
            user_id=get_current_user_id(),
            cache={},
        )
        if source is not None and error_code:
            # Only passages can be reused. A subsequent read gets its own answer.
            self._chunk_views.pop((chunk_index, source.fingerprint), None)
        output = projected[1]["output"]
        return output if isinstance(output, str) else exact_json(output)

    # -- finalize ----------------------------------------------------------------

    def _finalize_candidate_ids(self) -> list[str]:
        """Every retained record: writers' records in plan order, then the rest."""

        retained = [
            candidate_id
            for candidate_id, candidate in self.workspace.candidates.items()
            if candidate.status != CANDIDATE_STATUS_DISCARDED
            and candidate_id not in self._finalize_created
        ]
        ordered = [
            candidate_id
            for outcome in self.writer_outcomes
            for candidate_id in outcome.staged_candidate_ids
            if candidate_id in retained
        ]
        return ordered + [candidate_id for candidate_id in retained if candidate_id not in ordered]

    async def _invoke_finalize(self, attempt: int) -> Any:
        candidate_ids = self._finalize_candidate_ids()
        arguments = json.dumps({"candidate_ids": candidate_ids})
        before = set(self.workspace.candidates)
        context = ToolContext(
            context=None,
            tool_name=_tool_name(self.finalize_tool),
            tool_call_id=f"fanout_finalize_{attempt}",
            tool_arguments=arguments,
        )
        output = await self.finalize_tool.on_invoke_tool(context, arguments)
        finalized = self.workspace.finalization is not None
        if not finalized:
            # A failed attempt can leave its own materialized draft behind; it is not a
            # staged record and must not join the next attempt.
            self._finalize_created.update(set(self.workspace.candidates) - before)
        self._trace("finalize_attempt", {
            "attempt": attempt,
            "candidate_count": len(candidate_ids),
            "finalized": finalized,
        })
        return output

    async def _finalize(self):
        self.phase = "finalizing"
        output = await self._invoke_finalize(attempt=1)
        max_attempts = 1 + config.get_extraction_fanout_max_finalize_corrections()
        for attempt in range(2, max_attempts + 1):
            if self.workspace.finalization is not None:
                return
            self.phase = "correcting"
            self._progress("Fixing the problems validation found before saving")
            async for event in self._run_planner_thread(
                "[Finalize check] The system tried to finalize the staged records, and validation "
                "reported the problems below. Fix them with your correction tools: patch a record, "
                "discard one that should not be kept, or stage a missing one. Make every fix in one "
                "response, calling the tools in parallel. Do not finalize; the "
                "system finalizes again after you reply. Then reply with your final explanation "
                "(what was extracted, exclusions and uncertainty), as your instructions describe."
                f"\n\nValidation result:\n{output}"
            ):
                yield event
            self.phase = "finalizing"
            output = await self._invoke_finalize(attempt=attempt)

    def _plan_missing_error(self) -> Exception:
        from .streaming_tools import SpecialistOutputError

        message = (
            f"{self.specialist_name} did not submit an extraction plan, so no records "
            "were written."
        )
        self.emit_event({
            "type": "SPECIALIST_ERROR",
            "timestamp": _now_iso(),
            "details": {
                "specialist": self.specialist_name,
                "error": message,
                "message": message,
                "reason": "extraction_plan_missing",
                "severity": "error",
            },
        })
        return SpecialistOutputError(
            specialist_name=self.specialist_name,
            output_type_name="extraction_plan",
            message=message,
            details=[{"reason": "extraction_plan_missing"}],
        )


__all__ = [
    "ExtractionPlan",
    "FanoutExtraction",
    "PlanFormatError",
    "PlanItem",
    "SUBMIT_PLAN_TOOL_NAME",
    "WriterOutcome",
    "parse_plan_arguments",
    "planning_message",
    "prepare_fanout_extraction",
    "submit_plan_parameters_schema",
    "writer_slices",
]
