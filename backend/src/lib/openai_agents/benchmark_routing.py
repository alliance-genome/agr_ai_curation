"""Request-scoped benchmark route-plan propagation.

The benchmark planner owns validation and freezing.  This module only exposes
the already-resolved routes to nested runtime boundaries without changing
ordinary chat/flow scientific defaults.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeBenchmarkRoute:
    provider: str
    model: str
    reasoning_effort: str | None


_active_routes: ContextVar[Mapping[str, RuntimeBenchmarkRoute] | None] = ContextVar(
    "active_benchmark_routes", default=None
)
_active_invocation_route: ContextVar[
    tuple[str, RuntimeBenchmarkRoute] | None
] = ContextVar("active_benchmark_invocation_route", default=None)


def _coerce_route(value: Any) -> RuntimeBenchmarkRoute:
    if isinstance(value, Mapping):
        provider = value.get("provider")
        model = value.get("model")
        reasoning_effort = value.get("reasoning_effort")
    else:
        provider = getattr(value, "provider", None)
        model = getattr(value, "model", None)
        reasoning_effort = getattr(value, "reasoning_effort", None)
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("benchmark route provider is required")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("benchmark route model is required")
    return RuntimeBenchmarkRoute(
        provider=provider.strip(),
        model=model.strip(),
        reasoning_effort=(
            reasoning_effort.strip()
            if isinstance(reasoning_effort, str) and reasoning_effort.strip()
            else None
        ),
    )


@contextmanager
def benchmark_route_plan(routes: Mapping[str, Any]) -> Iterator[None]:
    """Activate one immutable, fully resolved cell route map."""

    normalized = {slot: _coerce_route(route) for slot, route in routes.items()}
    token = _active_routes.set(normalized)
    try:
        yield
    finally:
        _active_routes.reset(token)


def active_benchmark_route(slot: str) -> RuntimeBenchmarkRoute | None:
    routes = _active_routes.get()
    if routes is None:
        return None
    try:
        return routes[slot]
    except KeyError as exc:
        raise ValueError(f"Frozen benchmark route plan has no slot '{slot}'") from exc


def benchmark_route_plan_active() -> bool:
    return _active_routes.get() is not None


def benchmark_route_kwargs(slot: str) -> dict[str, Any]:
    """Return agent-construction overrides for an active model-bearing slot."""

    route = active_benchmark_route(slot)
    if route is None:
        return {}
    return {
        "model_id_override": route.model,
        "model_provider_override": route.provider,
        "model_reasoning_override": route.reasoning_effort,
        "benchmark_route_slot": slot,
    }


def attach_benchmark_route(agent: Any, slot: str) -> Any:
    """Attach content-free requested-route identity to an Agent and its model."""

    route = active_benchmark_route(slot)
    if route is None:
        return agent
    for target in (agent, getattr(agent, "model", None)):
        if target is None or isinstance(target, str):
            continue
        try:
            setattr(target, "_benchmark_route_slot", slot)
            setattr(target, "_benchmark_requested_provider", route.provider)
            setattr(target, "_benchmark_requested_model", route.model)
            setattr(target, "_benchmark_reasoning_effort", route.reasoning_effort)
        except (AttributeError, TypeError):
            # Immutable model stand-ins (and native string model IDs above) carry
            # route identity on the Agent, which is the native-usage boundary.
            pass
    setattr(agent, "benchmark_route_slot", slot)
    setattr(agent, "benchmark_requested_provider", route.provider)
    setattr(agent, "benchmark_requested_model", route.model)
    setattr(agent, "benchmark_reasoning_effort", route.reasoning_effort)
    return agent


def set_benchmark_invocation_route(agent: Any) -> Any:
    slot = getattr(agent, "benchmark_route_slot", None)
    if not slot:
        return None
    route = RuntimeBenchmarkRoute(
        provider=str(agent.benchmark_requested_provider),
        model=str(agent.benchmark_requested_model),
        reasoning_effort=getattr(agent, "benchmark_reasoning_effort", None),
    )
    return _active_invocation_route.set((str(slot), route))


def reset_benchmark_invocation_route(token: Any) -> None:
    if token is not None:
        _active_invocation_route.reset(token)


class BenchmarkTelemetryModel:
    """Transparent native SDK model proxy with provider-call telemetry."""

    def __init__(self, model: Any) -> None:
        self._model = model

    async def close(self) -> None:
        await self._model.close()

    def get_retry_advice(self, request: Any) -> Any:
        return self._model.get_retry_advice(request)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        from time import monotonic

        from src.lib.openai_agents.provider_usage import (
            begin_provider_invocation,
            complete_generic_provider_invocation,
            fail_provider_invocation,
        )

        active = _active_invocation_route.get()
        started_at = monotonic()
        pending = (
            begin_provider_invocation(
                route_slot=active[0],
                requested_provider=active[1].provider,
                requested_model=active[1].model,
                reasoning_effort=active[1].reasoning_effort,
                started_at=started_at,
            )
            if active is not None
            else None
        )
        try:
            response = await self._model.get_response(*args, **kwargs)
        except BaseException as exc:
            fail_provider_invocation(
                pending, exc, latency_ms=round((monotonic() - started_at) * 1000)
            )
            raise
        complete_generic_provider_invocation(
            pending,
            response,
            latency_ms=round((monotonic() - started_at) * 1000),
        )
        return response

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        from time import monotonic

        from src.lib.openai_agents.provider_usage import (
            begin_provider_invocation,
            complete_generic_provider_invocation,
            fail_provider_invocation,
        )

        active = _active_invocation_route.get()
        started_at = monotonic()
        pending = (
            begin_provider_invocation(
                route_slot=active[0],
                requested_provider=active[1].provider,
                requested_model=active[1].model,
                reasoning_effort=active[1].reasoning_effort,
                started_at=started_at,
            )
            if active is not None
            else None
        )
        terminal_response = None
        try:
            async for event in self._model.stream_response(*args, **kwargs):
                candidate = getattr(event, "response", None)
                if candidate is not None:
                    terminal_response = candidate
                yield event
        except BaseException as exc:
            fail_provider_invocation(
                pending, exc, latency_ms=round((monotonic() - started_at) * 1000)
            )
            raise
        complete_generic_provider_invocation(
            pending,
            terminal_response,
            latency_ms=round((monotonic() - started_at) * 1000),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


class BenchmarkTelemetryProvider:
    """Model-provider proxy used by native OpenAI request-owned providers."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self._models: dict[int, BenchmarkTelemetryModel] = {}

    def get_model(self, model_name: str | None) -> Any:
        model = self._provider.get_model(model_name)
        if _active_invocation_route.get() is None:
            return model
        return self._models.setdefault(id(model), BenchmarkTelemetryModel(model))

    async def aclose(self) -> None:
        await self._provider.aclose()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)
