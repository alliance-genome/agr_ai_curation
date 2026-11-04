"""The supervisor judges a record against the rules its own extractor ran under."""
from agents import Agent, ModelSettings
import pytest

from src.lib.observability.cost_context import attach_agent_cost_identity
from src.lib.prompts.assembly import _make_layer
from src.lib.validation_history.store import HistoryScope, canonical
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.protocol import REVIEW_PROTOCOL
from src.lib.validation_review.reviewer import create_supervisor_phase


logged, traced = [], []


@pytest.fixture
def phase(monkeypatch):
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    logged.clear()
    monkeypatch.setattr("src.lib.openai_agents.langfuse_client.log_agent_config",
                        lambda **kwargs: logged.append(kwargs["metadata"]))
    monkeypatch.setattr("src.lib.openai_agents.extraction_trace_events.write_extraction_trace_event",
                        lambda **kwargs: traced.append(kwargs))
    monkeypatch.setenv("VALIDATION_REVIEW_MODEL", "gpt-6.1-sol")
    supervisor = Agent(name="Supervisor", model="gpt-6-astra", instructions="Policy",
                       model_settings=ModelSettings(parallel_tool_calls=False))
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    supervisor.validation_review_policy_layers = (_make_layer(
        layer_id="policy", kind="curator_overlay", title="Policy", content="Curator policy",
        provenance="test", editable=True, locked=False, source_ref="test"),)
    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "supervisor.validation_review")

    def create(receipt):
        return create_supervisor_phase(context, supervisor=supervisor, checkpoint_provider=lambda: None,
                                       authenticated_groups=[], extractor_receipt=receipt)
    return create


def receipt(key, rules):
    return {"agent_key": key, "execution_receipt": None, "contract": {"instructions": rules}}


def test_review_prompt_carries_the_extractor_rules_after_the_protocol(phase):
    instructions = phase(receipt("reagents", "Keep only reagents used in this study.")).instructions
    assert instructions.index(REVIEW_PROTOCOL.strip()) < instructions.index("Keep only reagents used")
    assert 'agent="reagents"' in instructions
    assert "do not perform their steps" in instructions


def test_each_agents_review_sees_only_that_agents_rules(phase):
    reagents = phase(receipt("reagents", "Keep only reagents used in this study.")).instructions
    alleles = phase(receipt("alleles", "Keep every allele with a phenotype.")).instructions
    assert "Keep only reagents used" in reagents and "Keep every allele" not in reagents
    assert "Keep every allele" in alleles and "Keep only reagents used" not in alleles


def test_cache_routing_does_not_change_between_agents_or_papers(phase):
    first = phase(receipt("reagents", "Keep only reagents used in this study."))
    second = phase(receipt("alleles", "Keep every allele with a phenotype."))
    assert first.instructions != second.instructions
    key = first.model_settings.extra_args["prompt_cache_key"]
    assert key and key == second.model_settings.extra_args["prompt_cache_key"]


def test_extraction_without_saved_rules_says_so(phase):
    instructions = phase({"agent_key": "older", "execution_receipt": None, "contract": None}).instructions
    assert "has no saved extractor instructions" in instructions


def test_every_review_records_which_rules_it_used(phase):
    saved = receipt("reagents", "Keep only reagents used in this study.")
    phase(saved)
    expected = {"extractor_agent": "reagents",
                "extractor_rules_sha256": canonical(saved["contract"])[0]}
    assert expected.items() <= logged[-1].items()
    assert traced[-1]["event_type"] == "runtime.review_extractor_rules"
    assert expected.items() <= traced[-1]["output_summary"].items()


def test_a_review_without_rules_is_visible_in_its_record(phase):
    phase({"agent_key": "older", "execution_receipt": None, "contract": None})
    assert logged[-1]["extractor_rules_sha256"] is None
    assert traced[-1]["output_summary"]["extractor_rules_sha256"] is None
