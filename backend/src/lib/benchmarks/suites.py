"""Strict loading and deterministic planning for execution-only suite v2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from .loader import BenchmarkCatalogError
from .models import (
    BenchmarkConfiguration,
    BenchmarkRouteCatalog,
    BenchmarkSuite,
    BenchmarkSuiteRoute,
    ResolvedBenchmarkCase,
    ResolvedBenchmarkCell,
    ResolvedBenchmarkPlan,
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def validate_suite(value: Mapping[str, Any]) -> BenchmarkSuite:
    """Validate checked-in YAML or an ad hoc JSON mapping through one schema."""

    try:
        return BenchmarkSuite.model_validate(value)
    except ValidationError as exc:
        raise BenchmarkCatalogError(f"Invalid benchmark suite: {exc}") from exc


def load_suite(path: Path) -> BenchmarkSuite:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise BenchmarkCatalogError(
            f"Unable to load benchmark suite {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise BenchmarkCatalogError(f"Benchmark suite {path} must contain an object")
    try:
        return validate_suite(value)
    except BenchmarkCatalogError as exc:
        raise BenchmarkCatalogError(f"Invalid benchmark suite {path}: {exc}") from exc


def load_checked_in_suites(root: Path) -> tuple[BenchmarkSuite, ...]:
    suite_dir = root.expanduser().resolve(strict=False) / "suites"
    if not suite_dir.is_dir():
        raise BenchmarkCatalogError(
            f"Benchmark suite directory does not exist: {suite_dir}"
        )
    suites = tuple(load_suite(path) for path in sorted(suite_dir.glob("*.yaml")))
    if not suites:
        raise BenchmarkCatalogError(f"No benchmark suites found in {suite_dir}")
    suite_ids = [suite.suite_id for suite in suites]
    if len(suite_ids) != len(set(suite_ids)):
        raise BenchmarkCatalogError("Checked-in benchmark suite IDs must be unique")
    return suites


def _validate_route(
    route: BenchmarkSuiteRoute,
    *,
    catalog: BenchmarkRouteCatalog,
    context: str,
) -> None:
    matches = [
        item
        for item in catalog.models
        if item.provider == route.provider and item.model == route.model
    ]
    if not matches:
        raise BenchmarkCatalogError(
            f"{context} uses unknown provider/model route "
            f"'{route.provider}/{route.model}'"
        )
    model = matches[0]
    if route.reasoning_effort is not None and route.reasoning_effort not in set(
        model.reasoning_efforts
    ):
        allowed = ", ".join(model.reasoning_efforts) or "none"
        raise BenchmarkCatalogError(
            f"{context} reasoning effort '{route.reasoning_effort}' is not supported "
            f"by '{route.model}'; allowed values: {allowed}"
        )


def resolve_suite(
    suite: BenchmarkSuite,
    catalog: BenchmarkRouteCatalog,
    *,
    max_cases: int,
    max_configurations: int,
    max_repetitions: int,
    max_cells: int,
) -> ResolvedBenchmarkPlan:
    """Freeze defaults and explicit named arms into a deterministic cell plan."""

    limits = {
        "max_cases": max_cases,
        "max_configurations": max_configurations,
        "max_repetitions": max_repetitions,
        "max_cells": max_cells,
    }
    if any(value < 1 for value in limits.values()):
        raise ValueError("benchmark planning limits must be positive")
    if len(suite.cases) > max_cases:
        raise BenchmarkCatalogError(
            f"Suite contains {len(suite.cases)} cases; limit is {max_cases}"
        )
    if len(suite.configurations) > max_configurations:
        raise BenchmarkCatalogError(
            f"Suite contains {len(suite.configurations)} configurations; "
            f"limit is {max_configurations}"
        )
    if suite.repetitions > max_repetitions:
        raise BenchmarkCatalogError(
            f"Suite requests {suite.repetitions} repetitions; limit is {max_repetitions}"
        )
    cell_count = len(suite.cases) * len(suite.configurations) * suite.repetitions
    if cell_count > max_cells:
        raise BenchmarkCatalogError(
            f"Suite expands to {cell_count} cells; limit is {max_cells}"
        )

    targets = {(item.target.kind, item.target.id): item for item in catalog.targets}
    slots = {item.slot: item for item in catalog.route_slots}
    used_slots: set[str] = set()
    for case in suite.cases:
        target_key = (case.target.kind, case.target.id)
        target = targets.get(target_key)
        if target is None:
            raise BenchmarkCatalogError(
                f"Unknown {case.target.kind} target '{case.target.id}' in case "
                f"'{case.case_id}'"
            )
        used_slots.update(target.route_slots)

    for configuration in suite.configurations:
        unknown = set(configuration.routes) - used_slots
        if unknown:
            raise BenchmarkCatalogError(
                f"Configuration '{configuration.configuration_id}' uses route slots "
                f"not selected by any case target: {', '.join(sorted(unknown))}"
            )
        for slot, route in configuration.routes.items():
            _validate_route(
                route,
                catalog=catalog,
                context=f"configuration '{configuration.configuration_id}' slot '{slot}'",
            )

    resolved_configurations: list[BenchmarkConfiguration] = []
    for configuration in suite.configurations:
        resolved_routes = {
            slot: configuration.routes.get(slot, slots[slot].default_route)
            for slot in sorted(used_slots)
        }
        for slot, route in resolved_routes.items():
            _validate_route(
                route,
                catalog=catalog,
                context=f"resolved slot '{slot}'",
            )
        resolved_configurations.append(
            BenchmarkConfiguration(
                configuration_id=configuration.configuration_id,
                routes=resolved_routes,
            )
        )

    resolved_cases = tuple(
        ResolvedBenchmarkCase(
            case_id=case.case_id, target=case.target, input=case.input
        )
        for case in suite.cases
    )
    cells: list[ResolvedBenchmarkCell] = []
    for case in suite.cases:
        target = targets[(case.target.kind, case.target.id)]
        target_slots = sorted(target.route_slots)
        for configuration in resolved_configurations:
            cell_routes = {slot: configuration.routes[slot] for slot in target_slots}
            for repetition in range(1, suite.repetitions + 1):
                identity = {
                    "suite_id": suite.suite_id,
                    "case_id": case.case_id,
                    "configuration_id": configuration.configuration_id,
                    "repetition": repetition,
                    "target": case.target.model_dump(mode="json"),
                    "input": case.input.model_dump(mode="json"),
                    "routes": {
                        slot: route.model_dump(mode="json")
                        for slot, route in cell_routes.items()
                    },
                }
                cells.append(
                    ResolvedBenchmarkCell(
                        cell_id=_digest(identity),
                        case_id=case.case_id,
                        configuration_id=configuration.configuration_id,
                        repetition=repetition,
                        target=case.target,
                        input=case.input,
                        routes=cell_routes,
                    )
                )

    suite_digest = _digest(suite.model_dump(mode="json"))
    catalog_digest = _digest(catalog.model_dump(mode="json"))
    plan_payload = {
        "schema_version": 2,
        "suite_id": suite.suite_id,
        "suite_digest": suite_digest,
        "catalog_digest": catalog_digest,
        "repetitions": suite.repetitions,
        "cases": [case.model_dump(mode="json") for case in resolved_cases],
        "configurations": [
            configuration.model_dump(mode="json")
            for configuration in resolved_configurations
        ],
        "cells": [cell.model_dump(mode="json") for cell in cells],
    }
    return ResolvedBenchmarkPlan(
        schema_version=2,
        suite_id=suite.suite_id,
        suite_digest=suite_digest,
        catalog_digest=catalog_digest,
        repetitions=suite.repetitions,
        cases=resolved_cases,
        configurations=tuple(resolved_configurations),
        cells=tuple(cells),
        plan_digest=_digest(plan_payload),
    )
