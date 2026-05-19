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


def test_tightened_trial_gate_requires_expected_validator_audit_events():
    corpus = _load_corpus_module()
    checks: list[dict] = []
    trial = corpus.TRIALS[0]

    payload = corpus.validate_tightened_trial_gate(
        trial=trial,
        flow_result={
            "events": [
                _validator_lookup_event("alliance_gene_reference_lookup"),
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
            flow_result={"events": []},
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
                    {"type": "SPECIALIST_TEXT_FALLBACK_SUCCESS", "details": {}},
                ]
            },
            checks=checks,
            allow_specialist_text_fallback=False,
        )

    assert checks[-1]["ok"] is False
    assert checks[-1]["payload"]["specialist_text_fallback_event_count"] == 1


def test_tightened_trial_gate_honors_cross_domain_minimum_expected_bindings():
    corpus = _load_corpus_module()
    checks: list[dict] = []
    trial = next(
        trial
        for trial in corpus.TRIALS
        if trial.trial_id == "cross_domain_zebrafish_segmentation_screen"
    )

    payload = corpus.validate_tightened_trial_gate(
        trial=trial,
        flow_result={
            "events": [
                _validator_lookup_event("chemical_condition.chebi_api_lookup"),
                _validator_lookup_event("phenotype_term_ontology_validator"),
            ]
        },
        checks=checks,
        allow_specialist_text_fallback=False,
    )

    assert payload["minimum_expected_validator_bindings"] == 2
    assert payload["missing_expected_validator_bindings"] == [
        "alliance_gene_reference_lookup"
    ]
    assert checks[-1]["ok"] is True
