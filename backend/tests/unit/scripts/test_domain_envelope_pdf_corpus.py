from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def _load_corpus_module():
    repo_root = Path(__file__).resolve().parents[4]
    smoke_path = repo_root / "scripts" / "testing" / "dev_release_smoke.py"
    smoke_spec = importlib.util.spec_from_file_location("dev_release_smoke", smoke_path)
    assert smoke_spec and smoke_spec.loader
    smoke = importlib.util.module_from_spec(smoke_spec)
    sys.modules[smoke_spec.name] = smoke
    smoke_spec.loader.exec_module(smoke)

    corpus_path = repo_root / "scripts" / "testing" / "domain_envelope_pdf_corpus.py"
    if not corpus_path.exists():
        pytest.skip("domain_envelope_pdf_corpus.py is not present in this test image")
    corpus_spec = importlib.util.spec_from_file_location(
        "domain_envelope_pdf_corpus", corpus_path
    )
    assert corpus_spec and corpus_spec.loader
    corpus = importlib.util.module_from_spec(corpus_spec)
    sys.modules[corpus_spec.name] = corpus
    corpus_spec.loader.exec_module(corpus)
    return corpus


def _validator_lookup_event(binding_id: str) -> dict:
    return {
        "type": "TOOL_START",
        "details": {
            "toolName": "domain_validator_lookup",
            "validatorBindingId": binding_id,
            "validatorResultStatus": "resolved",
        },
    }


def _tool_complete_event(tool_name: str) -> dict:
    return {"type": "TOOL_COMPLETE", "details": {"toolName": tool_name}}


def _builder_summary_event(
    *,
    stage_tool: str = "stage_gene_mention_evidence",
    finalize_tool: str = "finalize_gene_extraction",
    staged: int = 1,
    finalized: int | None = None,
    object_count: int | None = None,
    validator_target_count: int | None = None,
    finalization_called: bool = True,
    zero_status: str | None = "validator_jobs_executed",
) -> dict:
    finalized_count = staged if finalized is None else finalized
    return {
        "type": "SPECIALIST_SUMMARY",
        "details": {
            "builderEnabled": True,
            "builderStageTool": stage_tool,
            "builderFinalizeTool": finalize_tool,
            "builderStagedCount": staged,
            "builderFinalizedCount": finalized_count,
            "builderFinalizationCalled": finalization_called,
            "builderStagedEvidenceIds": [f"evidence-{index + 1}" for index in range(staged)],
            "builderFinalizedObjectCount": (
                finalized_count if object_count is None else object_count
            ),
            "builderValidatorTargetCount": (
                finalized_count
                if validator_target_count is None
                else validator_target_count
            ),
            "builderZeroValidatorJobsStatus": zero_status,
        },
    }


def _builder_events(
    *,
    stage_tool: str = "stage_gene_mention_evidence",
    finalize_tool: str = "finalize_gene_extraction",
    staged: int = 1,
    finalized: int | None = None,
    stage_calls: int | None = None,
    finalize_calls: int = 1,
    object_count: int | None = None,
    validator_target_count: int | None = None,
    finalization_called: bool = True,
    zero_status: str | None = "validator_jobs_executed",
) -> list[dict]:
    finalized_count = staged if finalized is None else finalized
    return [
        *[
            _tool_complete_event(stage_tool)
            for _ in range(staged if stage_calls is None else stage_calls)
        ],
        *[_tool_complete_event(finalize_tool) for _ in range(finalize_calls)],
        _builder_summary_event(
            stage_tool=stage_tool,
            finalize_tool=finalize_tool,
            staged=staged,
            finalized=finalized_count,
            object_count=object_count,
            validator_target_count=validator_target_count,
            finalization_called=finalization_called,
            zero_status=zero_status,
        ),
    ]


def _cross_domain_builder_events() -> list[dict]:
    return [
        *_builder_events(
            stage_tool="stage_phenotype_assertion_evidence",
            finalize_tool="finalize_phenotype_extraction",
        ),
        *_builder_events(
            stage_tool="stage_gene_mention_evidence",
            finalize_tool="finalize_gene_extraction",
        ),
    ]


