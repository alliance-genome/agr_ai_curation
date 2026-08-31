"""Agents SDK model adapter for config-defined provider request policy."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
from dataclasses import replace
from time import monotonic
from typing import Any

from agents import ModelSettings
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

from .provider_usage import (
    begin_provider_invocation,
    complete_provider_invocation,
    fail_provider_invocation,
    normalize_provider_usage,
    ProviderUsageRecord,
)


def _payload_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    return {}


class _ProviderTelemetryStream:
    """Transparent async-stream proxy that retains only safe terminal fields."""

    def __init__(
        self,
        source: Any,
        *,
        adapter: str,
        requested_model: str,
        started_at: float,
        pending_invocation: Any,
    ) -> None:
        self._source = source
        self._iterator: AsyncIterator[Any] = source.__aiter__()
        self._adapter = adapter
        self._requested_model = requested_model
        self._started_at = started_at
        self._pending_invocation = pending_invocation
        self._usage: Mapping[str, Any] = {}
        self._metadata: Mapping[str, Any] = {}
        self._emitted = False

    def __aiter__(self) -> "_ProviderTelemetryStream":
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await self._iterator.__anext__()
        except StopAsyncIteration:
            self._emit()
            raise
        except BaseException as exc:
            fail_provider_invocation(
                self._pending_invocation,
                exc,
                latency_ms=round((monotonic() - self._started_at) * 1000),
            )
            self._emitted = True
            raise

        payload = _payload_mapping(chunk)
        usage = _payload_mapping(payload.get("usage"))
        metadata = _payload_mapping(payload.get("openrouter_metadata"))
        if usage:
            self._usage = usage
        if metadata:
            self._metadata = metadata
        return chunk

    def _emit(self) -> None:
        if self._emitted:
            return
        self._emitted = True
        latency_ms = round((monotonic() - self._started_at) * 1000)
        complete_provider_invocation(
            self._pending_invocation,
            normalize_provider_usage(
                self._adapter,
                {
                    "usage": self._usage,
                    "openrouter_metadata": self._metadata,
                },
                requested_model=self._requested_model,
                latency_ms=latency_ms,
            ),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)


class ProviderConfiguredChatCompletionsModel(OpenAIChatCompletionsModel):
    """Apply immutable config policy and optional safe telemetry capture."""

    def __init__(
        self,
        *,
        provider_id: str,
        request_extra_body: Mapping[str, Any],
        request_headers: Mapping[str, str],
        forbidden_request_fields: tuple[str, ...],
        omit_usage_request: bool,
        telemetry_adapter: str | None,
        disable_model_retries: bool,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._provider_id = provider_id
        self._request_extra_body = deepcopy(dict(request_extra_body))
        self._request_headers = dict(request_headers)
        self._forbidden_request_fields = forbidden_request_fields
        self._omit_usage_request = omit_usage_request
        self._telemetry_adapter = telemetry_adapter
        self._disable_model_retries = disable_model_retries

    def _apply_provider_policy(self, settings: ModelSettings) -> ModelSettings:
        caller_body = deepcopy(dict(settings.extra_body or {}))
        for field_name in self._forbidden_request_fields:
            if field_name in caller_body:
                raise ValueError(
                    f"Provider '{self._provider_id}' does not permit request field "
                    f"'{field_name}'"
                )

        for key, policy_value in self._request_extra_body.items():
            caller_value = caller_body.get(key)
            if isinstance(caller_value, Mapping) and isinstance(policy_value, Mapping):
                merged_value = deepcopy(dict(caller_value))
                merged_value.update(deepcopy(dict(policy_value)))
                caller_body[key] = merged_value
            else:
                caller_body[key] = deepcopy(policy_value)

        headers = dict(settings.extra_headers or {})
        headers.update(self._request_headers)
        return replace(
            settings,
            extra_body=caller_body or None,
            extra_headers=headers or None,
            include_usage=None if self._omit_usage_request else settings.include_usage,
            retry=None if self._disable_model_retries else settings.retry,
        )

    async def _fetch_response(self, *args: Any, **kwargs: Any) -> Any:
        if len(args) >= 3:
            positional_args = list(args)
            positional_args[2] = self._apply_provider_policy(positional_args[2])
            args = tuple(positional_args)
        elif "model_settings" in kwargs:
            kwargs["model_settings"] = self._apply_provider_policy(
                kwargs["model_settings"]
            )
        else:
            raise TypeError("model_settings is required")

        started_at = monotonic()
        pending_invocation = begin_provider_invocation(
            requested_provider=str(
                getattr(self, "_benchmark_requested_provider", self._provider_id)
            ),
            requested_model=str(
                getattr(self, "_benchmark_requested_model", self.model)
            ),
            route_slot=getattr(self, "_benchmark_route_slot", None),
            reasoning_effort=getattr(self, "_benchmark_reasoning_effort", None),
            started_at=started_at,
        )
        try:
            response = await super()._fetch_response(*args, **kwargs)
        except BaseException as exc:
            fail_provider_invocation(
                pending_invocation,
                exc,
                latency_ms=round((monotonic() - started_at) * 1000),
            )
            raise
        if not self._telemetry_adapter:
            complete_provider_invocation(
                pending_invocation,
                ProviderUsageRecord(
                    requested_provider=self._provider_id,
                    requested_model=str(self.model),
                    actual_provider=self._provider_id,
                    actual_model=str(self.model),
                    routing_attempt=0,
                    latency_ms=round((monotonic() - started_at) * 1000),
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    billed_cost=None,
                ),
            )
            return response

        if isinstance(response, tuple):
            synthetic_response, stream = response
            return (
                synthetic_response,
                _ProviderTelemetryStream(
                    stream,
                    adapter=self._telemetry_adapter,
                    requested_model=str(self.model),
                    started_at=started_at,
                    pending_invocation=pending_invocation,
                ),
            )

        latency_ms = round((monotonic() - started_at) * 1000)
        complete_provider_invocation(
            pending_invocation,
            normalize_provider_usage(
                self._telemetry_adapter,
                response,
                requested_model=str(self.model),
                latency_ms=latency_ms,
            ),
        )
        return response
