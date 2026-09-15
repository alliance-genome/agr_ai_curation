"""Shipped supporting-evidence selectors preserve collections, not scalar guesses."""

from dataclasses import replace
from pathlib import Path
import sys

import pytest

ALLIANCE_PYTHON_SRC = Path(__file__).resolve().parents[5] / "packages/alliance/python/src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry  # noqa: E402
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidatorBindingMatch,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope


def _supporting_bindings():
    for pack in load_alliance_domain_pack_registry().packs_by_id.values():
        for binding in DomainPackValidationRegistry.from_domain_pack(pack).bindings:
            for name, selector in binding.input_fields.items():
                if selector.source == "evidence_record":
                    yield pytest.param(pack.pack_id, binding, name, selector,
                                       id=f"{pack.pack_id}/{binding.binding_id}/{name}")


@pytest.mark.parametrize("pack_id,binding,name,selector", list(_supporting_bindings()))
@pytest.mark.parametrize("count", [0, 1, 2])
def test_shipped_supporting_evidence_contract(pack_id, binding, name, selector, count):
    assert selector.output == "quote_bundle"
    assert selector.allow_multiple is True
    # Exercise each real selector independently of the binding's biological inputs.
    isolated = replace(binding, input_fields={name: selector}, raw={})
    records = [{
        "evidence_record_id": f"evidence-{i}",
        "verified_quote": ("The treatment increased expression." if i == 0
                           else "The treatment decreased expression."),
        "chunk_id": f"chunk-{i}", "section": f"Section {i}",
        "field_path": selector.field_path or "mention",
    } for i in range(count)]
    unrelated = {"evidence_record_id": "other-object", "verified_quote": "Unrelated quote."}
    wrong_field = {"evidence_record_id": "other-field", "verified_quote": "Different field.",
                   "field_path": "unrelated_field"}
    ids = [record["evidence_record_id"] for record in records]
    obj = CuratableObjectEnvelope(object_type="fixture", pending_ref_id="target",
                                  payload={}, evidence_record_ids=ids + ids[:1] + (
                                      ["other-field"] if selector.field_path else []))
    envelope = DomainEnvelope(envelope_id="test", domain_pack_id=pack_id,
                              extracted_objects=[obj],
                              metadata={"evidence_records": records + [unrelated, wrong_field]})
    result = build_domain_validation_request(ValidatorBindingMatch(
        binding=isolated, envelope=envelope, object_envelope=obj))
    if count == 0 and selector.required:
        assert result.request is None
        assert result.findings
    else:
        assert result.findings == ()
        assert result.request is not None
        # Both conflicting quotes survive, each paired with its own provenance;
        # selection itself neither chooses one nor resolves a biological identity.
        assert result.selected_inputs.get(name, []) == records


def test_all_shipped_supporting_evidence_datatypes_are_audited():
    assert {case.values[0] for case in _supporting_bindings()} == {
        "gene", "agr.alliance.allele", "agr.alliance.disease",
        "agr.alliance.phenotype", "agr.alliance.gene_expression", "agr.alliance.go",
    }
