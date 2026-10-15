"""Durable exact history, scoped by server identity and invocation ancestry.

No trace truncation/redaction policy is applied to this canonical store. Only
runtime-owned payloads are accepted; callers cannot choose an owner or session.
A failed write disables eviction for the invocation, never retries a tool.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, field
import hashlib
import json
import logging
import time
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.models.sql.database import SessionLocal
from . import config

logger = logging.getLogger(__name__)
ACTIVE_HISTORY: ContextVar["RunHistory | None"] = ContextVar("active_run_history", default=None)
HISTORY_TOOL_NAMES = frozenset({"search_run_history", "read_run_history"})


class HistoryRequestError(ValueError):
    """Expected missing, inaccessible or malformed history requests."""


def _report_history_failure(operation: str, exc: Exception) -> None:
    """Report storage/serialization failures without SQL, payloads or chains."""
    message = {
        "write_failed": "Agent history write failed; active context will be retained",
        "search_failed": "Agent history search failed",
        "read_failed": "Agent history read failed",
    }[operation]
    try:
        from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
        report_runtime_exception(sanitized_runtime_error(message), component="agent_history",
            operation=operation, context={"error_type": type(exc).__name__})
    except Exception:
        # Optional diagnostics must not turn a completed mutation into a failure.
        pass
    logger.warning(message, extra={"sentry_skip_event": True})


def exact_json(value: Any) -> str:
    """Explicit lossless JSON representation. Never stringify an unknown object."""
    def encode(item):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        raise TypeError(f"Unsupported history payload type: {type(item).__name__}")
    return json.dumps(value, default=encode, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def searchable_text(content: str) -> str:
    """Decode JSON leaves for literal search without changing the stored original.

    Tool arguments/results may themselves be JSON strings. Include their decoded
    leaves too, so quotes, backslashes and line breaks are searchable as seen by
    the caller, rather than requiring knowledge of each serialization layer.
    """
    pending, parts = [json.loads(content)], []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(reversed(list(value.values())))
            parts.extend(value.keys())
        elif isinstance(value, list):
            pending.extend(reversed(value))
        elif isinstance(value, str):
            parts.append(value)
            if value.lstrip().startswith(("{", "[", '"')):
                try:
                    pending.append(json.loads(value))
                except ValueError:
                    pass
        else:
            parts.append(exact_json(value))
    # PostgreSQL text cannot store NUL. Apply the same reversible search spelling
    # to the query; the canonical JSON body already retains its escaped form.
    return "\n".join(parts).replace("\x00", "\\u0000")


@dataclass(frozen=True)
class HistoryScope:
    owner_id: str
    run_id: str
    invocation_id: str
    agent_key: str
    lineage: str
    session_id: str | None = None
    parent_invocation_id: str | None = None
    # Only an explicit root supervisor may inspect earlier runs in its session.
    session_reader: bool = False


class HistoryStore:
    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory

    def append(self, scope: HistoryScope, kind: str, content: str, *, tool_name=None, call_id=None, outcome=None) -> str:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        event_id = str(uuid4())
        with self.session_factory() as db, db.begin():
            # PostgreSQL and SQLite both support conflict-safe content-addressed insertion.
            if db.bind.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            db.execute(insert(AgentHistoryPayload).values(sha256=digest, content=content,
                search_text=searchable_text(content)).on_conflict_do_nothing(index_elements=["sha256"]))
            db.add(AgentHistoryEvent(event_id=event_id, owner_id=scope.owner_id,
                session_id=scope.session_id, run_id=scope.run_id,
                invocation_id=scope.invocation_id, parent_invocation_id=scope.parent_invocation_id,
                lineage=scope.lineage, agent_key=scope.agent_key, kind=kind,
                tool_name=tool_name, call_id=call_id, outcome=outcome, payload_sha256=digest))
        return event_id

    @staticmethod
    def _authorized(scope: HistoryScope):
        owned = AgentHistoryEvent.owner_id == scope.owner_id
        current_tree = ((AgentHistoryEvent.run_id == scope.run_id)
                        & AgentHistoryEvent.lineage.startswith(scope.lineage, autoescape=True))
        if scope.session_reader and scope.session_id:
            return owned & (AgentHistoryEvent.session_id == scope.session_id)
        return owned & current_tree

    @staticmethod
    def metadata(event: AgentHistoryEvent) -> dict:
        return {key: getattr(event, key) for key in (
            "event_id", "sequence", "run_id", "invocation_id", "parent_invocation_id",
            "agent_key", "kind", "tool_name", "call_id", "outcome", "payload_sha256")}

    def read(self, scope: HistoryScope, event_id: str) -> dict:
        with self.session_factory() as db:
            row = db.execute(select(AgentHistoryEvent, AgentHistoryPayload.content)
                .join(AgentHistoryPayload)
                .where(self._authorized(scope), AgentHistoryEvent.event_id == event_id)).first()
            if row is None:
                raise HistoryRequestError("History event unavailable in this authorized scope.")
            event, content = row
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != event.payload_sha256:
                raise RuntimeError("History payload integrity check failed.")
            return {"event": self.metadata(event), "payload": json.loads(content)}

    def search(self, scope: HistoryScope, query: str, *, after: int, limit: int, tool_name: str | None = None) -> list[dict]:
        if after < 0 or limit < 1:
            raise HistoryRequestError("after must be non-negative and limit positive")
        stmt = select(AgentHistoryEvent).join(AgentHistoryPayload).where(
            self._authorized(scope), AgentHistoryEvent.sequence > after)
        if query:
            stmt = stmt.where(AgentHistoryPayload.search_text.icontains(
                query.replace("\x00", "\\u0000"), autoescape=True))
        if tool_name:
            stmt = stmt.where(AgentHistoryEvent.tool_name == tool_name)
        with self.session_factory() as db:
            return [self.metadata(event) for event in db.scalars(
                stmt.order_by(AgentHistoryEvent.sequence).limit(limit))]


@dataclass
class RunHistory:
    scope: HistoryScope
    store: HistoryStore = field(default_factory=HistoryStore)
    healthy: bool = True
    events_written: int = 0
    submitted_payload_bytes: int = 0
    write_failures: int = 0
    write_duration_ms: float = 0.0
    # Item hash + occurrence index avoids collapsing repeated identical messages.
    item_refs: dict[tuple[str, int], str] = field(default_factory=dict)
    model_view: Any = None
    native_compaction_keys: set[str] = field(default_factory=set)
    tool_output_metadata: dict[str, dict] = field(default_factory=dict)
    builder_revisions: dict[str, dict[str, int]] = field(default_factory=dict)
    builder_previous_events: dict[str, str] = field(default_factory=dict)
    _metrics_lock: Any = field(default_factory=Lock, repr=False)

    def assert_current_scope(self) -> None:
        from src.lib.context import get_current_user_id, get_current_trace_id, get_current_session_id
        if (get_current_user_id() != self.scope.owner_id
                or get_current_trace_id() != self.scope.run_id
                or get_current_session_id() != self.scope.session_id):
            raise HistoryRequestError("History instance is not authorized for this request.")

    def _write_failed(self, exc: Exception) -> None:
        with self._metrics_lock:
            first_failure = self.healthy
            self.healthy = False
            self.write_failures += 1
        # Concurrent writes already in flight may also fail. Alert once per
        # invocation while counting every failed write/serialization attempt.
        if first_failure:
            _report_history_failure("write_failed", exc)

    def record_sync(self, kind: str, value: Any, **metadata) -> str | None:
        if not self.healthy:
            return None
        started = time.perf_counter()
        try:
            content = exact_json(value)
            ref = self.store.append(self.scope, kind, content, **metadata)
            with self._metrics_lock:
                self.events_written += 1
                self.submitted_payload_bytes += len(content.encode("utf-8"))
            return ref
        except Exception as exc:
            self._write_failed(exc)
            return None
        finally:
            with self._metrics_lock:
                self.write_duration_ms += (time.perf_counter() - started) * 1000

    async def record(self, kind: str, value: Any, **metadata) -> str | None:
        return await asyncio.to_thread(self.record_sync, kind, value, **metadata)

    async def capture_input(self, items: list[Any], instructions: str | None) -> dict[int, str]:
        occurrences: dict[str, int] = {}
        refs = {}
        for index, item in enumerate(items):
            try:
                digest = hashlib.sha256(exact_json(item).encode("utf-8")).hexdigest()
            except Exception as exc:
                self._write_failed(exc)
                continue
            occurrence = occurrences.get(digest, 0)
            occurrences[digest] = occurrence + 1
            key = (digest, occurrence)
            if key not in self.item_refs:
                ref = await self.record("input_item", item)
                if ref:
                    self.item_refs[key] = ref
            if key in self.item_refs:
                refs[index] = self.item_refs[key]
        await self.record("model_input_manifest", {"instructions": instructions,
            "items": [refs.get(i) for i in range(len(items))], "complete": len(refs) == len(items)})
        return refs

    async def checkpoint(self, items: list[Any], instructions: str | None, metadata: dict) -> str | None:
        """Persist a model-ready window by immutable item references.

        Exact original inputs and builder revisions remain separate. Recovering
        this window is data restoration; it never invokes the tools it describes.
        """
        if not self.healthy:
            return None
        occurrences: dict[str, int] = {}
        refs = []
        for item in items:
            content = exact_json(item)
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            occurrence = occurrences.get(digest, 0)
            occurrences[digest] = occurrence + 1
            key = (digest, occurrence)
            ref = self.item_refs.get(key)
            if ref is None:
                ref = await self.record("context_item", item)
                if ref:
                    self.item_refs[key] = ref
            refs.append(ref)
        return await self.record("model_context_checkpoint", {
            "items": refs, "instructions": instructions,
            "complete": all(refs), **metadata,
        })


def new_history(agent: Any, *, agent_key: str, runtime: str) -> RunHistory | None:
    from src.lib.context import get_current_session_id, get_current_trace_id, get_current_user_id
    if not config.get_agent_history_enabled():
        agent.run_history_unavailable_reason = "disabled_by_configuration"
        return None
    owner, run_id = get_current_user_id(), get_current_trace_id()
    if not owner or not run_id:
        # Unauthenticated/offline utility runs cannot manufacture an authorization scope.
        agent.run_history_unavailable_reason = "no_authenticated_owner_or_run"
        return None
    agent.run_history_unavailable_reason = None
    parent = ACTIVE_HISTORY.get()
    if parent and (parent.scope.owner_id != owner or parent.scope.run_id != run_id):
        parent = None
    invocation = str(uuid4())
    lineage = (parent.scope.lineage if parent else "") + invocation + "/"
    scope = HistoryScope(owner_id=owner, run_id=run_id, invocation_id=invocation,
        agent_key=agent_key, lineage=lineage, session_id=get_current_session_id(),
        parent_invocation_id=parent.scope.invocation_id if parent else None,
        session_reader=parent is None and runtime in {"chat_supervisor", "flow_supervisor", "agent_studio", "benchmark_assistant"})
    return RunHistory(scope)


async def record_runtime_payload(kind: str, value: Any, **metadata) -> str | None:
    history = ACTIVE_HISTORY.get()
    return await history.record(kind, value, **metadata) if history else None


def history_tools(history: RunHistory) -> list[Any]:
    from agents import function_tool
    from agr_ai_curation_runtime.tool_result_bounds import detail_chunk, fit_page, invalid_cursor
    from .tool_result_bounds import tool_result_budget

    @function_tool
    async def search_run_history(query: str = "", after: int = 0, limit: int | None = None,
                                 tool_name: str | None = None) -> dict:
        """Search exact retained history by literal text, including earlier negative findings.
        Returns event identities, not summaries. Read an event with read_run_history.
        Use next_after for continuation. Scope is enforced by the server.
        """
        try:
            history.assert_current_scope()
            cap = min(max(1, limit if limit is not None else config.get_agent_history_search_max_results()), config.get_agent_history_search_max_results())
            rows = await asyncio.to_thread(history.store.search, history.scope, query,
                                          after=after, limit=cap + 1, tool_name=tool_name)
            def render(page, count):
                more = len(rows) > count
                return {"events": page, "next_after": page[-1]["sequence"] if more and page else None,
                        "has_more": more, "coverage": "Recorded events since durable history was deployed. Older runs are not backfilled; interrupted runs or storage failures can leave gaps. No match does not prove an action did not occur."}
            page, _ = fit_page(rows, start=0, limit=cap, render=render, budget=tool_result_budget())
            return page
        except HistoryRequestError:
            return invalid_cursor("History search unavailable. Keep current evidence; do not infer missing results.")
        except Exception as exc:
            _report_history_failure("search_failed", exc)
            return invalid_cursor("History search unavailable. Keep current evidence; do not infer missing results.")

    @function_tool
    async def read_run_history(event_id: str, detail_path: str = "payload", detail_cursor: int = 0,
                               result_sha256: str | None = None) -> dict:
        """Read exact retained content or nested fields, without rerunning the original tool.
        Use detail.next_cursor and detail.sha256 to read every chunk. Dotted paths
        descend through payload, mapping keys and list indexes. Data is never summarized.
        """
        try:
            history.assert_current_scope()
            result = await asyncio.to_thread(history.store.read, history.scope, event_id)
            try:
                return detail_chunk(result, path=detail_path, cursor=detail_cursor,
                    expected_sha256=result_sha256, budget=tool_result_budget(), extra={"event_id": event_id})
            except (ValueError, KeyError, IndexError):
                return invalid_cursor("History event or path unavailable in this scope. Do not infer its contents.")
        except HistoryRequestError:
            return invalid_cursor("History event or path unavailable in this scope. Do not infer its contents.")
        except Exception as exc:
            _report_history_failure("read_failed", exc)
            return invalid_cursor("History event or path unavailable in this scope. Do not infer its contents.")
    for tool in (search_run_history, read_run_history):
        tool._runtime_history_tool = True
        invoke = tool.on_invoke_tool

        async def history_result(ctx, arguments, _invoke=invoke):
            # The SDK may stringify this dict with Python repr. Capture status
            # and pagination from the structured result before that conversion.
            value = await _invoke(ctx, arguments)
            call_id = getattr(ctx, "tool_call_id", None)
            if call_id:
                from .model_context_view import _attention, _continuations
                history.tool_output_metadata[call_id] = {
                    "attention": not isinstance(value, dict) or _attention(value),
                    "continuations": _continuations(value),
                }
            return value

        tool.on_invoke_tool = history_result
    return [search_run_history, read_run_history]


def wrap_history_tools(tools: list[Any], history: RunHistory) -> list[Any]:
    from agents import FunctionTool
    result = []
    for tool in tools:
        if not isinstance(tool, FunctionTool) or tool.name in HISTORY_TOOL_NAMES:
            result.append(tool)
            continue
        base = getattr(tool, "_history_original", tool)
        wrapped = copy(base)
        async def invoke(ctx, arguments, _base=base):
            history.assert_current_scope()
            token = ACTIVE_HISTORY.set(history)
            call_id = getattr(ctx, "tool_call_id", None)
            await history.record("tool_start", {"arguments": arguments}, tool_name=_base.name, call_id=call_id)
            try:
                value = await _base.on_invoke_tool(ctx, arguments)
                await history.record("tool_result", value, tool_name=_base.name, call_id=call_id, outcome="returned")
                # SDK output formatting may use Python/Pydantic repr rather than JSON.
                # Retain only mechanical status/navigation metadata from the original
                # structured value; never parse or semantically summarize that repr.
                if call_id:
                    try:
                        from .model_context_view import _attention, _continuations
                        structured = json.loads(exact_json(value))
                        if isinstance(structured, str):
                            structured = json.loads(structured)
                        history.tool_output_metadata[call_id] = {
                            "attention": _attention(structured),
                            "continuations": _continuations(structured),
                        }
                    except (TypeError, ValueError):
                        pass
                return value
            except BaseException as exc:
                await history.record("tool_error", {"type": type(exc).__name__, "message": str(exc)},
                                     tool_name=_base.name, call_id=call_id, outcome="raised")
                raise
            finally:
                ACTIVE_HISTORY.reset(token)
        wrapped.on_invoke_tool = invoke
        wrapped._history_original = base
        result.append(wrapped)
    return result


def prepare_history_tools(agent: Any, tools: list[Any], *, agent_key: str, runtime: str) -> tuple[list[Any], RunHistory | None]:
    history = new_history(agent, agent_key=agent_key, runtime=runtime)
    if any(getattr(tool, "name", None) in HISTORY_TOOL_NAMES
           and not getattr(tool, "_runtime_history_tool", False) for tool in tools):
        raise ValueError("Agent tool collides with a reserved runtime history tool name")
    original = [getattr(tool, "_history_original", tool) for tool in tools
                if getattr(tool, "name", None) not in HISTORY_TOOL_NAMES]
    agent.run_history = history
    if history is None:
        return original, None
    return [*wrap_history_tools(original, history), *history_tools(history)], history


def prepare_history_run(agent: Any, run_config):
    """Attach exact history without changing a precompiled Studio tool policy."""
    from .model_context_view import run_config_with_history
    from .tool_surface import (_agent_key, runtime_for_agent, ToolSurface, compile_tool_surface,
                               resolve_tool_loading_policy, MODE_EAGER_PROVIDER_UNSUPPORTED)
    previous = getattr(agent, "tool_surface", None)
    tools = list(previous.source_tools if isinstance(previous, ToolSurface)
                 else getattr(agent, "tools", None) or [])
    tools, history = prepare_history_tools(agent, tools,
        agent_key=_agent_key(agent), runtime=runtime_for_agent(agent))
    if isinstance(previous, ToolSurface):
        namespaces = {tool.name: getattr(tool, "_tool_namespace", None) for tool in previous.tools
                      if getattr(tool, "_tool_namespace", None)}
        def namespace(name):
            key = namespaces.get(name)
            return (key, previous.namespace_descriptions[key]) if key else None
        surface = compile_tool_surface(tools, runtime=previous.runtime, agent_key=previous.agent_key,
            policy=resolve_tool_loading_policy(previous.runtime, previous.agent_key),
            supports_tool_search=previous.mode != MODE_EAGER_PROVIDER_UNSUPPORTED,
            namespace_resolver=namespace, forced_tool_names=(
                *(name for name in previous.eager_names if name not in HISTORY_TOOL_NAMES),
                *(HISTORY_TOOL_NAMES if history else ())))
        surface.run_history = history
        agent.tool_surface = surface
        agent.tools = surface.tools
    elif not isinstance(previous, ToolSurface):
        agent.tools = tools
    return run_config_with_history(run_config, history)


async def record_model_history(agent: Any, kind: str, value: Any, **metadata) -> None:
    history = getattr(agent, "run_history", None)
    if history:
        await history.record(kind, value, **metadata)
        if kind == "model_output" and isinstance(value, list) and history.healthy:
            from .context_compaction import compaction_key
            for item in value:
                item = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                key = compaction_key(item) if isinstance(item, dict) else None
                if key:
                    history.native_compaction_keys.add(key)


def record_builder_history(kind: str, workspace: Any, *, output: Any, validation: Any) -> None:
    """Synchronous builder mutation boundary, before diagnostic trace reduction.

    Builder APIs are synchronous and cannot await a queued write. Commit here so
    a later context checkpoint cannot overtake an unpersisted object revision.
    """
    history = ACTIVE_HISTORY.get()
    if not history or not history.healthy:
        return
    with workspace._mutation_lock:
        _record_builder_delta(history, kind, workspace, output=output, validation=validation)


def _record_builder_delta(history: RunHistory, kind: str, workspace: Any, *, output: Any, validation: Any) -> None:
    """Called while the same lock protects candidate mutations and publication."""
    seen = history.builder_revisions.setdefault(workspace.builder_invocation_id, {})
    changed = [candidate.snapshot() for candidate in workspace.candidates.values()
               if seen.get(candidate.candidate_id) != candidate.revision]
    # Cumulative candidate bodies are already retained in earlier deltas. Never
    # archive the growing workspace again for every unrelated candidate mutation.
    metadata = {key: getattr(workspace, key) for key in (
        "run_id", "document_id", "domain_pack_id", "agent_id", "builder_invocation_id",
        "state", "validation_errors", "created_at", "updated_at")}
    metadata.update(workspace_revision=workspace.revision, candidate_count=len(workspace.candidates),
        generic_profile_ref=workspace.generic_profile.receipt if workspace.generic_profile else None,
        execution_receipt=workspace.execution_receipt,
        finalization=workspace.finalization.summary() if workspace.finalization else None)
    ref = history.record_sync(kind, {"history_format": "workspace_delta_v1", "workspace": metadata,
        "previous_event_id": history.builder_previous_events.get(workspace.builder_invocation_id),
        "changed_candidates": changed, "output": output, "validation": validation})
    if ref:
        seen.update((candidate["candidate_id"], candidate["revision"]) for candidate in changed)
        history.builder_previous_events[workspace.builder_invocation_id] = ref
