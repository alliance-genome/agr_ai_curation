import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError
import yaml

from src.lib.benchmarks.catalog import build_route_catalog
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import (
    BenchmarkModelCatalogEntry,
    BenchmarkRoute,
)
from src.lib.benchmarks.suites import (
    load_checked_in_suites,
    load_suite,
    resolve_suite,
    validate_suite,
)


ROOT = Path(__file__).resolve().parents[5] / "packages" / "alliance" / "benchmarks"
ALLIANCE_ROOT = ROOT.parent


def _route(model: str = "model-a", effort: str | None = "high") -> BenchmarkRoute:
    return BenchmarkRoute(provider="provider-a", model=model, reasoning_effort=effort)


def _catalog():
    return build_route_catalog(
        models=[
            BenchmarkModelCatalogEntry(
                provider="provider-a",
                model="model-a",
                reasoning_efforts=("low", "high"),
            ),
            BenchmarkModelCatalogEntry(
                provider="provider-a", model="model-b", reasoning_efforts=()
            ),
        ],
        supervisor_default=_route(),
        agent_defaults={"extractor": _route()},
        model_validator_defaults={"semantic-check": _route()},
        agent_targets={"extractor"},
        flow_agents={"Extraction Flow": ["extractor"]},
        flow_model_validators={"Extraction Flow": ["semantic-check"]},
    )


def _checked_in_catalog():
    models = [
        BenchmarkModelCatalogEntry(
            provider="openai",
            model=model,
            reasoning_efforts=("low", "medium", "high", "xhigh"),
        )
        for model in ("gpt-5.6-sol", "gpt-5.6-terra")
    ]
    models.extend(
        BenchmarkModelCatalogEntry(
            provider="openrouter", model=model, reasoning_efforts=()
        )
        for model in (
            "deepseek/deepseek-v4-pro-0813",
            "google/gemini-3.7-flash",
            "qwen/qwen3.8-27b",
        )
    )
    sol = BenchmarkRoute(
        provider="openai", model="gpt-5.6-sol", reasoning_effort="medium"
    )
    terra = BenchmarkRoute(
        provider="openai", model="gpt-5.6-terra", reasoning_effort="medium"
    )
    flow_recipe_path = ALLIANCE_ROOT / "config" / "flow_recipes.yaml"
    flow_recipe_data = yaml.safe_load(flow_recipe_path.read_text(encoding="utf-8"))
    gene_curation = next(
        recipe
        for recipe in flow_recipe_data["recipes"]
        if recipe["name"] == "Gene Curation"
    )
    flow_agents = [step["agent_id"] for step in gene_curation["steps"]]
    chat_output_definition = yaml.safe_load(
        (ALLIANCE_ROOT / "agents" / "chat_output" / "agent.yaml").read_text(
            encoding="utf-8"
        )
    )
    chat_output_alias = chat_output_definition["agent_id"]
    return build_route_catalog(
        models=models,
        supervisor_default=sol,
        agent_defaults={
            "pdf_extraction": sol,
            "gene_validation": terra,
            "chat_output": terra,
            "ontology_term_validation": terra,
        },
        model_validator_defaults={},
        agent_targets={"gene_validation", "ontology_term_validation"},
        flow_agents={"Gene Curation": flow_agents},
        flow_model_validators={},
        agent_aliases={chat_output_alias: "chat_output"},
    )


def _payload() -> dict:
    return {
        "schema_version": 2,
        "suite_id": "suite-1",
        "cases": [
            {
                "case_id": "case-1",
                "target": {"kind": "flow", "id": "Extraction Flow"},
                "input": {
                    "resolver": "checked_in_fixture",
                    "reference": "case-1.json",
                    "version": "v1",
                    "digest": "sha256:" + "b" * 64,
                },
            }
        ],
        "configurations": [
            {
                "configuration_id": "defaults",
                "routes": {},
            },
            {
                "configuration_id": "low-extractor",
                "routes": {
                    "agent:extractor": {
                        "provider": "provider-a",
                        "model": "model-a",
                        "reasoning_effort": "low",
                    }
                },
            },
        ],
        "repetitions": 2,
    }


def _resolve(payload: dict):
    return resolve_suite(
        validate_suite(payload),
        _catalog(),
        max_cases=50,
        max_configurations=10,
        max_repetitions=5,
        max_cells=250,
    )