def test_tightened_trial_gate_requires_expected_validator_audit_events():
    corpus = _load_corpus_module()
    checks: list[dict] = []
    trial = corpus.TRIALS[0]

    payload = corpus.validate_tightened_trial_gate(
        trial=trial,
        flow_result={
            "events": [
                _validator_lookup_event("alliance_gene_reference_lookup"),
                *_builder_events(),
            ]
        },
        checks=checks,
        allow_specialist_text_fallback=False,
    )

    assert payload["observed_validator_lookup_counts"] == {
        "alliance_gene_reference_lookup": 1
    }
    assert payload["missing_expected_validator_bindings"] == []
    assert checks[-1]["ok"] is True


def test_tightened_trial_gate_fails_when_validator_audit_is_missing():
    corpus = _load_corpus_module()
    checks: list[dict] = []

    with pytest.raises(corpus.smoke.SmokeFailure, match="missing validator audit"):
        corpus.validate_tightened_trial_gate(
            trial=corpus.TRIALS[0],
            flow_result={"events": [*_builder_events()]},
            checks=checks,
            allow_specialist_text_fallback=False,
        )

    assert checks[-1]["ok"] is False
    assert checks[-1]["payload"]["missing_expected_validator_bindings"] == [
        "alliance_gene_reference_lookup"
    ]


def test_tightened_trial_gate_fails_on_specialist_text_fallback():
    corpus = _load_corpus_module()
    checks: list[dict] = []

    with pytest.raises(corpus.smoke.SmokeFailure, match="specialist text fallback"):
        corpus.validate_tightened_trial_gate(
            trial=corpus.TRIALS[0],
            flow_result={
                "events": [
                    _validator_lookup_event("alliance_gene_reference_lookup"),
                    *_builder_events(),
                    {"type": "SPECIALIST_TEXT_FALLBACK_SUCCESS", "details": {}},
                ]
            },
            checks=checks,
            allow_specialist_text_fallback=False,
        )

    assert checks[-1]["ok"] is False
    assert checks[-1]["payload"]["specialist_text_fallback_event_count"] == 1


def test_tightened_trial_gate_requires_all_cross_domain_expected_bindings():
    corpus = _load_corpus_module()
    checks: list[dict] = []
    trial = next(
        trial
        for trial in corpus.TRIALS
        if trial.trial_id == "cross_domain_zebrafish_segmentation_screen"
    )

    with pytest.raises(corpus.smoke.SmokeFailure, match="missing validator audit"):
        corpus.validate_tightened_trial_gate(
            trial=trial,
            flow_result={
                "events": [
                    _validator_lookup_event("phenotype_term_ontology_validator"),
                    *_cross_domain_builder_events(),
                ]
            },
            checks=checks,
            allow_specialist_text_fallback=False,
        )

    assert checks[-1]["payload"]["minimum_expected_validator_bindings"] == 2
    assert checks[-1]["payload"]["missing_expected_validator_bindings"] == [
        "alliance_gene_reference_lookup"
    ]
    assert checks[-1]["ok"] is False


def test_tightened_trial_gate_requires_all_cross_domain_builder_finalizers():
    corpus = _load_corpus_module()
    checks: list[dict] = []
    trial = next(
        trial
        for trial in corpus.TRIALS
        if trial.trial_id == "cross_domain_zebrafish_segmentation_screen"
    )

    with pytest.raises(corpus.smoke.SmokeFailure, match="builder gate failed"):
        corpus.validate_tightened_trial_gate(
            trial=trial,
            flow_result={
                "events": [
                    _validator_lookup_event("phenotype_term_ontology_validator"),
                    _validator_lookup_event("alliance_gene_reference_lookup"),
                    *_builder_events(),
                ]
            },
            checks=checks,
            allow_specialist_text_fallback=False,
        )

    assert checks[-1]["payload"]["expected_builder_finalizations"] == 2
    assert checks[-1]["payload"]["builder_observations"]["finalize_tool_complete_count"] == 1
    assert checks[-1]["ok"] is False


