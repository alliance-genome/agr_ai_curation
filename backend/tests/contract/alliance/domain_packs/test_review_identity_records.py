"""Which staged Alliance phenotype records the review packet marks as identity records.

The packet marker and the save rule share one predicate (v0.11.6 H2). These are
the record shapes from the S06 and S07 dev runs, read through the real pack.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry  # noqa: E402

from src.lib.validation_history.assessments import identity_record, record_specs  # noqa: E402


@pytest.mark.parametrize(("object_type", "payload", "expected"), [
    # S06 rerun d9466d62: all 8 refused handles (D1, D13, D25, D34, D43, D52, D64, D76)
    # were subjects staged with only this note; the pack declares the type resolvable.
    ("PhenotypeSubject", {"resolution_note": (
        "Phenotype extraction did not stage the subject the paper names; "
        "phenotype_annotation_subject is absent.")}, True),
    # S07: a subject staged with its resolution state.
    ("PhenotypeSubject", {"taxon": None, "mention": "hoxa13a -/- , hoxa13b -/- , hoxd13a -/-",
                          "subject_type": "agm", "subject_label": None, "lookup_outcome": "not_validated",
                          "proposed_taxon": "NCBITaxon:7955", "resolution_state": "unresolved",
                          "subject_identifier": None, "validator_explanation": "Not validated yet."}, True),
    # S06 and S07: a ZFIN term no configured ontology mapping can look up.
    ("PhenotypeTerm", {"curie": None, "label": None, "mention": "sheath number per oligodendrocyte",
                       "lookup_outcome": "not_validated", "resolution_state": "unresolved",
                       "validator_explanation": "Not validated yet."}, True),
    # The empty reference placeholder: its type is declared resolvable too.
    ("Reference", {}, True),
    ("EvidenceQuote", {"evidence_record_id": "evidence-f30f4d9c924c2d4c"}, False),
    ("PhenotypeAnnotation", {"negated": False, "annotation_kind": "phenotype_assertion",
                             "rationale": "The Figure 1 legend identifies missing bifurcations."}, False),
])
def test_phenotype_record_shapes_are_identity_records_by_the_save_rule(object_type, payload, expected):
    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.phenotype")
    assert identity_record(payload, record_specs(pack, object_type)) is expected
