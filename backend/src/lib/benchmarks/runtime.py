"""Adapters from generic benchmark cases to existing production runtime boundaries."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.lib.agent_studio.catalog_service import get_agent_by_id
from src.lib.agent_studio.flow_tools import build_flow_definition_from_recipe
from src.lib.agent_studio.catalog_service import get_benchmark_agent_by_id
from .source_revisions import benchmark_source_revisions
from src.lib.document_context import DocumentContext
from src.lib.flows.executor import execute_flow
from src.lib.openai_agents.config import (
    get_benchmark_max_failure_detail_chars,
    get_benchmark_max_invocations_per_cell,
)
from src.lib.openai_agents.runner import run_agent_streamed
from src.lib.openai_agents.benchmark_routing import (
    attach_benchmark_route,
    benchmark_route_plan,
)
from src.lib.openai_agents.provider_usage import (
    capture_provider_usage,
    provider_usage_metadata,
)
from src.models.sql.curation_flow import CurationFlow

from .errors import BenchmarkCatalogError
from .flow_results import load_flow_extractions
from .flow_catalog import load_benchmark_flow_templates
from .models import (
    BenchmarkCellExecutionResult,
    ProviderUsage,
    ResolvedBenchmarkCell,
)


def _stable_invocations(records: list[Any]) -> list[ProviderUsage]:
    ordered = sorted(
        enumerate(records),
        key=lambda item: (
            item[1].sequence if item[1].sequence is not None else float("inf"),
            item[0],
        ),
    )
    return [
        ProviderUsage.model_validate(provider_usage_metadata(record))
        for _, record in ordered
    ]


async def execute_resolved_agent_cell(
    cell: ResolvedBenchmarkCell,
    case_input: dict[str, Any],
    run_id: str,
) -> BenchmarkCellExecutionResult:
    """Execute an agent cell with its frozen route and complete invocation ledger."""

    if cell.target.kind != "agent":
        raise ValueError("resolved agent execution requires an agent target")
    slot = f"agent:{cell.target.id}"
    route = cell.routes.get(slot)
    if route is None:
        raise ValueError(f"Frozen benchmark route plan has no slot '{slot}'")
    messages = case_input.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Agent benchmark input must contain messages")
    user_id = str(case_input.get("user_id") or "benchmark")
    document_id = case_input.get("document_id")
    document_name = case_input.get("document_name")
    doc_context = (
        DocumentContext.fetch(document_id, user_id, document_name)
        if document_id
        else None
    )
    active_groups = case_input.get("active_groups") or []
    with benchmark_route_plan(cell.routes), benchmark_source_revisions(cell.source_execution_receipts), capture_provider_usage(
        max_records=get_benchmark_max_invocations_per_cell(),
        max_failure_detail_chars=get_benchmark_max_failure_detail_chars(),
    ) as usage_records:
        construction = (get_benchmark_agent_by_id if cell.target.id.startswith("ca_") else get_agent_by_id)
        routing = ({"benchmark_slot": slot} if cell.target.id.startswith("ca_") else {
            "model_id_override": route.model, "model_provider_override": route.provider,
            "model_reasoning_override": route.reasoning_effort, "benchmark_route_slot": slot,
        })
        agent = construction(
            cell.target.id,
            db_user_id=case_input.get("db_user_id"),
            active_groups=active_groups,
            authenticated_groups=active_groups,
            **routing,
            **(doc_context.to_agent_kwargs() if doc_context else {}),
        )
        attach_benchmark_route(agent, slot)
        output: Any = None
        terminal_seen = False
        async for event in run_agent_streamed(
            context_messages=messages,
            user_id=user_id,
            document_id=document_id,
            document_name=document_name,
            doc_context=doc_context,
            session_id=run_id,
            active_groups=active_groups,
            agent=agent,
            sentry_workflow="benchmark_agent",
            chat_route_mode="agent",
            chat_route_target_id=cell.target.id,
            propagate_runtime_exceptions=True,
        ):
            if event.get("type") == "RUN_ERROR":
                raise RuntimeError("Agent benchmark target failed")
            if event.get("type") == "STRUCTURED_RESULT":
                output = (event.get("data") or {}).get("result")
            if event.get("type") == "RUN_FINISHED":
                terminal_seen = True
        if not terminal_seen:
            raise RuntimeError("Agent benchmark target ended without a terminal event")
        if not isinstance(output, dict):
            raise ValueError("Agent benchmark target must return a structured JSON object")
    return BenchmarkCellExecutionResult(
        output=output,
        invocations=_stable_invocations(usage_records),
    )


def _flow_from_recipe(target_id: str, active_groups: list[str] | None = None) -> CurationFlow:
    matches = [
        recipe
        for recipe in load_benchmark_flow_templates(active_groups or [])
        if recipe["name"] == target_id
    ]
    if len(matches) != 1:
        raise BenchmarkCatalogError(
            f"Expected one configured flow recipe named '{target_id}'"
        )
    recipe = matches[0]
    definition = build_flow_definition_from_recipe(
        steps=recipe["steps"],
        task_instructions=recipe["description"],
    )
    return CurationFlow(
        id=uuid5(NAMESPACE_URL, f"agr-benchmark-flow:{target_id}"),
        user_id=0,
        name=recipe["name"],
        description=recipe["description"],
        flow_definition=definition.model_dump(mode="json"),
        is_active=True,
    )


async def execute_resolved_flow_cell(
    cell: ResolvedBenchmarkCell,
    case_input: dict[str, Any],
    run_id: str,
) -> BenchmarkCellExecutionResult:
    """Execute a flow with independent supervisor, agent, and validator routes."""

    if cell.target.kind != "flow":
        raise ValueError("resolved flow execution requires a flow target")
    if "supervisor" not in cell.routes:
        raise ValueError("Frozen benchmark route plan has no slot 'supervisor'")
    flow = _flow_from_recipe(cell.target.id, case_input.get("active_groups", []))
    with benchmark_route_plan(cell.routes), benchmark_source_revisions(cell.source_execution_receipts), capture_provider_usage(
        max_records=get_benchmark_max_invocations_per_cell(),
        max_failure_detail_chars=get_benchmark_max_failure_detail_chars(),
    ) as usage_records:
        output: Any = None
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
            benchmark_routes=cell.routes,
        ):
            if event.get("type") == "FLOW_ERROR":
                raise RuntimeError("Flow benchmark target failed")
            if event.get("type") == "FLOW_FINISHED":
                terminal_seen = True
                completion = event.get("data")
                if not isinstance(completion, dict):
                    raise ValueError("Benchmark flow completion must contain a receipt")
                output = load_flow_extractions(
                    completion,
                    document_id=str(case_input.get("document_id") or ""),
                    user_id=str(case_input.get("user_id") or "benchmark"),
                    run_id=run_id,
                )
        if not terminal_seen:
            raise RuntimeError("Flow benchmark target ended without a terminal event")
    return BenchmarkCellExecutionResult(
        output=output,
        invocations=_stable_invocations(usage_records),
    )
