"""Effective model-request measurement and known-invalid request protection.

Every Agents SDK model call resolves its model through the SDK's per-turn model
resolution (``agents.run_internal.run_loop.get_model``). ``install_model_request_measurement``
wraps that resolution once per process so every runtime (standard chat, flows,
streamed and non-streamed specialists, validators, Agent Studio, one-shot
helpers such as hierarchy and figure-locator resolution), every tool-loop turn,
and every SDK retry attempt is measured from the exact arguments handed to the
provider adapter, before the adapter sends anything. WebSocket and HTTP
transports and OpenAI-compatible providers share this boundary.

Direct provider clients that bypass the Agents SDK measure their exact request
keyword arguments through ``measure_direct_request``.

The record keeps these categories separate and never reports an unobserved
value as zero:

- ``outbound``: what this request carries (instructions, input/history, tool
  results, tool definitions, output schema), in characters and UTF-8 bytes;
- ``model_visible``: instructions + input + initially visible tool definitions +
  handoffs + output schema, with a characters/4 token *estimate*;
- ``deferred_tools``: definitions transported for hosted tool search that are
  not loaded into model context unless a later ``tool_search_output`` input item
  shows them loaded (then they are counted in ``outbound.input``);
- ``provider_managed``: context the provider holds or loads that this process
  cannot observe (``previous_response_id`` history, stored conversations,
  prompt templates, definitions loaded by hosted tool search in this response);
- ``provider_usage``: tokens the provider reported for the request, or
  ``status="not_reported"``. It is correlation only, never a second cost record:
  cost stays on the SDK response/generation span keyed by the provider response id.

Application-stored data sizes stay with ``runtime_payload_budget.provider_context_preflight``
(``measurement_scope="application_payload"``).

Blocking is limited to known provider field constraints (currently the OpenAI
Responses ``instructions`` ceiling). Application size thresholds are warnings.
Nothing here trims, slices or drops request content.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping
import json
import logging
import threading
from typing import Any
import uuid

from agents.models.interface import Model

from src.lib.observability.payload_contracts import (
    PayloadContractViolation,
    report_payload_contract_violation,
)
from src.lib.openai_agents.tool_surface import ToolSurface, canonical_tool_name
from src.lib.runtime_payload_budget import estimate_tokens_from_chars

logger = logging.getLogger(__name__)

MEASUREMENT_EVENT_TYPE = "runtime.model_request_measurement"
INSTRUCTIONS_COMPONENT = "openai_responses.instructions"
INSTRUCTIONS_SETTING = "OPENAI_INSTRUCTIONS_MAX_CHARS"

_MEASURED_FLAG = "_ai_curation_model_request_measured"
_SDK_GET_MODEL: Any = None
_MEASURED_GET_MODEL: Any = None
_REPORTED_BLOCKS_MAX = 1024
_reported_blocks: "OrderedDict[tuple[Any, ...], None]" = OrderedDict()
_reported_blocks_lock = threading.Lock()


class ModelRequestBlockedError(PayloadContractViolation):
    """A known-invalid model request was blocked before it reached the provider."""

    def __init__(
        self,
        *,
        component: str,
        field: str,
        measured: int,
        unit: str,
        limit: int,
        setting: str,
        provider: str,
        model: str | None,
        agent: str | None,
        correlation: Mapping[str, Any],
    ) -> None:
        references = ", ".join(
            f"{key}={value}"
            for key, value in correlation.items()
            if key in {"trace_id", "run_id", "job_id", "workflow_id"} and value
        )
        message = (
            f"Model request for agent '{agent or 'unknown'}' was blocked before sending: "
            f"{field} is {measured:,} {unit}, above the {provider} limit of {limit:,} "
            f"{unit} (setting {setting}). No provider call was made and saved "
            "application data was not changed; reduce the content placed in "
            f"{field} and retry."
        )
        if references:
            message = f"{message} ({references})"
        super().__init__(
            category="provider_request_blocked",
            component=component,
            message=message,
            measured=measured,
            unit=unit,
            limit=limit,
            setting=setting,
            field=field,
        )
        self.provider = provider
        self.model = model
        self.agent = agent
        self.correlation = dict(correlation)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json", exclude_none=True)
    return str(value)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json", exclude_none=True)
        if isinstance(dumped, Mapping):
            return dumped
    return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=_json_default, separators=(",", ":"))


def _size(value: Any) -> dict[str, int]:
    text = _text(value)
    return {"chars": len(text), "bytes": len(text.encode("utf-8"))}


def _empty_size() -> dict[str, int]:
    return {"chars": 0, "bytes": 0}


def _add(target: dict[str, int], size: Mapping[str, int]) -> None:
    target["chars"] += size["chars"]
    target["bytes"] += size["bytes"]


# ---------------------------------------------------------------------------
# Component measurement
# ---------------------------------------------------------------------------


def flatten_loaded_tool_definitions(tools: Any) -> list[Any]:
    """Function/custom definitions a ``tool_search_output`` loaded.

    Hosted tool search returns namespaces (``{"type": "namespace", "tools":
    [...]}``) and may also return top-level definitions; the loaded count is the
    number of member definitions, not the number of namespaces.
    """

    if not isinstance(tools, list):
        return []
    definitions: list[Any] = []
    for raw_tool in tools:
        tool = _as_mapping(raw_tool)
        if tool.get("type") == "namespace":
            members = tool.get("tools")
            if isinstance(members, list):
                definitions.extend(members)
        else:
            definitions.append(raw_tool)
    return definitions


def _measure_input(input_value: Any) -> dict[str, Any]:
    items: list[Any]
    if input_value is None:
        items = []
    elif isinstance(input_value, str):
        items = [{"role": "user", "content": input_value}]
    else:
        items = list(input_value)

    total = _empty_size()
    messages: dict[str, dict[str, int]] = {}
    tool_calls = {"count": 0, **_empty_size()}
    tool_results = {"count": 0, **_empty_size()}
    reasoning = {"count": 0, **_empty_size()}
    loaded_tool_definitions = {
        "count": 0,
        "namespaces": 0,
        "items": 0,
        "definition_chars": 0,
        **_empty_size(),
    }
    loaded_names: set[str] = set()
    tool_search_calls = 0
    other = {"count": 0, **_empty_size()}
    largest_result: dict[str, Any] | None = None
    call_names: dict[str, str] = {}
    results: list[tuple[Mapping[str, Any], dict[str, int]]] = []

    for raw_item in items:
        item = _as_mapping(raw_item)
        size = _size(item if item else raw_item)
        _add(total, size)
        item_type = str(item.get("type") or "")
        role = item.get("role")
        if item_type == "tool_search_output":
            loaded_tool_definitions["items"] += 1
            tools = item.get("tools")
            definitions = flatten_loaded_tool_definitions(tools)
            loaded_tool_definitions["count"] += len(definitions)
            loaded_tool_definitions["namespaces"] += sum(
                1
                for tool in (tools if isinstance(tools, list) else [])
                if _as_mapping(tool).get("type") == "namespace"
            )
            loaded_tool_definitions["definition_chars"] += sum(
                _size(definition)["chars"] for definition in definitions
            )
            loaded_names.update(
                name
                for name in (
                    canonical_tool_name(_as_mapping(definition).get("name"))
                    for definition in definitions
                )
                if name
            )
            _add(loaded_tool_definitions, size)
        elif item_type.endswith("_call_output"):
            tool_results["count"] += 1
            _add(tool_results, size)
            results.append((item, size))
        elif item_type.endswith("_call") or item_type == "tool_search_call":
            tool_calls["count"] += 1
            _add(tool_calls, size)
            if item_type == "tool_search_call":
                tool_search_calls += 1
            call_id = item.get("call_id")
            if call_id and item.get("name"):
                call_names[str(call_id)] = canonical_tool_name(item.get("name"))
        elif item_type == "reasoning":
            reasoning["count"] += 1
            _add(reasoning, size)
        elif role or item_type == "message":
            bucket = messages.setdefault(str(role or "unknown"), {"count": 0, **_empty_size()})
            bucket["count"] += 1
            _add(bucket, size)
        else:
            other["count"] += 1
            _add(other, size)

    for item, size in results:
        if largest_result is None or size["chars"] > largest_result["chars"]:
            call_id = str(item.get("call_id") or "")
            largest_result = {
                "chars": size["chars"],
                "bytes": size["bytes"],
                "tool_name": call_names.get(call_id) or "unknown",
                "call_id": call_id or None,
            }

    return {
        "item_count": len(items),
        **total,
        "messages_by_role": messages,
        "tool_calls": {**tool_calls, "tool_search_calls": tool_search_calls},
        "tool_results": {**tool_results, "largest": largest_result},
        "reasoning_items": reasoning,
        "loaded_deferred_tool_definitions": {
            **loaded_tool_definitions,
            "names": sorted(loaded_names),
        },
        "other_items": other,
    }


def _tool_definition(tool: Any) -> tuple[dict[str, Any], bool, bool]:
    """Return (definition, deferred, hosted) for one SDK tool."""

    from agents.tool import FunctionTool, HostedMCPTool

    if isinstance(tool, FunctionTool):
        definition = {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.params_json_schema,
            "strict": tool.strict_json_schema,
        }
        namespace = getattr(tool, "_tool_namespace", None)
        if namespace:
            definition["namespace"] = namespace
        return definition, bool(tool.defer_loading), False
    if isinstance(tool, HostedMCPTool):
        config = dict(tool.tool_config)
        return config, bool(config.get("defer_loading")), True
    # Other hosted/custom tools are sized by type and name only; their
    # provider-side definitions are not observable here.
    return (
        {"type": type(tool).__name__, "name": getattr(tool, "name", None)},
        bool(getattr(tool, "defer_loading", False)),
        True,
    )


def _measure_tools(tools: Any, handoffs: Any) -> dict[str, Any]:
    visible = {"count": 0, **_empty_size()}
    deferred = {"count": 0, **_empty_size()}
    hosted_types: list[str] = []
    namespace_headers: dict[str, Any] = {}
    for tool in list(tools or []):
        definition, is_deferred, is_hosted = _tool_definition(tool)
        size = _size(definition)
        bucket = deferred if is_deferred else visible
        bucket["count"] += 1
        _add(bucket, size)
        if is_hosted:
            hosted_types.append(type(tool).__name__)
        namespace = definition.get("namespace") if not is_hosted else None
        if namespace and namespace not in namespace_headers:
            namespace_headers[namespace] = {
                "name": namespace,
                "description": getattr(tool, "_tool_namespace_description", None),
            }
    headers = {"count": len(namespace_headers), **_empty_size()}
    for header in namespace_headers.values():
        _add(headers, _size(header))
    handoff_size = {"count": 0, **_empty_size()}
    for handoff in list(handoffs or []):
        handoff_size["count"] += 1
        _add(
            handoff_size,
            _size(
                {
                    "name": getattr(handoff, "tool_name", None),
                    "description": getattr(handoff, "tool_description", None),
                    "parameters": getattr(handoff, "input_json_schema", None),
                }
            ),
        )
    return {
        "initially_visible": visible,
        "deferred": deferred,
        "hosted_tool_types": sorted(set(hosted_types)),
        "namespace_headers": headers,
        "handoffs": handoff_size,
    }


def _measure_output_schema(output_schema: Any) -> dict[str, Any] | None:
    if output_schema is None:
        return None
    is_plain_text = getattr(output_schema, "is_plain_text", None)
    if callable(is_plain_text) and is_plain_text():
        return None
    json_schema = getattr(output_schema, "json_schema", None)
    schema = json_schema() if callable(json_schema) else output_schema
    name_attr = getattr(output_schema, "name", None)
    name = name_attr() if callable(name_attr) else getattr(output_schema, "__name__", None)
    return {"name": str(name) if name else None, **_size(schema)}


def build_measurement(
    *,
    runtime: str,
    provider: str,
    api: str,
    transport: str,
    model: str | None,
    instructions: Any,
    input_value: Any,
    tools: Any = None,
    handoffs: Any = None,
    output_schema: Any = None,
    previous_response_id: str | None = None,
    conversation_id: str | None = None,
    prompt: Any = None,
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure one outbound model request without copying its content."""

    instructions_size = _size(instructions)
    input_measure = _measure_input(input_value)
    tool_measure = _measure_tools(tools, handoffs)
    schema_measure = _measure_output_schema(output_schema)

    visible_chars = (
        instructions_size["chars"]
        + input_measure["chars"]
        + tool_measure["initially_visible"]["chars"]
        + tool_measure["handoffs"]["chars"]
        + (schema_measure["chars"] if schema_measure else 0)
    )
    outbound_chars = visible_chars + tool_measure["deferred"]["chars"]

    provider_managed: list[dict[str, str]] = []
    if previous_response_id:
        provider_managed.append(
            {"component": "previous_response_history", "status": "unobservable"}
        )
    if conversation_id:
        provider_managed.append(
            {"component": "stored_conversation_history", "status": "unobservable"}
        )
    if prompt is not None:
        provider_managed.append({"component": "prompt_template", "status": "unobservable"})
    if tool_measure["deferred"]["count"]:
        provider_managed.append(
            {
                "component": "hosted_tool_search_loaded_definitions_this_response",
                "status": "provider_managed",
            }
        )

    return {
        "measurement_id": uuid.uuid4().hex,
        "measurement_scope": "model_request",
        "measurement_basis": (
            "agents_sdk_model_call" if runtime == "agents_sdk" else "direct_client_kwargs"
        ),
        "transport_payload_observed": runtime != "agents_sdk",
        "runtime": runtime,
        "provider": provider,
        "api": api,
        "transport": transport,
        "model": model,
        **dict(identity or {}),
        "outbound": {
            "json_chars": outbound_chars,
            "instructions": instructions_size,
            "input": input_measure,
            "tools": tool_measure,
            "output_schema": schema_measure,
        },
        "model_visible": {
            "json_chars": visible_chars,
            "estimated_tokens": estimate_tokens_from_chars(visible_chars),
            "estimate_basis": "characters_div_4",
        },
        "deferred_tools": {
            "count": tool_measure["deferred"]["count"],
            "json_chars": tool_measure["deferred"]["chars"],
            "loaded_status": (
                "provider_managed" if tool_measure["deferred"]["count"] else "not_applicable"
            ),
            "loaded_definitions_in_input": input_measure["loaded_deferred_tool_definitions"][
                "count"
            ],
        },
        "provider_managed": provider_managed,
        "warnings": [],
        "provider_usage": {"status": "pending"},
    }