def test_yaml_and_ad_hoc_json_resolve_to_identical_plan(tmp_path):
    payload = _payload()
    path = tmp_path / "suite.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")

    from_yaml = resolve_suite(
        load_suite(path),
        _catalog(),
        max_cases=50,
        max_configurations=10,
        max_repetitions=5,
        max_cells=250,
    )
    from_json = _resolve(json.loads(json.dumps(payload)))

    assert from_yaml == from_json
    assert len(from_yaml.cells) == 4
    assert from_yaml.plan_digest.startswith("sha256:")
    assert all(len(cell.routes) == 3 for cell in from_yaml.cells)
    assert from_yaml.configurations[0].routes["supervisor"] == _route()


def test_plan_digests_are_stable_and_change_with_execution_inputs():
    first = _resolve(_payload())
    second = _resolve(_payload())
    changed = _payload()
    changed["cases"][0]["input"]["version"] = "v2"
    third = _resolve(changed)

    assert first.plan_digest == second.plan_digest
    assert first.suite_digest == second.suite_digest
    assert first.plan_digest != third.plan_digest
    assert first.suite_digest != third.suite_digest


def test_resolved_plan_is_deeply_immutable():
    plan = _resolve(_payload())
    original_digest = plan.plan_digest

    with pytest.raises(ValidationError, match="frozen"):
        plan.cases[0].target.id = "changed"
    with pytest.raises(TypeError, match="does not support item assignment"):
        cast(Any, plan.configurations[0].routes)["supervisor"] = _route(
            model="model-b"
        )
    with pytest.raises(TypeError, match="does not support item assignment"):
        cast(Any, plan.cells[0].routes)["supervisor"] = _route(model="model-b")
    with pytest.raises(ValidationError, match="frozen"):
        plan.cells[0].routes["supervisor"].model = "changed"

    assert plan.plan_digest == original_digest


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["configurations"][0]["routes"].update(
                {"agent:unknown": {"provider": "provider-a", "model": "model-a"}}
            ),
            "not selected by any case target",
        ),
        (
            lambda payload: payload["configurations"][0]["routes"].update(
                {"supervisor": {"provider": "provider-a", "model": "unknown"}}
            ),
            "unknown provider/model",
        ),
        (
            lambda payload: payload["configurations"][0]["routes"].update(
                {
                    "supervisor": {
                        "provider": "provider-a",
                        "model": "model-b",
                        "reasoning_effort": "high",
                    }
                }
            ),
            "not supported",
        ),
    ],
)
def test_plan_rejects_invalid_slots_models_and_capabilities(mutate, message):
    payload = _payload()
    mutate(payload)

    with pytest.raises(BenchmarkCatalogError, match=message):
        _resolve(payload)


def test_plan_enforces_all_approved_limits():
    suite = validate_suite(_payload())
    for kwargs, message in (
        ({"max_cases": 0}, "positive"),
        ({"max_configurations": 1}, "configurations"),
        ({"max_repetitions": 1}, "repetitions"),
        ({"max_cells": 3}, "cells"),
    ):
        limits = {
            "max_cases": 50,
            "max_configurations": 10,
            "max_repetitions": 5,
            "max_cells": 250,
            **kwargs,
        }
        with pytest.raises((BenchmarkCatalogError, ValueError), match=message):
            resolve_suite(suite, _catalog(), **limits)


def test_every_checked_in_suite_is_v2_and_deterministic():
    suites = load_checked_in_suites(ROOT)

    assert {suite.suite_id for suite in suites} == {
        "flow-canary-gene-curation-v2",
        "isolated-gene-agent-v2",
        "isolated-ontology-agent-v2",
    }
    assert all(suite.schema_version == 2 for suite in suites)
    assert all(
        suite == load_suite(path)
        for suite, path in zip(
            suites, sorted((ROOT / "suites").glob("*.yaml")), strict=True
        )
    )
    for suite in suites:
        first = resolve_suite(
            suite,
            _checked_in_catalog(),
            max_cases=50,
            max_configurations=10,
            max_repetitions=5,
            max_cells=250,
        )
        second = resolve_suite(
            suite,
            _checked_in_catalog(),
            max_cases=50,
            max_configurations=10,
            max_repetitions=5,
            max_cells=250,
        )
        assert first == second
        assert first.plan_digest == second.plan_digest
        assert all(cell.routes for cell in first.cells)
        if suite.suite_id == "flow-canary-gene-curation-v2":
            expected_slots = {
                "supervisor",
                "agent:chat_output",
                "agent:gene_validation",
                "agent:pdf_extraction",
            }
            assert all(
                set(configuration.routes) == expected_slots
                for configuration in suite.configurations
            )
            assert all(set(cell.routes) == expected_slots for cell in first.cells)
