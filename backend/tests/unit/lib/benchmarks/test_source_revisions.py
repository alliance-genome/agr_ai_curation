"""Custom benchmark source pins survive planning, replay and model routing."""

from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from src.lib.benchmarks.catalog import build_route_catalog
from src.lib.benchmarks.lifecycle import BenchmarkLifecycleFailure, authoritative_plan
from src.lib.benchmarks.models import (
    BenchmarkModelCatalogEntry,
    BenchmarkSuite,
    BenchmarkSuiteRoute,
    ResolvedBenchmarkPlan,
)
from src.lib.benchmarks.source_revisions import (
    benchmark_source_revisions,
    require_benchmark_source,
)
from src.lib.benchmarks.suites import resolve_suite
from src.schemas.agent_execution_revision import (
    AgentExecutionReceipt,
    AgentOutputContract,
)


def source_receipt(key="ca_saved"):
    return AgentExecutionReceipt(agent_id=uuid4(), agent_key=key, agent_revision_id=uuid4(),
                                 revision=1, fingerprint="sha256:" + "a" * 64,
                                 output_contract=AgentOutputContract(output_state="none"))


def planned(receipt):
    key = receipt.agent_key
    route = BenchmarkSuiteRoute(provider="openai", model="gpt-5.6-sol", reasoning_effort=None)
    catalog = build_route_catalog(
        models=[BenchmarkModelCatalogEntry(provider="openai", model="gpt-5.6-sol")],
        supervisor_default=route, agent_defaults={key: route}, model_validator_defaults={},
        agent_targets=[key], agent_model_validators={}, flow_agents={}, flow_model_validators={},
        source_execution_receipts={f"agent:{key}": receipt},
    )
    suite = BenchmarkSuite.model_validate({
        "schema_version": 2, "suite_id": "source-proof", "cases": [{
            "case_id": "paper", "target": {"kind": "agent", "id": key},
            "input": {"resolver": "fixture", "reference": "paper", "version": "1", "digest": "sha256:" + "b" * 64},
            "user_query": "Extract the requested evidence",
        }], "configurations": [{"configuration_id": "experiment"}],
    })
    plan = resolve_suite(suite, catalog, max_cases=1, max_configurations=1, max_repetitions=1, max_cells=1)
    return suite, catalog, plan


def test_source_revision_changes_catalog_cell_plan_and_authoritative_admission():
    receipt = source_receipt()
    suite, catalog, plan = planned(receipt)
    replacement = receipt.model_copy(update={"agent_revision_id": uuid4(), "revision": 2,
                                             "fingerprint": "sha256:" + "c" * 64})
    _, next_catalog, next_plan = planned(replacement)
    assert plan.suite_digest == next_plan.suite_digest
    assert plan.catalog_digest != next_plan.catalog_digest
    assert plan.plan_digest != next_plan.plan_digest
    assert plan.cells[0].cell_id != next_plan.cells[0].cell_id
    assert ResolvedBenchmarkPlan.model_validate_json(plan.model_dump_json()) == plan
    assert authoritative_plan(suite_value=suite.model_dump(mode="json"), submitted_plan=plan, catalog=catalog)[1] == plan
    with pytest.raises(BenchmarkLifecycleFailure) as rejected:
        authoritative_plan(suite_value=suite.model_dump(mode="json"), submitted_plan=plan, catalog=next_catalog)
    assert rejected.value.code == "plan_drift"
    # Replay/rerun copies retain the original source, never the later catalog.
    subset = plan.model_copy(update={"cells": tuple(plan.cells)})
    assert subset.cells[0].source_execution_receipts[f"agent:{receipt.agent_key}"] == receipt


def test_system_plan_serialization_does_not_invent_source_identity():
    from tests.unit.lib.benchmarks.test_suites import _catalog
    target = _catalog().targets[0]
    assert "source_execution_receipts" not in target.model_dump(mode="json")