# ---------------------------------------------------------------------------
# Limits and warnings
# ---------------------------------------------------------------------------


def _apply_warnings(measurement: dict[str, Any]) -> None:
    from src.lib.openai_agents.config import (
        get_model_request_tool_result_warning_chars,
        get_model_request_warning_estimated_tokens,
    )

    warnings: list[dict[str, Any]] = measurement["warnings"]
    token_limit = get_model_request_warning_estimated_tokens()
    estimated = measurement["model_visible"]["estimated_tokens"]
    if estimated >= token_limit:
        warnings.append(
            {
                "component": "model_visible_request",
                "measured": estimated,
                "unit": "estimated_tokens",
                "threshold": token_limit,
                "setting": "MODEL_REQUEST_WARNING_ESTIMATED_TOKENS",
            }
        )
    largest = measurement["outbound"]["input"]["tool_results"]["largest"]
    result_limit = get_model_request_tool_result_warning_chars()
    if largest and largest["chars"] >= result_limit:
        warnings.append(
            {
                "component": "tool_result",
                "tool_name": largest["tool_name"],
                "measured": largest["chars"],
                "unit": "characters",
                "threshold": result_limit,
                "setting": "MODEL_REQUEST_TOOL_RESULT_WARNING_CHARS",
            }
        )


