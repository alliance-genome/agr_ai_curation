"""Canonical input capture and model-only source projection.

Native Responses compaction owns working-context reduction. Tool age, count,
characters and per-tool size do not decide what the model may see.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import inspect
from typing import Any

from agents.exceptions import UserError
from agents.run import ModelInputData

from .run_history import RunHistory
from .context_compaction import compacted_window


class ContextRecoveryError(UserError):
    """A visible failure instead of silently losing required context."""


class HistoryStorageUnavailable(ContextRecoveryError):
    """History persistence failed before a required context checkpoint."""


@dataclass
class ModelContextView:
    history: RunHistory
    run_config: Any = None
    last_metrics: dict = field(default_factory=dict)
    source_reference_views: dict = field(default_factory=dict)

    async def filter(self, call) -> ModelInputData:
        original = call.model_data
        if self.history.context_policy is not None:
            self.history.context_policy.canonical_input = original.input
        refs = await self.history.capture_input(original.input, original.instructions)
        from .document_references import project_model_source_items
        if self.history.context_policy is not None:
            await self.history.context_policy.restore(original.input)
        working = self.history.context_policy.apply(original.input) if self.history.context_policy is not None else original.input
        items = await project_model_source_items(working,
            user_id=self.history.scope.owner_id, cache=self.source_reference_views)
        items = compacted_window(items, self.history.native_compaction_keys)
        current = ModelInputData(input=items, instructions=original.instructions)
        self.last_metrics = {
            "policy": "native_compaction", "enabled": True,
            "native_compactions": len(self.history.native_compaction_keys),
            "compacted_prefix_items": len(original.input) - len(items),
            "archive_healthy": self.history.healthy,
            "archived_item_count": len(refs), "input_item_count": len(items),
            "archive_write_failures": self.history.write_failures,
        }
        await self.history.record("context_view", self.last_metrics)
        from .extraction_trace_events import write_extraction_trace_event
        write_extraction_trace_event(event_type="runtime.context_view", output_summary=self.last_metrics,
                                     metadata={"agent": getattr(call.agent, "name", None)})
        await self.history.checkpoint(current.input, current.instructions, {
            "native_compactions": len(self.history.native_compaction_keys),
            "completed_tool_call_ids": [item.get("call_id") for item in original.input
                if isinstance(item, dict) and item.get("type") == "function_call_output"],
        })
        return current


def run_config_with_history(run_config, history: RunHistory | None):
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
    view = ModelContextView(history, run_config=run_config) if history else None
    from .context_capacity import ContextCapacityPolicy
    policy = history.context_policy if history is not None else None
    policy = policy or ContextCapacityPolicy(history)
    policy.reasoning_item_id_policy = run_config.reasoning_item_id_policy
    source_reference_views = {}
    if history:
        history.context_policy = policy
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
        policy.canonical_input = data.input
        items = await project_model_source_items(policy.apply(data.input), user_id=get_current_user_id(), cache=source_reference_views)
        return ModelInputData(input=items, instructions=data.instructions)
    setattr(filter_input, "_context_policy", policy)
    setattr(filter_input, "_run_history", history)
    setattr(filter_input, "_agent_history_filter", True)
    setattr(filter_input, "_previous_filter", previous)
    return replace(run_config, call_model_input_filter=filter_input)
