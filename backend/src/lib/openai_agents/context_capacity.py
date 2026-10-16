"""Capacity recovery for native Responses requests; no per-tool context quota.

The token-counting endpoint measures the SDK's actual wire input. Native
compaction handles the ordinary threshold. This module acts only when a request
would exceed the configured model's verified input/context capability.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import time
from typing import Any

from .run_history import exact_json

CONTEXT_READ_TOOL = "read_pending_context"
_COUNT_FIELDS = frozenset({"model", "input", "instructions", "tools", "tool_choice",
    "parallel_tool_calls", "reasoning", "text", "previous_response_id", "conversation", "truncation"})


def item_keys(items: list) -> list[str]:
    return [hashlib.sha256(exact_json(item).encode("utf-8")).hexdigest() for item in items]


def pending_start(items: list[dict]) -> int:
    """Keep the latest incoming user item or complete pending tool group intact."""
    if not items:
        return 0
    outputs = set()
    for item in reversed(items):
        if item.get("type") != "function_call_output":
            break
        outputs.add(item.get("call_id"))
    if outputs:
        start = next((i for i, item in enumerate(items)
            if item.get("type") == "function_call" and item.get("call_id") in outputs), 0)
        while start and items[start - 1].get("type") == "reasoning":
            start -= 1
        return start
    return len(items) - 1


def closed_boundaries(items: list[dict], stop: int) -> list[int]:
    """Legal completed prefixes, without orphaning any tool call or result."""
    opened, boundaries = set(), []
    for index, item in enumerate(items[:stop]):
        if item.get("type") == "function_call":
            opened.add(item.get("call_id"))
        elif item.get("type") == "function_call_output":
            opened.discard(item.get("call_id"))
        if not opened and item.get("type") != "reasoning":
            boundaries.append(index + 1)
    return boundaries


async def fitting_closed_prefix(request, items, stop, *, after=0):
    """Choose a complete prefix measured to fit the standalone endpoint."""
    boundaries = [boundary for boundary in closed_boundaries(items, stop) if boundary > after]
    low, high = 0, len(boundaries)
    while low < high:
        mid = (low + high + 1) // 2
        if await request.count(items[:boundaries[mid - 1]]) <= request.capacity:
            low = mid
        else:
            high = mid - 1
    return boundaries[low - 1] if low else 0


def matching_projection_anchor(projection, items):
    keys = item_keys(items)
    for anchor in projection.get("anchors", []):
        if anchor["keys"] and keys[:len(anchor["keys"])] == anchor["keys"]:
            return anchor
    return None


@dataclass
class PendingPayload:
    event_id: str
    body: str
    source: dict
    cursor: int = 0

    def page(self, end: int) -> dict:
        return {"status": "ok", "source_event_id": self.event_id, "source": self.source,
            "content": self.body[self.cursor:end], "encoding": self.source.get("encoding", "text"),
            "sha256": hashlib.sha256(self.body.encode("utf-8")).hexdigest(),
            "returned_range": {"start": self.cursor, "end": end},
            "total_chars": len(self.body), "complete": end == len(self.body)}


@dataclass
class ContextCapacityPolicy:
    history: Any = None
    replacement_keys: list[str] = field(default_factory=list)
    replacement_items: list = field(default_factory=list)
    canonical_input: list = field(default_factory=list)
    pending: list[PendingPayload] = field(default_factory=list)
    last_input: list = field(default_factory=list)
    last_output: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    request: Any = None
    reasoning_item_id_policy: str | None = None
    restored: bool = False
    state_dirty: bool = False
    deferred_tool_choice: str | None = None

    def apply(self, items: list) -> list:
        if self.replacement_keys and item_keys(items[:len(self.replacement_keys)]) == self.replacement_keys:
            return deepcopy(self.replacement_items) + deepcopy(items[len(self.replacement_keys):])
        return deepcopy(items)

    async def restore(self, items):
        if self.restored:
            return
        self.restored = True
        if self.history is not None and self.history.scope.session_reader:
            projection = await asyncio.to_thread(
                self.history.store.latest_context_projection, self.history.scope, items)
            if projection:
                anchor = matching_projection_anchor(projection, items)
                self.replacement_keys = anchor["keys"]
                from .context_compaction import compacted_window
                self.replacement_items = (compacted_window(projection["items"], set(projection.get("native_keys", [])))
                    if anchor["native_window"] else projection["items"])
                self.history.native_compaction_keys.update(projection.get("native_keys", []))
                for pending in projection.get("pending", []):
                    retained = await asyncio.to_thread(self.history.store.read, self.history.scope, pending["event_id"])
                    payload = retained["payload"]
                    self.pending.append(PendingPayload(pending["event_id"], payload["content"], payload["source"], pending["cursor"]))
                self._reconcile_delivered_pages(items)

    def _reconcile_delivered_pages(self, items):
        """Advance only over exact pages already committed in SDK history."""
        import ast
        import json
        calls = {item.get("call_id"): item.get("name") for item in items if item.get("type") == "function_call"}
        for item in items:
            if item.get("type") != "function_call_output" or calls.get(item.get("call_id")) != CONTEXT_READ_TOOL:
                continue
            raw = item.get("output")
            try:
                try:
                    page = json.loads(raw)
                except (ValueError, TypeError):
                    page = ast.literal_eval(raw)
            except (ValueError, SyntaxError, TypeError):
                continue
            if not isinstance(page, dict):
                continue
            for payload in self.pending:
                span = page.get("returned_range", {})
                start, end = span.get("start"), span.get("end")
                if (page.get("source_event_id") == payload.event_id
                    and page.get("sha256") == hashlib.sha256(payload.body.encode("utf-8")).hexdigest()
                    and type(start) is int and type(end) is int
                    and start <= payload.cursor < end <= len(payload.body)
                    and page.get("content") == payload.body[start:end]):
                    payload.cursor = end
                    self.state_dirty = True
        self.pending = [payload for payload in self.pending if payload.cursor < len(payload.body)]

    async def _save_projection(self, original: list, prepared: list):
        from .model_context_view import HistoryStorageUnavailable
        if self.history is None:
            raise HistoryStorageUnavailable("Exact context storage is required for capacity recovery.")
        from .context_compaction import compacted_window
        native_keys = self.history.native_compaction_keys
        canonical_window = compacted_window(original, native_keys)
        prepared_window = compacted_window(prepared, native_keys)
        value = {"source_item_keys": item_keys(original), "items": prepared,
            "anchors": [{"keys": item_keys(original), "native_window": False},
                        {"keys": item_keys(canonical_window), "native_window": True},
                        {"keys": item_keys(prepared), "native_window": False},
                        {"keys": item_keys(prepared_window), "native_window": True}],
            "native_keys": sorted(native_keys),
            "pending": [{"event_id": payload.event_id, "cursor": payload.cursor} for payload in self.pending]}
        ref = await self.history.record("context_capacity_projection", value)
        if not ref:
            raise HistoryStorageUnavailable("Working context could not be saved; completed actions have not been repeated.")
        self.replacement_keys, self.replacement_items = value["source_item_keys"], deepcopy(prepared)
        self.state_dirty = False

    def _ingestion_settings(self, settings):
        choice = settings.tool_choice
        if isinstance(choice, str) and choice not in {"auto", "none", "required", CONTEXT_READ_TOOL}:
            self.deferred_tool_choice = choice
        return replace(settings, tool_choice=CONTEXT_READ_TOOL, parallel_tool_calls=False)

    async def prepare(self, request, items: list, settings):
        from .model_context_view import ContextRecoveryError
        self.request = request
        await self.restore(self.canonical_input or items)
        original = deepcopy(self.canonical_input or items)
        prepared = self.apply(items)
        # Count the choice that will actually be sent. Restoring it after recovery
        # would reject a request that could fit after compacting closed history.
        if self.pending:
            settings = self._ingestion_settings(settings)
        elif self.deferred_tool_choice:
            settings = replace(settings, tool_choice=self.deferred_tool_choice)
        request.set_settings(settings)
        count = await request.count(prepared)
        self.metrics = {"input_tokens_before": count, "input_capacity": request.capacity,
            "standalone_compactions": 0, "capacity_ingress_payloads": 0}
        if count > request.capacity:
            if self.history is None or not self.history.healthy:
                from .model_context_view import HistoryStorageUnavailable
                raise HistoryStorageUnavailable("Exact context storage is required before capacity recovery.")
            compacted_prefix_size = 0
            while count > request.capacity:
                stop = pending_start(prepared)
                # /compact input must fit too. Each iteration consumes additional
                # original history; opaque state alone is never retried.
                boundary = await fitting_closed_prefix(request, prepared, stop,
                                                       after=compacted_prefix_size)
                if not boundary:
                    break
                compacted = await request.compact(prepared[:boundary])
                candidate = [*compacted, *prepared[boundary:]]
                after = await request.count(candidate)
                if after >= count:
                    break
                prepared, count = candidate, after
                compacted_prefix_size = len(compacted)
                self.metrics["standalone_compactions"] += 1
            if count > request.capacity:
                if self.history is None or not request.has_context_tool:
                    raise ContextRecoveryError("This runtime has no exact context-ingestion tool for an input larger than model capacity.")
                # Only actual overflow reaches this path. Consider text anywhere
                # in restored history too: a single old item may exceed /compact's
                # own capacity. Preserve exchanges and queue source data in order.
                settings = self._ingestion_settings(settings)
                request.set_settings(settings)
                count = await request.count(prepared)
                calls = {item.get("call_id"): item.get("name") for item in prepared
                    if item.get("type") == "function_call"}
                candidates = []
                for index, item in enumerate(prepared):
                    key = "output" if item.get("type") == "function_call_output" else "content"
                    body, encoding = item.get(key), "text"
                    if calls.get(item.get("call_id")) == CONTEXT_READ_TOOL:
                        continue  # Never recursively ingest an ingestion page.
                    if isinstance(body, list) and all(isinstance(block, dict) and block.get("type") in {"input_text", "output_text", "text"} for block in body):
                        body, encoding = exact_json(body), "json"
                    if isinstance(body, str) and body:
                        candidates.append((index, key, body, encoding))
                queued = []
                for index, key, body, encoding in sorted(candidates, key=lambda entry: len(entry[2]), reverse=True):
                    item = prepared[index]
                    source = {"role": item.get("role", "tool"), "call_id": item.get("call_id"),
                        "tool_name": calls.get(item.get("call_id")), "field": key, "encoding": encoding}
                    ref = await self.history.record("context_ingress", {"source": source, "content": body})
                    if not ref:
                        from .model_context_view import HistoryStorageUnavailable
                        raise HistoryStorageUnavailable("Oversized input could not be retained exactly.")
                    descriptor = exact_json({"context_ingress_event": ref,
                        "message": "This input exceeds actual model capacity. Read all pending context before completing the task; it is retained exactly.",
                        "read_tool": CONTEXT_READ_TOOL, "exact_recovery": {"tool": "read_run_history", "event_id": ref, "detail_path": "payload.content"},
                        "source": source, "total_chars": len(body)})
                    candidate = [*prepared]
                    replacement = descriptor
                    if isinstance(item.get(key), list):
                        # Output-message content is a block list on the Responses
                        # wire, even when its exact body lives in the archive.
                        block_type = item[key][0]["type"] if item[key] else "input_text"
                        block = {"type": block_type, "text": descriptor}
                        if block_type == "output_text":
                            block["annotations"] = []
                        replacement = [block]
                    candidate[index] = {**item, key: replacement}
                    after = await request.count(candidate)
                    if after >= count:
                        continue
                    prepared, count = candidate, after
                    queued.append((index, PendingPayload(ref, body, source)))
                    self.metrics["capacity_ingress_payloads"] += 1
                    if count <= request.capacity:
                        break
                self.pending.extend(payload for _, payload in sorted(queued, key=lambda entry: entry[0]))
                count = await request.count(prepared)
                if count > request.capacity or not self.pending:
                    raise ContextRecoveryError("The request's instructions, tool definitions, or non-text inputs exceed model capacity; textual ingestion cannot reduce them.")
            self.state_dirty = True
        self.last_input, self.last_output = deepcopy(prepared), []
        if self.state_dirty:
            await self._save_projection(original, prepared)
        self.metrics.update(input_tokens_after=count, pending_payloads=len(self.pending),
            token_count_requests=request.count_calls, token_count_duration_ms=request.count_duration_ms)
        return prepared, settings

    def observe_output(self, output):
        from agents.items import _output_item_to_input_item
        from agents.run_internal.items import run_item_to_input_item
        from types import SimpleNamespace
        self.last_output = []
        for item in output or []:
            converted = _output_item_to_input_item(item)
            run_item = SimpleNamespace(
                type="reasoning_item" if converted.get("type") == "reasoning" else "model_output",
                to_input_item=lambda value=converted: value,
            )
            self.last_output.append(run_item_to_input_item(run_item, self.reasoning_item_id_policy))
            if converted.get("type") == "function_call":
                name = converted.get("name")
                qualified_name = f"{converted['namespace']}.{name}" if converted.get("namespace") else name
                if self.deferred_tool_choice in {name, qualified_name}:
                    self.deferred_tool_choice = None

    async def checkpoint_output(self, *, native_compaction=False):
        # The SDK persists response outputs separately. Record a candidate anchored
        # to both pre- and post-compaction windows; restore selects only an anchor
        # present in durable SDK history, never merely the newest event.
        if self.replacement_keys and (self.state_dirty or native_compaction):
            await self._save_projection([*self.canonical_input, *self.last_output],
                                        [*self.last_input, *self.last_output])

    async def next_page(self, call_id: str) -> dict:
        """Return the largest exact page fitting this actual request's capacity."""
        from .context_compaction import compacted_window
        from .model_context_view import ContextRecoveryError
        if not self.pending or self.request is None:
            return {"status": "complete", "pending_payloads": 0}
        self.history.assert_current_scope()
        payload = self.pending[0]
        prefix = [*self.last_input, *self.last_output]
        prefix = compacted_window(prefix, self.history.native_compaction_keys)
        def render(end):
            return {**payload.page(end), "pending_payloads": len(self.pending) - int(end == len(payload.body))}
        def with_page(end):
            return [*prefix, {"type": "function_call_output", "call_id": call_id, "output": str(render(end))}]
        if await self.request.count(with_page(payload.cursor + 1)) > self.request.capacity:
            stop = pending_start(prefix + [{"type": "function_call_output", "call_id": call_id, "output": ""}])
            boundary = await fitting_closed_prefix(self.request, prefix, stop)
            if not boundary:
                raise ContextRecoveryError("Required context leaves no space for source ingestion.")
            compacted = await self.request.compact(prefix[:boundary])
            prefix = [*compacted, *prefix[boundary:]]
            await self._save_projection([*self.canonical_input, *self.last_output], prefix)
        low, high = payload.cursor, len(payload.body)
        while low < high:
            mid = (low + high + 1) // 2
            proposal = with_page(mid)
            if await self.request.count(proposal) <= self.request.capacity:
                low = mid
            else:
                high = mid - 1
        if low == payload.cursor:
            raise ContextRecoveryError("No source content fits beside the current required context; capacity ingestion made no progress.")
        page = render(low)
        # Publish coverage only with the actual exact page. A failed model call
        # cannot be mistaken for successful task completion (the run fails).
        ref = await self.history.record("context_ingress_coverage", {
            "source_event_id": payload.event_id, "range": page["returned_range"], "sha256": page["sha256"]})
        if not ref:
            from .model_context_view import HistoryStorageUnavailable
            raise HistoryStorageUnavailable("Context coverage could not be saved.")
        self.state_dirty = True
        payload.cursor = low
        if low == len(payload.body):
            self.pending.pop(0)
        return page