def _blocked_violation(measurement: Mapping[str, Any]) -> ModelRequestBlockedError | None:
    """Return the known provider field violation for this request, if any."""

    from src.lib.openai_agents.config import get_openai_instructions_max_chars

    if measurement["provider"] != "openai" or measurement["api"] != "responses":
        return None
    limit = get_openai_instructions_max_chars()
    measured = measurement["outbound"]["instructions"]["chars"]
    if measured <= limit:
        return None
    return ModelRequestBlockedError(
        component=INSTRUCTIONS_COMPONENT,
        field="instructions",
        measured=measured,
        unit="characters",
        limit=limit,
        setting=INSTRUCTIONS_SETTING,
        provider=measurement["provider"],
        model=measurement.get("model"),
        agent=measurement.get("agent_name"),
        correlation=_correlation(measurement),
    )


def _correlation(measurement: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "trace_id",
        "run_id",
        "job_id",
        "workflow_id",
        "node_id",
        "activity",
        "agent_id",
        "agent_role",
        "attempt",
        "sdk_trace_id",
        "sdk_span_id",
        "transport",
        "api",
        "measurement_id",
    )
    correlation = {key: measurement.get(key) for key in keys if measurement.get(key) is not None}
    outbound = measurement["outbound"]
    correlation.update(
        {
            "input_chars": outbound["input"]["chars"],
            "tool_result_count": outbound["input"]["tool_results"]["count"],
            "tool_definition_chars": outbound["tools"]["initially_visible"]["chars"],
            "deferred_tool_definition_chars": outbound["tools"]["deferred"]["chars"],
            "output_schema_chars": (outbound["output_schema"] or {}).get("chars"),
            "estimated_tokens": measurement["model_visible"]["estimated_tokens"],
        }
    )
    return correlation