def test_normal_failed_cell_rerun_keeps_original_source_without_catalog_resolution(monkeypatch):
    from src.lib.benchmarks import lifecycle
    from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
    from src.models.sql.benchmark import BenchmarkJobStatus

    monkeypatch.setattr("src.lib.config.groups_loader.get_valid_group_ids", lambda: [])
    receipt = source_receipt()
    suite, _, plan = planned(receipt)
    context = BenchmarkCuratorContext(subject="owner", auth_provider="oidc", db_user_id=1, active_groups=())
    parent = NS(id=uuid4(), status=BenchmarkJobStatus.FAILED, curator_context=context.model_dump(mode="json"),
                resolved_plan=plan.model_dump(mode="json"), suite_specification=suite.model_dump(mode="json"))
    failed = NS(id=uuid4(), cell_key=plan.cells[0].cell_id)
    session = MagicMock()
    session.scalar.return_value = parent
    session.scalars.return_value = [failed]
    session.execute.return_value.all.return_value = [(plan.cases[0].case_id, uuid4())]
    repository = MagicMock()
    reservation = NS(outcome="accepted", job_id=uuid4(), owner_subject="owner")
    repository.reserve_idempotency.return_value = (reservation, True)
    repository.create_job.return_value = NS(id=reservation.job_id)
    monkeypatch.setattr(lifecycle, "BenchmarkRepository", lambda _: repository)
    forbidden = MagicMock(side_effect=AssertionError("rerun must not consult new source or catalog"))
    monkeypatch.setattr(lifecycle, "authoritative_plan", forbidden)
    monkeypatch.setattr(lifecycle, "materialize_plan_inputs", forbidden)
    args = dict(session=session, owner_subject="owner", source_job_id=parent.id,
                requested_cell_ids=(failed.id,), idempotency_key="same-rerun", current_context=context)
    result = lifecycle._rerun_job(**args)
    copied = repository.create_job.call_args.kwargs["plan"]
    assert copied.cells == plan.cells
    assert copied.cells[0].source_execution_receipts == plan.cells[0].source_execution_receipts
    repository.reserve_idempotency.return_value = (reservation, False)
    assert lifecycle._rerun_job(**args).replayed
    assert result.job_id == reservation.job_id
    repository.create_job.assert_called_once()
    forbidden.assert_not_called()


@pytest.mark.parametrize("mode", ["absent", "wrong_agent", "wrong_slot"])
def test_missing_or_mismatched_cell_source_fails_closed(mode):
    receipt = source_receipt()
    sources = {} if mode == "absent" else {"agent:ca_saved": receipt}
    with benchmark_source_revisions(sources), pytest.raises(ValueError, match="frozen source"):
        require_benchmark_source("agent:other" if mode == "wrong_slot" else "agent:ca_saved",
                                 "ca_other" if mode == "wrong_agent" else "ca_saved")
    with pytest.raises(ValueError):
        require_benchmark_source("agent:ca_saved", "ca_saved")


def test_worker_uses_frozen_plan_source_and_rejects_changed_sql_binding(monkeypatch):
    from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
    from src.lib.benchmarks.worker import BenchmarkWorker
    from src.models.sql.benchmark import BenchmarkCell

    receipt = source_receipt()
    _, _, plan = planned(receipt)
    resolved = plan.cells[0]
    monkeypatch.setattr("src.lib.config.groups_loader.get_valid_group_ids", lambda: [])
    curator = BenchmarkCuratorContext(subject="user", auth_provider="oidc", db_user_id=1, active_groups=())
    row = NS(id=uuid4(), job_id=uuid4(), cell_key=resolved.cell_id,
             target_kind="agent", target_id=receipt.agent_key, case_id=resolved.case_id,
             configuration_id=resolved.configuration_id, repetition=resolved.repetition,
             routes={slot: route.model_dump(mode="json") for slot, route in resolved.routes.items()},
             **{f"input_{key}": value for key, value in resolved.input.model_dump().items()})
    job = NS(resolved_plan=plan.model_dump(mode="json"), curator_context=curator.model_dump(mode="json"))
    session = MagicMock()
    session.get.side_effect = lambda model, _: row if model is BenchmarkCell else job
    worker = BenchmarkWorker.__new__(BenchmarkWorker)
    worker.session_factory = lambda: nullcontext(session)
    assert worker._load_cell(row.id) == (row, resolved)
    row.routes = {next(iter(row.routes)): {"provider": "openai", "model": "changed", "reasoning_effort": None}}
    with pytest.raises(ValueError, match="differs"):
        worker._load_cell(row.id)
    job.resolved_plan["cells"][0].pop("source_execution_receipts")
    with pytest.raises(ValueError, match="no frozen source"):
        worker._load_cell(row.id)


def test_custom_constructor_requires_both_cell_source_and_active_route(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.openai_agents.benchmark_routing import benchmark_route_plan

    receipt = source_receipt()
    _, _, plan = planned(receipt)
    cell = plan.cells[0]
    forbidden = MagicMock(side_effect=AssertionError("must reject before any database or provider work"))
    monkeypatch.setattr(catalog_service, "_get_pinned_agent_by_id", forbidden)
    with pytest.raises(ValueError, match="frozen source"):
        catalog_service.get_benchmark_agent_by_id(receipt.agent_key, benchmark_slot=f"agent:{receipt.agent_key}")
    with benchmark_source_revisions(cell.source_execution_receipts), pytest.raises(ValueError, match="active frozen"):
        catalog_service.get_benchmark_agent_by_id(receipt.agent_key, benchmark_slot=f"agent:{receipt.agent_key}")
    with benchmark_source_revisions(cell.source_execution_receipts), benchmark_route_plan(cell.routes):
        with pytest.raises(ValueError, match="only by the frozen cell"):
            catalog_service.get_benchmark_agent_by_id(receipt.agent_key, benchmark_slot=f"agent:{receipt.agent_key}",
                                                       execution_revision_id=str(uuid4()))
    forbidden.assert_not_called()
