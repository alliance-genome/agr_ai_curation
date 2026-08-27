"""Opt-in live source contract for RGD mature-RNA product resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.tools.gene_product_resolution import (  # noqa: E402
    resolve_gene_product,
)
from agr_ai_curation_alliance.tools.go_annotations import validate_go_gene_id  # noqa: E402

LIVE_DB_ENV = "ALLIANCE_LIVE_DB_CONTRACT_TESTS"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(
    not _truthy(os.getenv(LIVE_DB_ENV)),
    reason=f"Set {LIVE_DB_ENV}=1 to run live curation source contracts",
)
def test_live_rat_mir_124_3p_mapping_is_complete_and_cardinality_driven():
    result = resolve_gene_product(
        "miR-124-3p",
        "NCBITaxon:10116",
        "RGD",
        "rno",
        use_cache=False,
    )

    assert result.identity_kind == "mature_product"
    assert result.mature_product is not None
    assert result.mature_product.rnacentral_id.startswith("RNAcentral:URS")
    assert result.mature_product.mirbase_id is not None
    assert result.mature_product.mirbase_id.startswith("miRBase:MIMAT")
    assert result.candidate_mappings
    assert all(
        validate_go_gene_id(candidate.gene_id) == candidate.gene_id
        for candidate in result.candidate_mappings
    )
    assert "RGD:miR-124" not in {
        candidate.gene_id for candidate in result.candidate_mappings
    }

    if len(result.candidate_mappings) == 1:
        assert result.status == "resolved"
        assert result.resolved_gene_id == result.candidate_mappings[0].gene_id
    else:
        assert result.status == "ambiguous"
        assert result.resolved_gene_id is None