def _report_block_once(violation: ModelRequestBlockedError, measurement: Mapping[str, Any]) -> None:
    """Report one grouped Sentry event per underlying blocked request.

    A supervisor or flow may re-issue the same oversized request (a later turn,
    a re-invoked specialist, possibly a few characters different). Those
    repeats for the same agent and component in the same trace/run are logged
    but not captured again, and the violation is marked captured so wrapper
    boundaries do not capture it either.
    """

    scope = measurement.get("trace_id") or measurement.get("run_id")
    key = (scope, violation.component, measurement.get("agent_name"))
    if scope:
        with _reported_blocks_lock:
            if key in _reported_blocks:
                logger.warning(
                    "model request blocked again (already reported) component=%s "
                    "agent=%s measured=%s limit=%s trace_id=%s",
                    violation.component,
                    measurement.get("agent_name"),
                    violation.measured,
                    violation.limit,
                    measurement.get("trace_id"),
                )
                setattr(violation, "_ai_curation_sentry_captured", True)
                return
            _reported_blocks[key] = None
            while len(_reported_blocks) > _REPORTED_BLOCKS_MAX:
                _reported_blocks.popitem(last=False)
    report_payload_contract_violation(
        violation,
        phase="model_request",
        provider=violation.provider,
        model=violation.model,
        agent=violation.agent,
        trace_id=measurement.get("trace_id"),
        session_id=measurement.get("session_id"),
        flow_id=measurement.get("workflow_id"),
        correlation=violation.correlation,
    )
    # Duplicate suppression must hold even when Sentry was unavailable; the
    # structured log above is the retained record in that case.
    setattr(violation, "_ai_curation_sentry_captured", True)


