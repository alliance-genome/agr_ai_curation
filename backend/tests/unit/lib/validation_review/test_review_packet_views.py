"""Packet views show what the reviewer judges, each quote once, without borrowing fields."""
from src.lib.validation_review.reads import _PacketEvidence, _packet_answer


QUOTE = "Mutant rats showed reduced kidney mass (Fig. 2)."


def _attempt(evidence):
    return {"input": {"binding_id": "rgd_go_evidence_policy", "request": {
        "target": {"field_path": "go_annotations[0]"},
        "selected_inputs": {"evidence_quotes": evidence, "go_term": "GO:0001822"},
        "evidence": evidence}}}


def _evidence(**extra):
    return {"evidence_record_id": "evidence-1", "verified_quote": QUOTE, "section": "Results", "page": 3,
            "chunk_id": "chunk-1", **extra}


def test_rgd_shaped_policy_verdict_is_inline_in_the_answer():
    evidence = _PacketEvidence()
    record = _evidence()
    result = {
        "status": "unresolved", "explanation": "Evidence is from a cited earlier paper.",
        "resolved_values": {}, "resolved_objects": [], "candidates": [], "missing_expected_fields": [],
        "lookup_attempts": [{"provider": "db", "method": "lookup", "query": {}, "result_count": 0,
                             "outcome": "not_found"}],
        "decision": "curator_review_required",
        "policy_violations": ["Evidence comes from a cited paper, not this study."],
        "negation_supported": False, "qualifiers_supported": True,
        "ambiguity": "The figure may show a different strain.",
        "primary_evidence_record_ids": ["evidence-1"],
        "component_checks": [{"name": "aspect", "lookup_attempts": [{"query": {"x": 1}}], "status": "resolved"}],
        "proposed_qualifiers": [],
    }
    answer = _packet_answer(_attempt([record]), {"state": "completed", "result": result}, evidence)
    assert answer["additional_fields"] == {
        "decision": "curator_review_required",
        "policy_violations": ["Evidence comes from a cited paper, not this study."],
        "negation_supported": False, "qualifiers_supported": True,
        "ambiguity": "The figure may show a different strain.",
        "primary_evidence_record_ids": ["E1"],
        "component_checks": [{"name": "aspect", "status": "resolved"}],
    }
    assert "more_in_history" not in answer and "lookup_attempts" not in str(answer)
    assert answer["sent"] == {"evidence_quotes": ["E1"], "go_term": "GO:0001822"}


def test_same_quote_with_different_note_or_field_keeps_its_own_fields_but_not_the_quote_twice():
    evidence = _PacketEvidence()
    first = evidence.handle(_evidence(field_paths=["phenotype_terms"]))
    noted = evidence.handle(_evidence(evidence_record_id="evidence-2", agent_note="Only panel b.",
                                      field_paths=["phenotype_terms"]))
    other_field = evidence.handle(_evidence(field_paths=["condition_relations"]))
    same = evidence.handle(_evidence(evidence_record_id="evidence-3", field_paths=["phenotype_terms"],
                                     figure_reference="Fig. 2"))
    assert first == same and len({first, noted, other_field}) == 3
    assert evidence.entries[first] == {"verified_quote": QUOTE, "section": "Results", "page": 3,
                                       "figure_reference": "Fig. 2", "field_paths": ["phenotype_terms"]}
    assert evidence.entries[noted] == {"same_quote_as": first, "section": "Results", "page": 3,
                                       "agent_note": "Only panel b.", "field_paths": ["phenotype_terms"]}
    assert evidence.entries[other_field] == {"same_quote_as": first, "section": "Results", "page": 3,
                                             "field_paths": ["condition_relations"]}
    assert "agent_note" not in evidence.entries[first], "a note is never borrowed from another record"
    assert str(evidence.entries).count(QUOTE) == 1


def test_conflicting_locations_for_one_quote_stay_separate():
    evidence = _PacketEvidence()
    first = evidence.handle(_evidence())
    second = evidence.handle(_evidence(page=4))
    assert first != second
    assert evidence.entries[second] == {"same_quote_as": first, "section": "Results", "page": 4}