class NativeCapacityRequest:
    """Pinned SDK wire construction plus the official token-count/compact APIs."""

    def __init__(self, model, settings, *, instructions, tools, output_schema, handoffs,
                 previous_response_id=None, conversation_id=None, prompt=None, compact_call=None):
        from .model_context_view import ContextRecoveryError
        from src.lib.config.models_loader import get_model
        from .config import get_agent_model_capacity_overrides
        self.model = model
        self.client = model._get_client()
        self.kwargs = dict(system_instructions=instructions, model_settings=settings,
            tools=tools, output_schema=output_schema, handoffs=handoffs,
            previous_response_id=previous_response_id, conversation_id=conversation_id, prompt=prompt)
        # Capacity and counting must see the same effective request as HTTP/WS.
        wire = self._wire([])
        self.effective_model = wire["model"]
        self.effective_instructions = wire.get("instructions")
        definition = get_model(self.effective_model)
        overrides = get_agent_model_capacity_overrides().get(self.effective_model, {})
        limits = {name: overrides.get(name, getattr(definition, name, None))
            for name in ("context_window_tokens", "max_input_tokens", "max_output_tokens")}
        if not all(type(value) is int and value > 0 for value in limits.values()):
            raise ContextRecoveryError(f"Model {self.effective_model} needs verified input/context/output capabilities in the model catalog.")
        output_tokens = wire.get("max_output_tokens") or limits["max_output_tokens"]
        if type(output_tokens) is not int or not 0 < output_tokens <= limits["max_output_tokens"]:
            raise ContextRecoveryError("Requested output exceeds the configured model's output capability.")
        self.capacity = min(limits["max_input_tokens"], limits["context_window_tokens"] - output_tokens)
        self.has_context_tool = any(getattr(tool, "name", None) == CONTEXT_READ_TOOL for tool in tools)
        self.compact_call = compact_call
        self.count_calls = 0
        self.count_duration_ms = 0.0

    def set_settings(self, settings):
        self.kwargs["model_settings"] = settings

    def _wire(self, items):
        from openai import Omit, NotGiven
        from .model_context_view import ContextRecoveryError
        wire = self.model._build_response_create_kwargs(input=deepcopy(items), **self.kwargs)
        extra = wire.get("extra_body") or {}
        if not isinstance(extra, dict):
            raise ContextRecoveryError("Responses extra_body must be a mapping.")
        # These fields are owned by the SDK/context boundary. An extra-body
        # override would replace recovered input or forced ingestion settings.
        owned = {"input", "tools", "tool_choice", "parallel_tool_calls", "context_management",
                 "previous_response_id", "conversation", "truncation"}
        if owned.intersection(extra):
            raise ContextRecoveryError("Configure SDK-owned context fields through ModelSettings, not extra_body.")
        wire = {key: value for key, value in {**wire, **extra}.items()
                if not isinstance(value, (Omit, NotGiven))}
        if wire.get("prompt"):
            raise ContextRecoveryError("Stored prompt templates cannot be counted by this input-based context policy.")
        if wire.get("previous_response_id") or wire.get("conversation"):
            raise ContextRecoveryError("Use application-owned working context without server-managed conversation chaining.")
        if wire.get("truncation") == "auto":
            raise ContextRecoveryError("Automatic provider truncation would bypass exact context continuation.")
        return wire

    async def count(self, items):
        wire = self._wire(items)
        body = {key: value for key, value in wire.items() if key in _COUNT_FIELDS}
        started = time.monotonic()
        try:
            result = await self.client.responses.input_tokens.count(**body)
            return result.input_tokens
        finally:
            self.count_calls += 1
            self.count_duration_ms += (time.monotonic() - started) * 1000

    async def compact(self, items):
        if self.compact_call is None:
            raise RuntimeError("Capacity compaction requires the measured provider callback")
        response = await self.compact_call(items, model=self.effective_model, instructions=self.effective_instructions)
        # /compact returns the whole canonical window. Do not extract only the
        # opaque compaction item or prune retained user/assistant messages.
        return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in response.output]