def enforce_and_announce(measurement: dict[str, Any]) -> None:
    """Apply warnings, then block a known-invalid request before it is sent."""

    _apply_warnings(measurement)
    violation = _blocked_violation(measurement)
    if violation is not None:
        measurement["outcome"] = "blocked_before_send"
        measurement["provider_usage"] = {"status": "not_sent"}
        measurement["blocked"] = violation.diagnostic()
        _publish(measurement)
        _report_block_once(violation, measurement)
        raise violation
    if measurement["warnings"]:
        logger.warning(
            "model request size warning agent=%s provider=%s model=%s warnings=%s "
            "estimated_tokens=%s trace_id=%s",
            measurement.get("agent_name"),
            measurement["provider"],
            measurement.get("model"),
            json.dumps(measurement["warnings"], sort_keys=True),
            measurement["model_visible"]["estimated_tokens"],
            measurement.get("trace_id"),
        )


# ---------------------------------------------------------------------------
# Usage correlation and publication
# ---------------------------------------------------------------------------


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def usage_from_model_response(response: Any) -> dict[str, Any]:
    """Provider usage from an SDK ``ModelResponse``; zero-filled SDK defaults are not usage."""

    usage = getattr(response, "usage", None)
    if usage is None or not getattr(usage, "requests", 0):
        # The SDK substitutes Usage() (requests=0, all zeros) when the provider
        # reported nothing; that is missing usage, not zero tokens.
        return {"status": "not_reported"}
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return {
        "status": "reported",
        "source": "provider_response",
        "input_tokens": _optional_int(getattr(usage, "input_tokens", None)),
        "cached_input_tokens": _optional_int(getattr(input_details, "cached_tokens", None)),
        "output_tokens": _optional_int(getattr(usage, "output_tokens", None)),
        "reasoning_output_tokens": _optional_int(
            getattr(output_details, "reasoning_tokens", None)
        ),
        "total_tokens": _optional_int(getattr(usage, "total_tokens", None)),
    }


def usage_from_provider_payload(usage: Any) -> dict[str, Any]:
    """Provider usage from a raw Responses/Chat Completions usage object."""

    mapping = _as_mapping(usage)
    if not mapping:
        return {"status": "not_reported"}
    input_details = _as_mapping(
        mapping.get("input_tokens_details") or mapping.get("prompt_tokens_details")
    )
    output_details = _as_mapping(
        mapping.get("output_tokens_details") or mapping.get("completion_tokens_details")
    )
    return {
        "status": "reported",
        "source": "provider_response",
        "input_tokens": _optional_int(
            mapping.get("input_tokens", mapping.get("prompt_tokens"))
        ),
        "cached_input_tokens": _optional_int(input_details.get("cached_tokens")),
        "output_tokens": _optional_int(
            mapping.get("output_tokens", mapping.get("completion_tokens"))
        ),
        "reasoning_output_tokens": _optional_int(output_details.get("reasoning_tokens")),
        "total_tokens": _optional_int(mapping.get("total_tokens")),
    }


def record_outcome(
    measurement: dict[str, Any],
    *,
    outcome: str,
    usage: Mapping[str, Any] | None = None,
    response_id: str | None = None,
    provider_request_id: str | None = None,
    error_type: str | None = None,
) -> None:
    """Attach the provider outcome/usage to the measurement and publish it once."""

    if measurement.get("_published"):
        return
    measurement["outcome"] = outcome
    measurement["provider_usage"] = dict(usage or {"status": "not_reported"})
    if response_id:
        measurement["provider_response_id"] = response_id
    if provider_request_id:
        measurement["provider_request_id"] = provider_request_id
    if error_type:
        measurement["error_type"] = error_type
    _publish(measurement)


