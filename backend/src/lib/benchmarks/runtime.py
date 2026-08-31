"""Adapters from generic benchmark cases to existing production runtime boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.lib.agent_studio.catalog_service import get_agent_by_id
from src.lib.agent_studio.flow_tools import build_flow_definition_from_recipe
from src.lib.config.agent_loader import load_agent_definitions
from src.lib.flows.executor import execute_flow
from src.lib.openai_agents.config import (
    get_benchmark_case_limit,
    get_benchmark_inline_max_bytes,
    get_benchmark_matrix_limit,
    get_benchmark_max_concurrency,
    get_benchmark_preview_max_chars,
    get_benchmark_result_limit,
    get_benchmark_retries,
    get_benchmark_timeout_seconds,
)
from src.lib.openai_agents.runner import run_agent_streamed
from src.lib.packages.flow_recipes import load_flow_recipe_catalog
from src.lib.packages.tool_registry import resolve_default_packages_dir
from src.models.sql.curation_flow import CurationFlow

from .loader import BenchmarkCatalog, BenchmarkCatalogError
from .models import BenchmarkRoute, ExecutionResult, ProviderUsage
from .service import BenchmarkService


def get_default_benchmark_root() -> Path:
    return resolve_default_packages_dir() / "alliance" / "benchmarks"


def build_default_catalog(root: Path | None = None) -> BenchmarkCatalog:
    flow_catalog = load_flow_recipe_catalog()
    flow_ids = {
        recipe.name
        for contribution in flow_catalog.contributions
        for recipe in contribution.manifest.recipes
    }
    return BenchmarkCatalog(
        root or get_default_benchmark_root(),
        agent_ids=load_agent_definitions(),
        flow_ids=flow_ids,
        # Schema validation is side-effect free and catalog-independent. The
        # canonical resolver performs provider/model availability validation at
        # execution time, after the requested route has been selected.
        route_validator=lambda _model, _provider: None,
    )


def build_default_service(root: Path | None = None) -> BenchmarkService:
    return BenchmarkService(
        build_default_catalog(root),
        agent_executor=execute_agent_case,
        flow_executor=execute_flow_case,
        max_concurrency=get_benchmark_max_concurrency(),
        matrix_limit=get_benchmark_matrix_limit(),
        case_limit=get_benchmark_case_limit(),
        result_limit=get_benchmark_result_limit(),
        timeout_seconds=get_benchmark_timeout_seconds(),
        retries=get_benchmark_retries(),
        preview_max_chars=get_benchmark_preview_max_chars(),
        inline_max_bytes=get_benchmark_inline_max_bytes(),
    )


def _provider_usage_from_event(event: dict[str, Any]) -> ProviderUsage | None:
    for container_key in ("data", "details"):
        container = event.get(container_key)
        if isinstance(container, dict) and isinstance(
            container.get("provider_usage"), dict
        ):
            return ProviderUsage.model_validate(container["provider_usage"])
    return None


async def execute_agent_case(
    target_id: str,
    case_input: dict[str, Any],
    route: BenchmarkRoute,
    run_id: str,
) -> ExecutionResult:
    messages = case_input.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Agent benchmark input must contain messages")
    active_groups = case_input.get("active_groups") or []
    agent = get_agent_by_id(
        target_id,
        db_user_id=case_input.get("db_user_id"),
        authenticated_groups=active_groups,
        model_id_override=route.model,
        model_provider_override=route.provider,
    )
    output: Any = None
    provider_usage = None
    terminal_seen = False
    async for event in run_agent_streamed(
        context_messages=messages,
        user_id=str(case_input.get("user_id") or "benchmark"),
        session_id=run_id,
        active_groups=active_groups,
        agent=agent,
        sentry_workflow="benchmark_agent",
        chat_route_mode="agent",
        chat_route_target_id=target_id,
        propagate_runtime_exceptions=True,
    ):
        provider_usage = _provider_usage_from_event(event) or provider_usage
        if event.get("type") == "RUN_ERROR":
            raise RuntimeError("Agent benchmark target failed")
        if event.get("type") == "RUN_FINISHED":
            terminal_seen = True
            data = event.get("data") or {}
            output = data.get("structured_result", data.get("response"))
    if not terminal_seen:
        raise RuntimeError("Agent benchmark target ended without a terminal event")
    return ExecutionResult(output=output, provider_usage=provider_usage)


def _flow_from_recipe(target_id: str) -> CurationFlow:
    catalog = load_flow_recipe_catalog()
    matches = [
        recipe
        for contribution in catalog.contributions
        for recipe in contribution.manifest.recipes
        if recipe.name == target_id
    ]
    if len(matches) != 1:
        raise BenchmarkCatalogError(
            f"Expected one configured flow recipe named '{target_id}'"
        )
    recipe = matches[0]
    definition = build_flow_definition_from_recipe(
        steps=recipe.model_dump(exclude_none=True)["steps"],
        task_instructions=recipe.description,
    )
    return CurationFlow(
        id=uuid5(NAMESPACE_URL, f"agr-benchmark-flow:{target_id}"),
        user_id=0,
        name=recipe.name,
        description=recipe.description,
        flow_definition=definition.model_dump(mode="json"),
        is_active=True,
    )


async def execute_flow_case(
    target_id: str,
    case_input: dict[str, Any],
    route: BenchmarkRoute,
    run_id: str,
) -> ExecutionResult:
    flow = _flow_from_recipe(target_id)
    output: Any = None
    provider_usage = None
    terminal_seen = False
    async for event in execute_flow(
        flow=flow,
        user_id=str(case_input.get("user_id") or "benchmark"),
        session_id=run_id,
        db_user_id=case_input.get("db_user_id"),
        document_id=case_input.get("document_id"),
        document_name=case_input.get("document_name"),
        user_query=str(case_input.get("user_query") or ""),
        active_groups=case_input.get("active_groups") or [],
        flow_run_id=run_id,
        chat_route_mode="flow",
        chat_route_target_id=str(flow.id),
        model_id_override=route.model,
        model_provider_override=route.provider,
    ):
        provider_usage = _provider_usage_from_event(event) or provider_usage
        if event.get("type") == "FLOW_ERROR":
            raise RuntimeError("Flow benchmark target failed")
        if event.get("type") == "FLOW_FINISHED":
            terminal_seen = True
            output = event.get("data") or event.get("details")
    if not terminal_seen:
        raise RuntimeError("Flow benchmark target ended without a terminal event")
    return ExecutionResult(output=output, provider_usage=provider_usage)