def test_tightened_trial_gate_rejects_stage_finalized_count_mismatch():
    corpus = _load_corpus_module()
    checks: list[dict] = []

    with pytest.raises(corpus.smoke.SmokeFailure, match="builder gate failed"):
        corpus.validate_tightened_trial_gate(
            trial=corpus.TRIALS[0],
            flow_result={
                "events": [
                    _validator_lookup_event("alliance_gene_reference_lookup"),
                    *_builder_events(staged=2, finalized=1),
                ]
            },
            checks=checks,
            allow_specialist_text_fallback=False,
        )

    observations = checks[-1]["payload"]["builder_observations"]
    assert observations["builder_stage_finalized_count_match"] is False
    assert observations["builder_stage_tool_complete_count_match"] is False
    assert checks[-1]["ok"] is False


def test_tightened_trial_gate_allows_configured_zero_retained_builder_success():
    corpus = _load_corpus_module()
    checks: list[dict] = []
    trial = corpus.CorpusTrial(
        trial_id="gene_zero_retained",
        domain="gene",
        agent_ids=("gene_extractor",),
        title="Zero retained fixture",
        organism="Drosophila melanogaster",
        pmcid="PMC0",
        pmid=None,
        doi=None,
        pdf_url="https://example.org/fixture.pdf",
        prompt="Extract no genes from this fixture.",
        expected_validator_bindings=(),
        minimum_expected_validator_bindings=0,
        expected_builder_finalizations=1,
        minimum_builder_stage_count=0,
        minimum_builder_finalized_object_count=0,
        minimum_builder_validator_target_count=0,
        allow_zero_retained_builder_success=True,
    )

    payload = corpus.validate_tightened_trial_gate(
        trial=trial,
        flow_result={
            "events": [
                *_builder_events(
                    staged=0,
                    finalized=0,
                    object_count=0,
                    validator_target_count=0,
                    zero_status="empty_finalized_output",
                )
            ]
        },
        checks=checks,
        allow_specialist_text_fallback=False,
    )

    assert payload["builder_ok"] is True
    assert payload["builder_observations"]["builder_finalized_object_total"] == 0
    assert checks[-1]["ok"] is True


def test_build_trial_flow_uses_agent_specific_cross_domain_prompts():
    corpus = _load_corpus_module()
    trial = next(
        trial
        for trial in corpus.TRIALS
        if trial.trial_id == "cross_domain_zebrafish_segmentation_screen"
    )

    flow = corpus.build_trial_flow(trial)
    agent_nodes = [
        node for node in flow["nodes"] if node["type"] == "agent"
    ]

    assert [node["data"]["agent_id"] for node in agent_nodes] == [
        "phenotype_extractor",
        "gene_extractor",
    ]
    assert all(node["data"]["input_source"] == "custom" for node in agent_nodes)
    assert "mid-trunk myotome boundary" in agent_nodes[0]["data"]["custom_input"]
    assert "zebrafish her1" in agent_nodes[1]["data"]["custom_input"]
    assert "Do not extract chemicals or genes" in agent_nodes[0]["data"]["step_goal"]
    assert "Do not extract chemicals or phenotype statements" in agent_nodes[1]["data"]["step_goal"]


def test_flow_summary_keeps_domain_validator_lookup_events():
    corpus = _load_corpus_module()

    summary = corpus._summarize_flow_events(
        {
            "events": [
                _validator_lookup_event("alliance_gene_reference_lookup"),
                {"type": "TEXT_MESSAGE_CONTENT", "content": "done"},
            ],
            "event_types": ["TOOL_START", "TEXT_MESSAGE_CONTENT"],
            "flow_run_id": "flow-1",
            "total_evidence_records": 1,
        }
    )

    assert summary["domain_events"] == [
        _validator_lookup_event("alliance_gene_reference_lookup")
    ]