def _publish(measurement: dict[str, Any]) -> None:
    measurement["_published"] = True
    record = {key: value for key, value in measurement.items() if not key.startswith("_")}
    outbound = record["outbound"]
    usage = record["provider_usage"]
    logger.info(
        "model_request_measurement runtime=%s agent=%s provider=%s api=%s transport=%s "
        "model=%s attempt=%s outcome=%s instructions_chars=%s input_chars=%s "
        "input_items=%s tool_results=%s tool_results_chars=%s visible_tools=%s "
        "visible_tool_chars=%s deferred_tools=%s deferred_tool_chars=%s "
        "output_schema_chars=%s estimated_tokens=%s usage_status=%s "
        "provider_input_tokens=%s provider_cached_input_tokens=%s trace_id=%s",
        record["runtime"],
        record.get("agent_name"),
        record["provider"],
        record["api"],
        record["transport"],
        record.get("model"),
        record.get("attempt"),
        record.get("outcome"),
        outbound["instructions"]["chars"],
        outbound["input"]["chars"],
        outbound["input"]["item_count"],
        outbound["input"]["tool_results"]["count"],
        outbound["input"]["tool_results"]["chars"],
        outbound["tools"]["initially_visible"]["count"],
        outbound["tools"]["initially_visible"]["chars"],
        outbound["tools"]["deferred"]["count"],
        outbound["tools"]["deferred"]["chars"],
        (outbound["output_schema"] or {}).get("chars"),
        record["model_visible"]["estimated_tokens"],
        usage.get("status"),
        usage.get("input_tokens"),
        usage.get("cached_input_tokens"),
        record.get("trace_id"),
        extra={"model_request_measurement": record},
    )
    try:
        from src.lib.openai_agents.extraction_trace_events import (
            write_extraction_trace_event,
        )

        write_extraction_trace_event(
            event_type=MEASUREMENT_EVENT_TYPE,
            trace_id=record.get("trace_id"),
            input_summary=record,
            metadata={
                "runtime": record["runtime"],
                "provider": record["provider"],
                "model": record.get("model"),
                "agent": record.get("agent_name"),
                "outcome": record.get("outcome"),
            },
        )
    except (ImportError, RuntimeError, TypeError, ValueError):
        logger.warning("Failed to write model request measurement trace event", exc_info=True)


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _request_identity(agent: Any | None) -> dict[str, Any]:
    from src.lib.context import get_current_session_id, get_current_trace_id
    from src.lib.observability.cost_context import current_cost_context

    identity: dict[str, Any] = {}
    if agent is not None:
        identity["agent_name"] = getattr(agent, "name", None)
        cost_identity = getattr(agent, "cost_identity", None)
        if isinstance(cost_identity, Mapping):
            identity["agent_id"] = cost_identity.get("agent_id")
            identity["agent_role"] = cost_identity.get("agent_role")
    identity["trace_id"] = get_current_trace_id()
    identity["session_id"] = get_current_session_id()
    cost_context = current_cost_context()
    for key in ("activity", "run_id", "workflow_id", "node_id", "job_id", "document_id"):
        if cost_context.get(key) is not None:
            identity[key] = cost_context.get(key)
    try:
        from agents.tracing import get_current_span, get_current_trace

        sdk_trace = get_current_trace()
        sdk_span = get_current_span()
        identity["sdk_trace_id"] = getattr(sdk_trace, "trace_id", None)
        identity["sdk_span_id"] = getattr(sdk_span, "span_id", None)
    except ImportError:
        pass
    return {key: value for key, value in identity.items() if value is not None}


# ---------------------------------------------------------------------------
# Agents SDK model wrapper
# ---------------------------------------------------------------------------


def describe_model(model: Any, *, provider_hint: str | None = None) -> tuple[str, str, str]:
    """Return (provider, api, transport) for a resolved SDK model.

    A provider tag on the model wins, then a tag on the ``RunConfig`` model
    provider that resolved it (Agent Studio pins native OpenAI this way).
    """

    from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
    from agents.models.openai_responses import OpenAIResponsesModel, OpenAIResponsesWSModel

    tagged = (
        getattr(model, "_agr_provider_id", None)
        or getattr(model, "_provider_id", None)
        or provider_hint
    )
    if isinstance(model, OpenAIResponsesWSModel):
        api, transport = "responses", "websocket"
    elif isinstance(model, OpenAIResponsesModel):
        api, transport = "responses", "http"
    elif isinstance(model, OpenAIChatCompletionsModel):
        api, transport = "chat_completions", "http"
    else:
        return str(tagged or "unknown"), "unknown", "unknown"
    if not tagged:
        # Untagged SDK models come from the runner's provider or the SDK default
        # client, both built for the configured default runner provider.
        from src.lib.config.providers_loader import get_default_runner_provider

        tagged = get_default_runner_provider().provider_id
    return str(tagged), api, transport


