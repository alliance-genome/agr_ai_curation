"""Coarse, reversible model-input checkpoints over a complete durable history.

Only completed successful tool-output bodies are replaced, never the original
request, instructions, tool-call arguments, opaque reasoning or pending calls.
Mechanical output references complement native Responses compaction. The soft
projection target is never a reason to reject an otherwise valid request.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import inspect
import json
from typing import Any

from agents.exceptions import UserError
from agents.run import ModelInputData

from . import config
from .run_history import RunHistory, exact_json
from .context_compaction import compacted_window, compaction_key

class ContextRecoveryError(UserError):
    """A visible failure instead of silently losing required context."""


class HistoryStorageUnavailable(ContextRecoveryError):
    """History persistence failed before a required context checkpoint."""


def _attention(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "success" and child is False:
                return True
            if key in {"error", "errors", "error_code", "validation_errors", "warnings"} and child:
                return True
            if key in {"status", "lookup_status", "outcome"} and child in (
                "error", "failed", "invalid_request", "incomplete", "needs_patch", "cancelled"):
                return True
            if _attention(child):
                return True
    elif isinstance(value, list):
        return any(_attention(child) for child in value)
    return False


def _continuations(value: Any, path: str = "") -> dict:
    """Preserve exact page navigation fields, without interpreting their meaning."""
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if (key.startswith("next_") or key in {"span_page", "content_range", "result_page"}) and child is not None:
                result[child_path] = child
            elif isinstance(child, (dict, list)):
                result.update(_continuations(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(_continuations(child, f"{path}.{index}"))
    return result


def _estimate(agent, data: ModelInputData, run_config=None) -> dict:
    # Reuse the actual request-measurement model; it includes tools, instructions,
    # handoffs and structured output. It never invokes a provider or charges money.
    from .model_request_measurement import build_measurement
    from agents.agent_output import AgentOutputSchema, AgentOutputSchemaBase
    output_type = getattr(agent, "output_type", None)
    output_schema = (output_type if isinstance(output_type, AgentOutputSchemaBase)
                     else AgentOutputSchema(output_type) if output_type is not None else None)
    reserve = getattr(getattr(run_config, "model_settings", None), "max_tokens", None)
    if reserve is None:
        reserve = getattr(getattr(agent, "model_settings", None), "max_tokens", None)
    reserve_estimated = reserve is None
    if reserve_estimated:
        reserve = config.get_agent_context_output_reserve_tokens()
    measurement = build_measurement(runtime="agents_sdk", provider="estimate", api="estimate",
        transport="estimate", model=None, instructions=data.instructions, input_value=data.input,
        tools=agent.tools, handoffs=getattr(agent, "handoffs", None),
        output_schema=output_schema, output_reserve_tokens=reserve)
    estimated = measurement["model_visible"]["estimated_tokens"]
    reserve = measurement.get("output_reserve_tokens") or 0
    # Deferred definitions may become loaded server-side. Conservatively count all.
    from src.lib.runtime_payload_budget import estimate_tokens_from_chars
    deferred = measurement["outbound"]["tools"]["deferred"]["chars"]
    return {"estimated_total_tokens": estimated + reserve + estimate_tokens_from_chars(deferred),
            "output_reserve_tokens": reserve, "output_reserve_estimated": reserve_estimated, "outbound": measurement["outbound"]}


@dataclass
class ModelContextView:
    history: RunHistory
    retired: dict[str, str] = field(default_factory=dict)
    run_config: Any = None
    allow_projection: bool = True
    checkpoint: int = 0
    last_metrics: dict = field(default_factory=dict)
    source_reference_views: dict = field(default_factory=dict)

    async def filter(self, call) -> ModelInputData:
        original = call.model_data
        refs = await self.history.capture_input(original.input, original.instructions)
        before = _estimate(call.agent, original, self.run_config)
        from .document_references import project_model_source_items
        items = await project_model_source_items(original.input,
            user_id=self.history.scope.owner_id, cache=self.source_reference_views)
        enabled = config.get_agent_context_view_enabled()
        offset = 0
        reset_compactions = 0
        if self.history.healthy and enabled:
            if self.allow_projection:
                items = compacted_window(items, self.history.native_compaction_keys)
                offset = len(original.input) - len(items)
                refs = {index - offset: ref for index, ref in refs.items() if index >= offset}
            else:
                # A tool-less synthesis retry starts from the SDK's complete
                # canonical history. Earlier native state may summarize history
                # references that this retry cannot read. Remove only our own
                # native items, preserving exact bodies and foreign/standalone
                # opaque state. Any new compaction now sees the available originals.
                indexes = [index for index, item in enumerate(items)
                           if compaction_key(item) not in self.history.native_compaction_keys]
                reset_compactions = len(items) - len(indexes)
                items = [items[index] for index in indexes]
                refs = {new: refs[old] for new, old in enumerate(indexes) if old in refs}
        # A fresh canonical source is used on every request. Never overwrite SDK state.
        if self.history.healthy and enabled and self.allow_projection:
            for index, ref in refs.items():
                if ref in self.retired and isinstance(items[index], dict):
                    items[index]["output"] = self.retired[ref]
        current = ModelInputData(input=items, instructions=original.instructions)
        size = _estimate(call.agent, current, self.run_config)
        trigger = config.get_agent_context_trigger_estimated_tokens()
        if enabled and size["estimated_total_tokens"] > trigger:
            call_ids = {item.get("call_id") for item in items
                        if isinstance(item, dict) and item.get("type") == "function_call"}
            outputs = [i for i, item in enumerate(items) if isinstance(item, dict)
                       and item.get("type") == "function_call_output" and item.get("call_id") in call_ids]
            eligible = (outputs[:-config.get_agent_context_recent_tool_results()]
                        if self.allow_projection and self.history.healthy else [])
            changed = False
            for index in eligible:
                ref = refs.get(index)
                if not ref or ref in self.retired:
                    continue
                output = items[index].get("output")
                if not isinstance(output, str):
                    # Exact text reads cannot recreate a native image/audio input.
                    continue
                metadata = self.history.tool_output_metadata.get(items[index].get("call_id"))
                if metadata is None:
                    try:
                        value = json.loads(output) if isinstance(output, str) else output
                    except (ValueError, TypeError):
                        # Unknown/unstructured output may carry an actionable error.
                        continue
                    metadata = {"attention": _attention(value), "continuations": _continuations(value)}
                if metadata["attention"]:
                    continue
                read = {"event_id": ref, "detail_path": "payload.output", "detail_cursor": 0}
                source_call = next((item for item in items if isinstance(item, dict)
                                    and item.get("type") == "function_call"
                                    and item.get("call_id") == items[index].get("call_id")), None)
                if source_call and source_call.get("name", "").rsplit(".", 1)[-1] == "read_run_history":
                    # Point back to the original read, not a wrapper around a
                    # wrapper. The exact returned page also remains archived.
                    read = json.loads(source_call["arguments"])
                descriptor = exact_json({"history_event_id": ref,
                    "content_externalized": True,
                    "read": read,
                    "continuation_fields": metadata["continuations"],
                    "instruction": "Original output retained exactly. Use read_run_history before relying on its details; do not rerun the original tool to recover it."})
                if len(descriptor.encode("utf-8")) >= len(exact_json(output).encode("utf-8")):
                    continue
                self.retired[ref] = descriptor
                items[index]["output"] = descriptor
                changed = True
                size = _estimate(call.agent, current, self.run_config)
                if size["estimated_total_tokens"] <= config.get_agent_context_target_estimated_tokens():
                    break
            if changed:
                self.checkpoint += 1
        retrieval_ids = {item.get("call_id") for item in items if isinstance(item, dict)
                         and item.get("type") == "function_call"
                         and item.get("name", "").rsplit(".", 1)[-1] in {"read_run_history", "search_run_history"}}
        self.last_metrics = {"policy": "exact_history_native_compaction_v2", "enabled": enabled, "retrieval_available": self.allow_projection,
            "native_compactions": len(self.history.native_compaction_keys),
            "compacted_prefix_items": offset,
            "reset_native_compaction_items": reset_compactions,
            "above_soft_target": size["estimated_total_tokens"] > trigger,
            "checkpoint": self.checkpoint, "archive_healthy": self.history.healthy,
            "archive_events_written": self.history.events_written,
            "archive_submitted_payload_bytes": self.history.submitted_payload_bytes,
            "archive_write_failures": self.history.write_failures,
            "archive_write_duration_ms": self.history.write_duration_ms,
            "retained_item_count": len(items) - sum(ref in self.retired for ref in refs.values()) if self.history.healthy and enabled else len(items),
            "input_item_count": len(items), "archived_item_count": len(refs),
            "externalized_output_count": sum(ref in self.retired for ref in refs.values()) if self.history.healthy else 0,
            "before": before, "after": size,
            "retrieval_call_count": len(retrieval_ids),
            "retrieval_output_bytes": sum(len(exact_json(item.get("output")).encode("utf-8"))
                                          for item in items if isinstance(item, dict)
                                          and item.get("type") == "function_call_output"
                                          and item.get("call_id") in retrieval_ids)}
        await self.history.record("context_view", self.last_metrics)
        from .extraction_trace_events import write_extraction_trace_event
        write_extraction_trace_event(event_type="runtime.context_view", output_summary=self.last_metrics,
                                     metadata={"agent": getattr(call.agent, "name", None)})
        # This is working state, not a replacement for canonical inputs, builder
        # revisions, action receipts or exact scientific results in the archive.
        await self.history.checkpoint(current.input, current.instructions, {
            "native_compactions": len(self.history.native_compaction_keys),
            "completed_tool_call_ids": [item.get("call_id") for item in original.input
                                        if isinstance(item, dict) and item.get("type") == "function_call_output"],
        })
        # If even the final manifest write failed, return the original full input.
        if not self.history.healthy and enabled and before["estimated_total_tokens"] > trigger:
            raise HistoryStorageUnavailable("Working history could not be saved while preparing this request. "
                                       "Completed actions have not been repeated. Please try again later.")
        return current if self.history.healthy else original


def run_config_with_history(run_config, history: RunHistory | None, *, allow_projection: bool = True):
    from agents import RunConfig
    if run_config is None and history is None:
        return None
    run_config = run_config or RunConfig()
    previous = run_config.call_model_input_filter
    # Child runs inherit configs; never reuse their parent's scope/filter,
    # including when this child cannot establish a scope or a kill switch is off.
    inherited = getattr(previous, "_agent_history_filter", False)
    if inherited:
        previous = previous._previous_filter
    view = ModelContextView(history, run_config=run_config, allow_projection=allow_projection) if history else None
    source_reference_views = {}
    if history:
        history.model_view = view

    async def filter_input(call):
        if history:
            history.assert_current_scope()
        if previous:
            data = previous(call)
            if inspect.isawaitable(data):
                data = await data
            call = replace(call, model_data=data)
        if view is not None:
            return await view.filter(call)
        from .document_references import project_model_source_items
        from src.lib.context import get_current_user_id
        data = call.model_data
        items = await project_model_source_items(data.input, user_id=get_current_user_id(), cache=source_reference_views)
        return ModelInputData(input=items, instructions=data.instructions)
    setattr(filter_input, "_agent_history_filter", True)
    setattr(filter_input, "_previous_filter", previous)
    return replace(run_config, call_model_input_filter=filter_input)
