import pytest
from pydantic import ValidationError

from src.lib.benchmarks.catalog import build_route_catalog
from src.lib.benchmarks.models import BenchmarkModelCatalogEntry, BenchmarkRoute


def _route(model: str = "model-a") -> BenchmarkRoute:
    return BenchmarkRoute(provider="provider-a", model=model, reasoning_effort="high")


def test_catalog_exposes_stable_model_backed_slots_only():
    catalog = build_route_catalog(
        models=[
            BenchmarkModelCatalogEntry(
                provider="provider-a",
                model="model-a",
                reasoning_efforts=("high",),
            )
        ],
        supervisor_default=_route(),
        agent_defaults={"extractor": _route()},
        model_validator_defaults={"semantic-check": _route()},
        agent_targets={"extractor"},
        flow_agents={"Extraction Flow": ["extractor"]},
        flow_model_validators={"Extraction Flow": ["semantic-check"]},
    )

    assert [slot.slot for slot in catalog.route_slots] == [
        "supervisor",
        "agent:extractor",
        "validator:semantic-check",
    ]
    flow = next(item for item in catalog.targets if item.target.kind == "flow")
    assert flow.route_slots == (
        "supervisor",
        "agent:extractor",
        "validator:semantic-check",
    )
    assert "validator:json-schema" not in flow.route_slots
    with pytest.raises(ValidationError, match="frozen"):
        flow.target.id = "changed"
    with pytest.raises(ValidationError, match="frozen"):
        catalog.route_slots[0].default_route.model = "changed"


def test_catalog_resolves_flow_agent_aliases_to_canonical_slots():
    catalog = build_route_catalog(
        models=[
            BenchmarkModelCatalogEntry(
                provider="provider-a",
                model="model-a",
                reasoning_efforts=("high",),
            )
        ],
        supervisor_default=_route(),
        agent_defaults={"chat_output": _route()},
        model_validator_defaults={},
        agent_targets=(),
        flow_agents={"Formatting Flow": ["chat_output_formatter"]},
        flow_model_validators={},
        agent_aliases={"chat_output_formatter": "chat_output"},
    )

    flow = next(item for item in catalog.targets if item.target.kind == "flow")
    assert flow.route_slots == ("supervisor", "agent:chat_output")


def test_catalog_rejects_flow_slot_without_checked_in_default():
    with pytest.raises(ValueError, match="without defaults"):
        build_route_catalog(
            models=[
                BenchmarkModelCatalogEntry(
                    provider="provider-a",
                    model="model-a",
                    reasoning_efforts=("high",),
                )
            ],
            supervisor_default=_route(),
            agent_defaults={},
            model_validator_defaults={},
            agent_targets=(),
            flow_agents={"Extraction Flow": ["missing"]},
            flow_model_validators={},
        )