class MeasuredModel(Model):
    """Measure every request an SDK model sends; block known-invalid requests.

    One instance wraps one resolved model for one agent turn, so its call
    counter is the SDK retry attempt number for that turn.
    """

    def __init__(
        self,
        inner: Model,
        *,
        agent: Any | None,
        provider_hint: str | None = None,
    ) -> None:
        self._inner = inner
        self._agent = agent
        self._provider_hint = provider_hint
        self._attempts = 0

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    @property
    def inner_model(self) -> Model:
        return self._inner

    async def close(self) -> None:
        await self._inner.close()

    def get_retry_advice(self, request: Any) -> Any:
        if isinstance(getattr(request, "error", None), ModelRequestBlockedError):
            from agents.retry import ModelRetryAdvice

            # replay_safety="unsafe" is the SDK's hard stop: no retry policy can
            # re-send a request that is invalid by construction.
            return ModelRetryAdvice(
                suggested=False,
                replay_safety="unsafe",
                reason="blocked_before_send",
            )
        return self._inner.get_retry_advice(request)

    def _measure(
        self,
        system_instructions: Any,
        input: Any,
        tools: Any,
        output_schema: Any,
        handoffs: Any,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> dict[str, Any]:
        self._attempts += 1
        provider, api, transport = describe_model(self._inner, provider_hint=self._provider_hint)
        identity = _request_identity(self._agent)
        identity["attempt"] = self._attempts
        measurement = build_measurement(
            runtime="agents_sdk",
            provider=provider,
            api=api,
            transport=transport,
            model=getattr(self._inner, "model", None),
            instructions=system_instructions,
            input_value=input,
            tools=tools,
            handoffs=handoffs,
            output_schema=output_schema,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
            identity=identity,
        )
        enforce_and_announce(measurement)
        return measurement

    def _observe_tool_surface(self, measurement: dict[str, Any], response: Any) -> None:
        """Fold this response into the run's tool surface and record it (ALL-1280)."""

        surface = getattr(self._agent, "tool_surface", None)
        if not isinstance(surface, ToolSurface):
            return
        if response is not None:
            surface.observe_response_output(getattr(response, "output", None))
        measurement["tool_surface"] = surface.summary()

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    ):
        measurement = self._measure(
            system_instructions,
            input,
            tools,
            output_schema,
            handoffs,
            previous_response_id,
            conversation_id,
            prompt,
        )
        try:
            response = await self._inner.get_response(
                system_instructions,
                input,
                model_settings,
                tools,
                output_schema,
                handoffs,
                tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            )
        except BaseException as exc:
            self._observe_tool_surface(measurement, None)
            record_outcome(measurement, outcome=_failure_outcome(exc), error_type=type(exc).__name__)
            raise
        self._observe_tool_surface(measurement, response)
        record_outcome(
            measurement,
            outcome="completed",
            usage=usage_from_model_response(response),
            response_id=getattr(response, "response_id", None),
            provider_request_id=getattr(response, "request_id", None),
        )
        return response

    async def stream_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    ) -> AsyncIterator[Any]:
        measurement = self._measure(
            system_instructions,
            input,
            tools,
            output_schema,
            handoffs,
            previous_response_id,
            conversation_id,
            prompt,
        )
        stream = self._inner.stream_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )
        terminal_response: Any = None
        terminal_type: str | None = None
        try:
            async for event in stream:
                event_type = getattr(event, "type", None)
                if event_type in {"response.completed", "response.incomplete", "response.failed"}:
                    terminal_response = getattr(event, "response", None)
                    terminal_type = event_type
                yield event
        except BaseException as exc:
            if terminal_response is None:
                self._observe_tool_surface(measurement, None)
                record_outcome(
                    measurement,
                    outcome=_failure_outcome(exc),
                    error_type=type(exc).__name__,
                )
            raise
        finally:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()
            if terminal_response is not None:
                self._observe_tool_surface(measurement, terminal_response)
                record_outcome(
                    measurement,
                    outcome=(terminal_type or "response.completed").removeprefix("response."),
                    usage=usage_from_provider_payload(getattr(terminal_response, "usage", None)),
                    response_id=getattr(terminal_response, "id", None),
                    provider_request_id=getattr(terminal_response, "_request_id", None),
                )
            else:
                self._observe_tool_surface(measurement, None)
                record_outcome(measurement, outcome="stream_closed_without_terminal_event")


def _failure_outcome(exc: BaseException) -> str:
    if isinstance(exc, GeneratorExit):
        return "consumer_closed"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "provider_error"


def measure_resolved_model(
    model: Model,
    *,
    agent: Any | None,
    run_config: Any | None = None,
) -> Model:
    """Wrap one SDK-resolved model so its requests are measured."""

    if isinstance(model, MeasuredModel):
        return model
    provider_hint = getattr(getattr(run_config, "model_provider", None), "_agr_provider_id", None)
    return MeasuredModel(model, agent=agent, provider_hint=provider_hint)


def install_model_request_measurement() -> None:
    """Route every Agents SDK model resolution through ``MeasuredModel`` (idempotent).

    Every ``Runner.run``/``run_streamed``/``run_sync`` turn, including nested
    specialist runs and runs without an explicit ``RunConfig``, resolves its model
    through ``run_loop.get_model``. Wrapping there covers agent-bound model
    objects, provider-resolved model names, WebSocket and HTTP transports, and
    SDK-managed retries without per-call-site instrumentation. The pinned SDK
    (0.17.4) defines ``get_model`` in ``turn_preparation`` and binds it into
    ``run_loop`` by name; the regression guard test fails if an SDK upgrade
    moves it.
    """

    global _SDK_GET_MODEL, _MEASURED_GET_MODEL
    from agents.run_internal import run_loop, turn_preparation

    if _MEASURED_GET_MODEL is None:
        resolve_model = turn_preparation.get_model

        def get_measured_model(agent: Any, run_config: Any) -> Model:
            return measure_resolved_model(
                resolve_model(agent, run_config),
                agent=agent,
                run_config=run_config,
            )

        setattr(get_measured_model, _MEASURED_FLAG, True)
        setattr(get_measured_model, "__wrapped__", resolve_model)
        _SDK_GET_MODEL = resolve_model
        _MEASURED_GET_MODEL = get_measured_model

    # Wrap the SDK resolver at its definition. ``run_loop`` imports it by name,
    # so rebind that name too, but only while it is still the unwrapped SDK
    # function: a foreign wrapper installed there (for example Sentry's OpenAI
    # Agents integration) resolves ``turn_preparation.get_model`` at call time
    # and therefore reaches this wrapper in either install order. Wrapping the
    # foreign wrapper instead would recurse through it.
    turn_preparation.get_model = _MEASURED_GET_MODEL
    if run_loop.get_model is _SDK_GET_MODEL:
        run_loop.get_model = _MEASURED_GET_MODEL


def model_request_measurement_installed() -> bool:
    from agents.run_internal import run_loop, turn_preparation

    return (
        _MEASURED_GET_MODEL is not None
        and turn_preparation.get_model is _MEASURED_GET_MODEL
        and run_loop.get_model is not _SDK_GET_MODEL
    )


# ---------------------------------------------------------------------------
# Direct provider clients (outside the Agents SDK)
# ---------------------------------------------------------------------------


def measure_direct_request(
    *,
    surface: str,
    provider: str,
    api: str,
    kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    """Measure and enforce one direct client request from its exact keyword arguments.

    ``api`` is ``responses``, ``responses.compact`` or ``chat_completions``.
    Returns the measurement to pass to ``record_outcome`` after the call.
    """

    identity = _request_identity(None)
    identity["agent_name"] = surface
    identity["attempt"] = 1
    if api == "chat_completions":
        instructions = None
        input_value = kwargs.get("messages")
    else:
        instructions = kwargs.get("instructions")
        input_value = kwargs.get("input")
    output_schema = kwargs.get("text_format") or kwargs.get("response_format")
    schema_payload = None
    if output_schema is not None:
        schema_fn = getattr(output_schema, "model_json_schema", None)
        schema_payload = _SchemaSize(
            name=getattr(output_schema, "__name__", None),
            schema=schema_fn() if callable(schema_fn) else output_schema,
        )
    measurement = build_measurement(
        runtime="direct_client",
        provider=provider,
        api="responses" if api.startswith("responses") else api,
        transport="http",
        model=kwargs.get("model"),
        instructions=instructions,
        input_value=input_value,
        tools=None,
        output_schema=schema_payload,
        previous_response_id=kwargs.get("previous_response_id"),
        identity=identity,
    )
    measurement["operation"] = api
    tools = kwargs.get("tools")
    if tools:
        tool_size = _size(tools)
        measurement["outbound"]["tools"]["initially_visible"] = {
            "count": len(tools),
            **tool_size,
        }
        measurement["outbound"]["json_chars"] += tool_size["chars"]
        measurement["model_visible"]["json_chars"] += tool_size["chars"]
        measurement["model_visible"]["estimated_tokens"] = estimate_tokens_from_chars(
            measurement["model_visible"]["json_chars"]
        )
    enforce_and_announce(measurement)
    return measurement


class _SchemaSize:
    """Adapter so direct-client structured-output schemas share schema measurement."""

    def __init__(self, *, name: str | None, schema: Any) -> None:
        self._name = name
        self._schema = schema

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str | None:
        return self._name

    def json_schema(self) -> Any:
        return self._schema


async def call_measured_direct_request(
    *,
    surface: str,
    provider: str,
    api: str,
    kwargs: Mapping[str, Any],
    call: Any,
) -> Any:
    """Measure, enforce, send and record one direct client request."""

    measurement = measure_direct_request(
        surface=surface,
        provider=provider,
        api=api,
        kwargs=kwargs,
    )
    try:
        response = await call(**kwargs)
    except BaseException as exc:
        record_outcome(measurement, outcome=_failure_outcome(exc), error_type=type(exc).__name__)
        raise
    record_outcome(
        measurement,
        outcome="completed",
        usage=usage_from_provider_payload(getattr(response, "usage", None)),
        response_id=getattr(response, "id", None),
        provider_request_id=getattr(response, "_request_id", None),
    )
    return response
